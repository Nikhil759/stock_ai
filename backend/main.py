"""FastAPI server — multi-bot deployments with allocation pools."""

from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import bot
import database as db
import eod
from screener import screen
from strategies import STRATEGY_NAMES, VALID_STRATEGIES, get_strategy, list_strategies
from workspace import is_valid_workspace_id, normalize_workspace_id

ROOT = Path(__file__).resolve().parent.parent
UI_FILE = ROOT / "Trading Bot.dc.html"

load_dotenv(ROOT / ".env")

app = FastAPI(title="Wolf Capital", version="0.4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


class ScreenRequest(BaseModel):
    strategy: str = Field(..., pattern="^(value|winners|box|dip)$")
    allocation: int = Field(..., ge=10000, le=1000000)


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
    from fund_scheduler import start_fund_scheduler

    setup_app_logging(verbose=True)
    db.init_db()
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


def _get_bot_or_404(bot_id: int, workspace_id: str) -> dict:
    b = db.get_bot(bot_id, workspace_id=workspace_id)
    if not b:
        raise HTTPException(status_code=404, detail="Wolf not found")
    return b


@app.get("/api/health")
def health():
    return {"status": "ok"}


# --- Bots ---

@app.get("/api/bots")
def list_bots(include_terminated: bool = False, ws: str = Depends(require_workspace)):
    return {
        "bots": [_bot_response(b) for b in db.list_bots(ws, include_terminated)],
        "workspaceId": ws,
    }


@app.get("/api/bots/{bot_id}")
def get_bot(bot_id: int, ws: str = Depends(require_workspace)):
    return _bot_response(_get_bot_or_404(bot_id, ws))


@app.post("/api/bots/deploy")
def deploy_bot(req: DeployRequest, ws: str = Depends(require_workspace)):
    if req.strategy not in VALID_STRATEGIES:
        raise HTTPException(status_code=400, detail="Unknown strategy")

    deployed = db.deploy_bot(
        strategy=req.strategy,
        allocation=req.allocation,
        workspace_id=ws,
        mode=req.mode,
        level=req.level,
        auto_threshold=req.auto_threshold,
        max_daily_loss_pct=req.max_daily_loss_pct,
        max_deployed_pct=req.max_deployed_pct,
        max_per_stock_pct=req.max_per_stock_pct,
        stop_loss_pct=req.stop_loss_pct,
        name=req.name,
    )
    bot_id = deployed["id"]
    screen_result = None

    if req.run_screen:
        try:
            result = screen(req.strategy, req.allocation, deployed)
            if result.get("supported"):
                setup_msg = (
                    f"Wolf #{bot_id} created · ₹{req.allocation:,} pool · "
                    f"{req.mode} mode · {STRATEGY_NAMES.get(req.strategy, req.strategy)}"
                )
                result["reasoningLog"] = [
                    {"phase": "setup", "message": setup_msg},
                    *(result.get("reasoningLog") or []),
                ]
                bot_result = bot.process_screen_results(bot_id, result.get("candidates", []), deployed)
                result["botAction"] = bot_result
                pipeline_payload = result.get("pipelinePayload")
                if pipeline_payload:
                    pipeline_payload["botId"] = bot_id
                    db.save_birth_intention(bot_id, pipeline_payload)
                    from fund_manager.intentions import write_birth_intention_file
                    write_birth_intention_file(bot_id, pipeline_payload)
            screen_result = result
        except Exception as exc:
            screen_result = {
                "strategy": req.strategy,
                "supported": False,
                "pipeline": "dossier",
                "message": f"Screening failed: {exc}",
            }

    return {"bot": _bot_response(db.get_bot(bot_id, ws)), "screen": screen_result, "workspaceId": ws}


@app.post("/api/bots/{bot_id}/pause")
def pause_bot(bot_id: int, ws: str = Depends(require_workspace)):
    _get_bot_or_404(bot_id, ws)
    b = db.set_bot_status(bot_id, "paused")
    if not b:
        raise HTTPException(status_code=404, detail="Wolf not found")
    return _bot_response(b)


@app.post("/api/bots/{bot_id}/resume")
def resume_bot(bot_id: int, ws: str = Depends(require_workspace)):
    _get_bot_or_404(bot_id, ws)
    b = db.set_bot_status(bot_id, "running")
    if not b:
        raise HTTPException(status_code=404, detail="Wolf not found")
    return _bot_response(b)


@app.post("/api/bots/{bot_id}/terminate")
def terminate_bot(bot_id: int, ws: str = Depends(require_workspace)):
    _get_bot_or_404(bot_id, ws)
    b = db.set_bot_status(bot_id, "terminated")
    if not b:
        raise HTTPException(status_code=404, detail="Wolf not found")
    return _bot_response(b)


@app.put("/api/bots/{bot_id}")
def update_bot(bot_id: int, body: BotUpdate, ws: str = Depends(require_workspace)):
    _get_bot_or_404(bot_id, ws)
    updates = body.model_dump(exclude_none=True)
    b = db.update_bot(bot_id, **updates)
    if not b:
        raise HTTPException(status_code=404, detail="Wolf not found")
    if updates:
        db.log_action(bot_id, "config_updated", ", ".join(f"{k}={v}" for k, v in updates.items()))
    return _bot_response(b)


@app.post("/api/bots/{bot_id}/eod")
def run_eod(bot_id: int, ws: str = Depends(require_workspace)):
    _get_bot_or_404(bot_id, ws)
    return eod.run_eod(bot_id)


@app.post("/api/bots/{bot_id}/refresh")
def refresh_prices(bot_id: int, ws: str = Depends(require_workspace)):
    _get_bot_or_404(bot_id, ws)
    return eod.refresh_prices(bot_id)


@app.post("/api/bots/{bot_id}/screen")
def run_screen(bot_id: int, ws: str = Depends(require_workspace)):
    b = _get_bot_or_404(bot_id, ws)
    if b["status"] == "terminated":
        raise HTTPException(status_code=400, detail="Wolf is terminated")
    try:
        cash = int(b["availableCash"])
        result = screen(b["strategy"], max(cash, b["allocation"]), b)
        if result.get("supported"):
            bot_result = bot.process_screen_results(bot_id, result.get("candidates", []), b)
            result["botAction"] = bot_result
        result["bot"] = _bot_response(db.get_bot(bot_id, ws))
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/bots/{bot_id}/trades")
def list_trades(bot_id: int, ws: str = Depends(require_workspace)):
    _get_bot_or_404(bot_id, ws)
    return {"trades": db.get_trades(bot_id)}


@app.get("/api/bots/{bot_id}/trades/{trade_id}/report")
def trade_pick_report(bot_id: int, trade_id: int, ws: str = Depends(require_workspace)):
    from pick_report import rebuild_pick_report_for_trade

    bot = _get_bot_or_404(bot_id, ws)
    trade = db.get_trade(trade_id, bot_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    report = rebuild_pick_report_for_trade(trade, bot)
    db.update_trade_pick_report(trade_id, report)
    return {"pickReport": report, "tradeId": trade_id}


@app.get("/api/bots/{bot_id}/pending")
def list_pending(bot_id: int, ws: str = Depends(require_workspace)):
    _get_bot_or_404(bot_id, ws)
    return {"pending": db.get_pending(bot_id)}


@app.post("/api/bots/{bot_id}/pending/{pending_id}/resolve")
def resolve_pending(bot_id: int, pending_id: int, body: ApproveRequest, ws: str = Depends(require_workspace)):
    _get_bot_or_404(bot_id, ws)
    result = db.resolve_pending(bot_id, pending_id, body.approve)
    if not result:
        raise HTTPException(status_code=404, detail="Pending trade not found")
    return result


@app.get("/api/bots/{bot_id}/log")
def action_log(bot_id: int, limit: int = 30, ws: str = Depends(require_workspace)):
    _get_bot_or_404(bot_id, ws)
    return {"log": db.get_action_log(bot_id, limit)}


@app.get("/api/bots/{bot_id}/daily-note")
def bot_daily_note(bot_id: int, note_date: str | None = None, ws: str = Depends(require_workspace)):
    _get_bot_or_404(bot_id, ws)
    note = db.get_daily_note(bot_id, note_date)
    if not note:
        return {"note": None, "message": "No fund manager note for this date."}
    return note


@app.post("/api/bots/{bot_id}/trades/manual")
def manual_trade(bot_id: int, body: ManualTradeRequest, ws: str = Depends(require_workspace)):
    b = _get_bot_or_404(bot_id, ws)
    candidate = {
        "ticker": body.ticker,
        "name": body.name,
        "sector": body.sector,
        "buyPrice": body.buy_price,
        "sellPrice": body.sell_price,
        "shares": body.qty,
        "cost": body.qty * body.buy_price,
        "canLog": True,
    }
    try:
        trade = bot.manual_log_trade(bot_id, candidate, b)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"trade": trade, "bot": _bot_response(db.get_bot(bot_id, ws))}


@app.patch("/api/bots/{bot_id}/trades/{trade_id}/target")
def update_target(bot_id: int, trade_id: int, body: TargetUpdateRequest, ws: str = Depends(require_workspace)):
    _get_bot_or_404(bot_id, ws)
    try:
        trade = db.update_trade_target(trade_id, body.target, body.reason or "Wolf updated sell target.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return {"trade": trade}


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


# --- Legacy routes (redirect to first bot if exists) ---

@app.get("/api/config")
def legacy_config(ws: str = Depends(require_workspace)):
    bots = db.list_bots(ws)
    if not bots:
        return {"mode": "advisory", "budget": 10000, "paused": False, "behaviorSummary": "Deploy a Wolf to get started."}
    return _bot_response(bots[0])


@app.get("/")
def index():
    return RedirectResponse("/app")


@app.get("/app")
def app_page():
    if not UI_FILE.exists():
        raise HTTPException(status_code=404, detail="UI file not found")
    return FileResponse(UI_FILE, media_type="text/html")


app.mount("/", StaticFiles(directory=str(ROOT), html=False), name="static")
