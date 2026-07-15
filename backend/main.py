"""FastAPI server — Wolf Capital (Supabase-backed /api/bots/*)."""

from pathlib import Path
from typing import Literal
from uuid import UUID
import logging
import os
import sys
from datetime import date

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import bot
import database as db
from strategies import STRATEGY_NAMES, VALID_STRATEGIES, get_strategy, list_strategies
from workspace import is_valid_workspace_id, normalize_workspace_id

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.deploy_wolf import (
    build_deploy_screen_response,
    deploy_new_wolf,
    guardrails_from_deploy_request,
    resolve_deploy_user_id,
)
import wolf_api
from db import repository as repo
from dashboard.auth_router import session_user_id

UI_FILE = ROOT / "Trading Bot.dc.html"
log = logging.getLogger(__name__)

load_dotenv(ROOT / ".env")

app = FastAPI(title="Wolf Capital", version="0.5.0")

from dashboard.auth_router import (
    allowed_origins,
    install_session_middleware,
    router as health_router,
)

install_session_middleware(app)
app.include_router(health_router)

_cors_origins = allowed_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi import Request as _Request
from dashboard.auth_router import api_ops_me as _api_ops_me


@app.get("/api/ops/me")
async def ops_me_alias(request: _Request):
    return await _api_ops_me(request)


class DeployRequest(BaseModel):
    strategy: str = Field(..., pattern="^(value|winners|box|dip)$")
    allocation: int = Field(..., ge=10000, le=1000000)
    mode: Literal["advisory", "autonomous"] = "advisory"
    level: Literal["A", "B", "C"] = "A"
    auto_threshold: int = Field(2000, ge=500, le=500000)
    max_daily_loss_pct: float = Field(5, ge=1, le=50)
    max_deployed_pct: float = Field(100, ge=10, le=100)
    max_per_stock_pct: float = Field(40, ge=5, le=100)
    stop_loss_pct: float = Field(15, ge=5, le=50)
    name: str | None = None
    run_screen: bool = True


class BotUpdate(BaseModel):
    mode: Literal["advisory", "autonomous"] | None = None
    level: Literal["A", "B", "C"] | None = None
    auto_threshold: int | None = Field(None, ge=500, le=500000)
    max_daily_loss_pct: float | None = Field(None, ge=1, le=50)
    max_deployed_pct: float | None = Field(None, ge=10, le=100)
    max_per_stock_pct: float | None = Field(None, ge=5, le=100)
    name: str | None = None


class ManualTradeRequest(BaseModel):
    ticker: str
    name: str | None = None
    sector: str | None = None
    qty: int = Field(..., ge=1)
    buy_price: float = Field(..., gt=0)
    sell_price: float = Field(..., gt=0)


class ApproveRequest(BaseModel):
    approve: bool


class TargetUpdateRequest(BaseModel):
    target: float = Field(..., gt=0)
    reason: str = ""


@app.on_event("startup")
def startup():
    from logging_setup import setup_app_logging

    setup_app_logging(verbose=True)
    if os.getenv("WOLF_ENABLE_SQLITE_CRON", "").strip().lower() in ("1", "true", "yes"):
        db.init_db()
        from fund_scheduler import start_fund_scheduler

        start_fund_scheduler()


def _bot_response(b: dict) -> dict:
    cfg = bot.bot_config(b)
    return {
        **b,
        "behaviorSummary": bot.behavior_summary(cfg),
        "stopLossNote": f"{b['stop_loss_pct']}% below buy price (automatic on every trade)",
    }


def require_workspace(x_workspace_id: str = Header(..., alias="X-Workspace-Id")) -> str:
    ws = normalize_workspace_id(x_workspace_id)
    if not is_valid_workspace_id(ws):
        raise HTTPException(status_code=400, detail="Invalid or missing workspace id")
    return ws


def _resolve_user(
    request: Request,
    x_user_id: str | None = None,
) -> UUID | None:
    uid = session_user_id(request)
    if uid:
        return uid
    return resolve_deploy_user_id(x_user_id)


def _require_user(request: Request, x_user_id: str | None = None) -> UUID:
    uid = _resolve_user(request, x_user_id)
    if not uid:
        raise HTTPException(
            status_code=401,
            detail="Log in required — use Google sign-in before deploying a Wolf.",
        )
    return uid


def _get_wolf_or_404(wolf_id: str, user_id: UUID) -> dict:
    b = wolf_api.get_bot_for_user(user_id, wolf_id)
    if not b:
        raise HTTPException(status_code=404, detail="Wolf not found")
    return b


@app.get("/api/health")
def health():
    return {"status": "ok"}


# --- Bots (Supabase) ---

@app.get("/api/bots")
def list_bots(
    request: Request,
    include_terminated: bool = False,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    user_id = _resolve_user(request, x_user_id)
    if not user_id:
        return {"bots": [], "workspaceId": ws}
    bots = wolf_api.list_bots_for_user(
        user_id, include_terminated=include_terminated
    )
    return {
        "bots": [_bot_response(b) for b in bots],
        "workspaceId": ws,
    }


@app.get("/api/bots/{wolf_id}")
def get_bot(
    wolf_id: str,
    request: Request,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    user_id = _require_user(request, x_user_id)
    return _bot_response(_get_wolf_or_404(wolf_id, user_id))


@app.post("/api/bots/deploy")
def deploy_bot(
    req: DeployRequest,
    request: Request,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    if req.strategy not in VALID_STRATEGIES:
        raise HTTPException(status_code=400, detail="Unknown strategy")

    user_id = _require_user(request, x_user_id)
    screen_result = None

    try:
        guardrails = guardrails_from_deploy_request(
            stop_loss_pct=req.stop_loss_pct,
            max_daily_loss_pct=req.max_daily_loss_pct,
            max_deployed_pct=req.max_deployed_pct,
            max_per_stock_pct=req.max_per_stock_pct,
        )
        wolf_result = deploy_new_wolf(
            user_id=user_id,
            strategy=req.strategy,
            budget=req.allocation,
            guardrails=guardrails,
            wolf_name=req.name,
        )
        screen_result = build_deploy_screen_response(
            wolf_result,
            strategy=req.strategy,
            allocation=req.allocation,
        )
        bot_row = wolf_api.get_bot_for_user(user_id, wolf_result["wolf_id"])
    except Exception as exc:
        log.exception("Supabase deploy failed")
        raise HTTPException(
            status_code=500,
            detail=f"Deploy failed: {exc}",
        ) from exc

    return {
        "bot": _bot_response(bot_row),
        "screen": screen_result,
        "workspaceId": ws,
        "wolf": wolf_result["wolf"],
    }


@app.post("/api/bots/{wolf_id}/pause")
def pause_bot(
    wolf_id: str,
    request: Request,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    user_id = _require_user(request, x_user_id)
    _get_wolf_or_404(wolf_id, user_id)
    repo.set_wolf_status(wolf_id, "paused")
    return _bot_response(_get_wolf_or_404(wolf_id, user_id))


@app.post("/api/bots/{wolf_id}/resume")
def resume_bot(
    wolf_id: str,
    request: Request,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    user_id = _require_user(request, x_user_id)
    _get_wolf_or_404(wolf_id, user_id)
    repo.set_wolf_status(wolf_id, "active")
    return _bot_response(_get_wolf_or_404(wolf_id, user_id))


@app.post("/api/bots/{wolf_id}/terminate")
def terminate_bot(
    wolf_id: str,
    request: Request,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    user_id = _require_user(request, x_user_id)
    _get_wolf_or_404(wolf_id, user_id)
    repo.set_wolf_status(wolf_id, "closed")
    return _bot_response(_get_wolf_or_404(wolf_id, user_id))


@app.put("/api/bots/{wolf_id}")
def update_bot(
    wolf_id: str,
    body: BotUpdate,
    request: Request,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    user_id = _require_user(request, x_user_id)
    _get_wolf_or_404(wolf_id, user_id)
    # Name/guardrail updates deferred — autonomous wolves use deploy-time guardrails.
    return _bot_response(_get_wolf_or_404(wolf_id, user_id))


@app.post("/api/bots/{wolf_id}/eod")
def run_eod(
    wolf_id: str,
    request: Request,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    _require_user(request, x_user_id)
    raise HTTPException(
        status_code=501,
        detail="EOD not yet wired to Supabase — coming in daily cron (Part 4).",
    )


@app.post("/api/bots/{wolf_id}/refresh")
def refresh_prices(
    wolf_id: str,
    request: Request,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    user_id = _require_user(request, x_user_id)
    _get_wolf_or_404(wolf_id, user_id)
    trades = wolf_api.holdings_to_trades(wolf_id)
    updated = [
        {"ticker": t["ticker"], "ltp": t["ltp"]}
        for t in trades
        if t.get("status") == "open"
    ]
    return {"updated": updated, "count": len(updated)}


@app.post("/api/bots/{wolf_id}/screen")
def run_screen(
    wolf_id: str,
    request: Request,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    user_id = _require_user(request, x_user_id)
    b = _get_wolf_or_404(wolf_id, user_id)
    if b["terminated"]:
        raise HTTPException(status_code=400, detail="Wolf is terminated")
    raise HTTPException(
        status_code=501,
        detail="Re-screen is not available post-deploy — daily cron handles reviews.",
    )


@app.get("/api/bots/{wolf_id}/trades")
def list_trades(
    wolf_id: str,
    request: Request,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    user_id = _require_user(request, x_user_id)
    _get_wolf_or_404(wolf_id, user_id)
    return {"trades": wolf_api.holdings_to_trades(wolf_id)}


@app.get("/api/bots/{wolf_id}/trades/{trade_id}/report")
def trade_pick_report(
    wolf_id: str,
    trade_id: int,
    request: Request,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    user_id = _require_user(request, x_user_id)
    b = _get_wolf_or_404(wolf_id, user_id)
    trades = wolf_api.holdings_to_trades(wolf_id)
    trade = next((t for t in trades if t.get("id") == trade_id), None)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    sym = trade.get("ticker", "")
    intents = repo.list_intents_for_wolf(wolf_id, limit=20)
    rationale = next(
        (i.get("rationale") for i in intents if i.get("symbol") == sym),
        f"Birth deploy position in {sym}.",
    )
    report = {
        "ticker": sym,
        "summary": rationale,
        "sections": [{"title": "Why this stock", "body": rationale}],
        "tradePlan": [
            {"label": "Entry price", "value": f"₹{trade['entry']:,.2f}"},
            {"label": "Current price", "value": f"₹{trade['ltp']:,.2f}"},
            {"label": "Target", "value": f"₹{trade.get('target', 0):,.0f}"},
        ],
    }
    return {"pickReport": report, "tradeId": trade_id}


@app.get("/api/bots/{wolf_id}/pending")
def list_pending(
    wolf_id: str,
    request: Request,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    user_id = _require_user(request, x_user_id)
    _get_wolf_or_404(wolf_id, user_id)
    return {"pending": []}


@app.post("/api/bots/{wolf_id}/pending/{pending_id}/resolve")
def resolve_pending(
    wolf_id: str,
    pending_id: int,
    body: ApproveRequest,
    request: Request,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    _require_user(request, x_user_id)
    raise HTTPException(status_code=501, detail="Advisory pending trades are not enabled.")


@app.get("/api/bots/{wolf_id}/log")
def action_log(
    wolf_id: str,
    request: Request,
    limit: int = 30,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    user_id = _require_user(request, x_user_id)
    _get_wolf_or_404(wolf_id, user_id)
    return {"log": wolf_api.intents_to_action_log(wolf_id, limit=limit)}


@app.get("/api/bots/{wolf_id}/daily-note")
def bot_daily_note(
    wolf_id: str,
    request: Request,
    note_date: str | None = None,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    user_id = _require_user(request, x_user_id)
    wolf = repo.get_wolf_for_user(wolf_id, user_id)
    if not wolf:
        raise HTTPException(status_code=404, detail="Wolf not found")
    birth = wolf.get("birth_intent")
    text = ""
    if isinstance(birth, dict):
        text = str(birth.get("text") or birth.get("body") or "")
    elif isinstance(birth, str):
        text = birth
    if not text:
        return {
            "botId": wolf_id,
            "date": note_date or date.today().isoformat(),
            "note": None,
            "updatedAt": None,
            "message": "No daily note yet — deploy or wait for daily review.",
        }
    updated = wolf.get("updated_at") or wolf.get("created_at")
    updated_at = (
        updated.isoformat()
        if hasattr(updated, "isoformat")
        else str(updated) if updated else None
    )
    return {
        "botId": wolf_id,
        "date": note_date or date.today().isoformat(),
        "note": text,
        "updatedAt": updated_at,
    }


@app.post("/api/bots/{wolf_id}/trades/manual")
def manual_trade(
    wolf_id: str,
    body: ManualTradeRequest,
    request: Request,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    _require_user(request, x_user_id)
    raise HTTPException(
        status_code=501,
        detail="Manual trades not yet wired to Supabase executor.",
    )


@app.patch("/api/bots/{wolf_id}/trades/{trade_id}/target")
def update_target(
    wolf_id: str,
    trade_id: int,
    body: TargetUpdateRequest,
    request: Request,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    _require_user(request, x_user_id)
    raise HTTPException(
        status_code=501,
        detail="Target updates not yet wired to Supabase holdings.",
    )


# --- Strategies ---

@app.get("/api/strategies")
def strategies_list():
    return {"strategies": list_strategies()}


@app.get("/api/strategies/{strategy_id}")
def strategy_detail(strategy_id: str):
    if strategy_id not in VALID_STRATEGIES:
        raise HTTPException(status_code=404, detail="Strategy not found")
    meta = get_strategy(strategy_id)
    return {
        "id": meta["id"],
        "name": meta["name"],
        "horizon": meta["horizon"],
        "analysisType": meta["analysisType"],
        "implemented": meta["implemented"],
        "knowledgeFile": meta["knowledgeFile"],
        "knowledge": meta["knowledge"],
    }


# --- Legacy routes ---

@app.get("/api/config")
def legacy_config(
    request: Request,
    ws: str = Depends(require_workspace),
    x_user_id: str | None = Header(None, alias="X-User-Id"),
):
    user_id = _resolve_user(request, x_user_id)
    if not user_id:
        return {
            "mode": "autonomous",
            "budget": 10000,
            "paused": False,
            "behaviorSummary": "Log in and deploy a Wolf to get started.",
        }
    bots = wolf_api.list_bots_for_user(user_id)
    if not bots:
        return {
            "mode": "autonomous",
            "budget": 10000,
            "paused": False,
            "behaviorSummary": "Deploy a Wolf to get started.",
        }
    return _bot_response(bots[0])


@app.get("/")
def index(code: str | None = None):
    if code:
        return RedirectResponse(f"/health/auth/callback?code={code}", status_code=303)
    return RedirectResponse("/app")


@app.get("/app")
def app_page(code: str | None = None):
    if code:
        return RedirectResponse(f"/health/auth/callback?code={code}", status_code=303)
    if not UI_FILE.exists():
        raise HTTPException(status_code=404, detail="UI file not found")
    return FileResponse(UI_FILE, media_type="text/html")


app.mount("/", StaticFiles(directory=str(ROOT), html=False), name="static")
