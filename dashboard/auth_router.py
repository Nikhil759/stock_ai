"""
Phase F — FastAPI health dashboard (HTML) with Supabase Google OAuth (PKCE).

No Streamlit. Mounted under /health on the main FastAPI app.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import base64
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from urllib.parse import urlencode, urlparse

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

_REPO = Path(__file__).resolve().parents[1]
load_dotenv(_REPO / ".env")

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

router = APIRouter(tags=["health-dashboard"])


def supabase_url() -> str:
    url = (
        os.getenv("SUPABASE_URL")
        or os.getenv("SUPABASE_PROJECT_URL")
        or ""
    ).strip()
    if not url:
        raise RuntimeError("SUPABASE_URL or SUPABASE_PROJECT_URL must be set")
    return url.rstrip("/")


def supabase_anon_key() -> str:
    key = (os.getenv("SUPABASE_ANON_KEY") or "").strip()
    if not key:
        raise RuntimeError("SUPABASE_ANON_KEY must be set")
    return key


def authorized_email() -> str:
    return (os.getenv("AUTHORIZED_EMAIL") or "").strip().lower()


def _normalize_origin(raw: str) -> str:
    val = raw.strip().rstrip("/")
    if not val:
        return ""
    if not val.startswith(("http://", "https://")):
        val = "https://" + val
    return val


def redirect_url() -> str:
    explicit = (os.getenv("APP_REDIRECT_URL") or "").strip()
    if explicit:
        return explicit
    frontend = _normalize_origin(os.getenv("FRONTEND_URL", ""))
    if frontend:
        return f"{frontend}/health/auth/callback"
    return "http://127.0.0.1:8000/health/auth/callback"


_PKCE_COOKIE = "wc_pkce_verifier"
_PKCE_MAX_AGE = 600  # seconds — OAuth round-trip + PWA handoff
_SESSION_MAX_AGE = int(os.getenv("DASHBOARD_SESSION_MAX_AGE", str(60 * 60 * 24 * 30)))


def _is_prod() -> bool:
    return bool(os.getenv("RAILWAY_ENVIRONMENT"))


def _cookie_secure() -> bool:
    return _is_prod() or os.getenv("DASHBOARD_COOKIE_SECURE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _cookie_domain() -> str | None:
    """Share cookies across apex + www (e.g. .wolfcapital.pro)."""
    frontend = _normalize_origin(os.getenv("FRONTEND_URL", ""))
    if not frontend:
        return None
    host = (urlparse(frontend).hostname or "").lower()
    if not host or host in ("localhost", "127.0.0.1"):
        return None
    parts = host.split(".")
    if len(parts) >= 2:
        return "." + ".".join(parts[-2:])
    return None


def _session_same_site() -> str:
    """Vercel proxies /health and /api — auth cookies are first-party; Lax works in PWA."""
    override = (os.getenv("DASHBOARD_COOKIE_SAMESITE") or "").strip().lower()
    if override in ("lax", "strict", "none"):
        return override
    return "lax"


def _set_pkce_cookie(response: RedirectResponse, verifier: str) -> None:
    response.set_cookie(
        key=_PKCE_COOKIE,
        value=verifier,
        max_age=_PKCE_MAX_AGE,
        httponly=True,
        secure=_cookie_secure(),
        samesite=_session_same_site(),
        path="/",
        domain=_cookie_domain(),
    )


def _cookie_delete_kwargs() -> dict[str, Any]:
    return {
        "path": "/",
        "secure": _cookie_secure(),
        "httponly": True,
        "samesite": _session_same_site(),
    }


def _delete_cookie_variants(response: RedirectResponse, key: str) -> None:
    """Delete host-only and parent-domain cookies (legacy + current auth cookies)."""
    kwargs = _cookie_delete_kwargs()
    response.delete_cookie(key=key, **kwargs)
    domain = _cookie_domain()
    if domain:
        response.delete_cookie(key=key, domain=domain, **kwargs)


def _clear_pkce_cookie(response: RedirectResponse) -> None:
    _delete_cookie_variants(response, _PKCE_COOKIE)


def _clear_session_cookies(response: RedirectResponse) -> None:
    _delete_cookie_variants(response, "session")


def _clear_auth_cookies(response: RedirectResponse) -> None:
    _clear_session_cookies(response)
    _clear_pkce_cookie(response)


def _read_pkce_verifier(request: Request) -> str | None:
    raw = request.cookies.get(_PKCE_COOKIE) or request.session.get("pkce_verifier")
    return (raw or "").strip() or None


def _client_host(request: Request) -> str:
    """Browser-facing host (Vercel/Railway set X-Forwarded-Host on proxied requests)."""
    forwarded = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    if forwarded:
        return forwarded.split(":")[0].lower()
    return (request.url.hostname or "").lower()


def _is_upstream_proxied(request: Request) -> bool:
    """Vercel rewrites /health to Railway; browser URL is already canonical."""
    if request.headers.get("x-vercel-id"):
        return True
    upstream = (request.url.hostname or "").lower()
    return upstream.endswith(".railway.app") or ".up.railway.app" in upstream


def _canonical_redirect(request: Request) -> RedirectResponse | None:
    """Force auth on FRONTEND_URL host so apex/www and PWA share cookies."""
    if _is_upstream_proxied(request):
        return None
    frontend = _normalize_origin(os.getenv("FRONTEND_URL", ""))
    if not frontend:
        return None
    canon_host = (urlparse(frontend).hostname or "").lower()
    req_host = _client_host(request)
    if not canon_host or not req_host or req_host == canon_host:
        return None
    target = f"{frontend}{request.url.path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(target, status_code=307)


def session_secret() -> str:
    return (
        os.getenv("DASHBOARD_SESSION_SECRET")
        or os.getenv("SUPABASE_ANON_KEY")
        or "dev-insecure-session-secret"
    )


def allowed_origins() -> list[str]:
    """Origins permitted for CORS + post-login return_to redirects."""
    seen: set[str] = set()
    out: list[str] = []
    for key in ("FRONTEND_URL", "RAILWAY_PUBLIC_URL", "RAILWAY_URL"):
        origin = _normalize_origin(os.getenv(key, ""))
        if origin and origin not in seen:
            seen.add(origin)
            out.append(origin)
    for origin in ("http://127.0.0.1:8000", "http://localhost:8000"):
        if origin not in seen:
            seen.add(origin)
            out.append(origin)
    return out


def _default_app_url() -> str:
    frontend = _normalize_origin(os.getenv("FRONTEND_URL", ""))
    if frontend:
        return f"{frontend}/app"
    for key in ("RAILWAY_PUBLIC_URL", "RAILWAY_URL"):
        origin = _normalize_origin(os.getenv(key, ""))
        if origin:
            return f"{origin}/app"
    return "/app"


def _safe_return_url(raw: str | None) -> str | None:
    """Reject open redirects — only allow configured frontend/API origins."""
    if not raw or not str(raw).strip():
        return None
    url = str(raw).strip()
    for origin in allowed_origins():
        if url == origin or url.startswith(origin + "/"):
            return url
    return None


def _post_auth_redirect(request: Request) -> str:
    stored = request.session.pop("return_to", None)
    safe = _safe_return_url(stored)
    return safe or _default_app_url()


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _session_email(request: Request) -> str | None:
    email = request.session.get("user_email")
    return email.strip().lower() if isinstance(email, str) and email.strip() else None


def session_user_id(request: Request):
    """Return logged-in user's UUID from session, or None."""
    from uuid import UUID

    raw = request.session.get("user_id")
    if isinstance(raw, str) and raw.strip():
        try:
            return UUID(raw.strip())
        except ValueError:
            pass
    email = _session_email(request)
    if not email:
        return None
    from db import repository as repo

    user = repo.get_user_by_email(email)
    if not user:
        user = repo.ensure_user_from_auth_email(email)
    if user and user.get("id"):
        uid = UUID(str(user["id"]))
        request.session["user_id"] = str(uid)
        return uid
    return None


def is_authorized(request: Request) -> bool:
    allowed = authorized_email()
    if not allowed:
        return False
    return _session_email(request) == allowed


def _format_run_time(started_at: str | None) -> str:
    if not started_at:
        return "—"
    dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    local = dt.astimezone(ZoneInfo("Asia/Kolkata"))
    return local.strftime("%d %b %Y, %H:%M IST")


def _format_run_time_compact(started_at: str | None) -> str:
    """Short timestamp for dense tables — time only if today, else day + time."""
    if not started_at:
        return "—"
    dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    ist = ZoneInfo("Asia/Kolkata")
    local = dt.astimezone(ist)
    today = datetime.now(ist).date()
    if local.date() == today:
        return local.strftime("%H:%M")
    return local.strftime("%d %b %H:%M")


def _stage_chip(status: str | None) -> str:
    if status == "success":
        return "ok"
    if status == "failed":
        return "bad"
    if status == "skipped":
        return "skip"
    if status in ("partial", "running"):
        return "warn"
    return "idle"


def _flatten_stages(stages: dict) -> list[dict[str, Any]]:
    """Normalize stages JSON into rows for the checklist UI."""
    rows: list[dict[str, Any]] = []
    if not stages:
        return rows

    for key in ("fetch", "technicals", "market_context"):
        info = stages.get(key)
        if info is None:
            rows.append(
                {
                    "id": key,
                    "label": key.replace("_", " ").title(),
                    "status": None,
                    "chip": "idle",
                    "detail": "not started",
                }
            )
        else:
            st = info.get("status")
            detail = info.get("detail") or ""
            rows.append(
                {
                    "id": key,
                    "label": key.replace("_", " ").title(),
                    "status": st,
                    "chip": _stage_chip(st),
                    "detail": detail or st or "",
                }
            )

    funnels = stages.get("funnels") or {}
    for name in ("value", "winners", "box", "dip"):
        info = funnels.get(name)
        label = f"Funnel · {name}"
        if info is None:
            rows.append(
                {
                    "id": f"funnels.{name}",
                    "label": label,
                    "status": None,
                    "chip": "idle",
                    "detail": "not started",
                }
            )
        else:
            st = info.get("status")
            detail = info.get("detail") or f"in={info.get('in')} out={info.get('out')}"
            rows.append(
                {
                    "id": f"funnels.{name}",
                    "label": label,
                    "status": st,
                    "chip": _stage_chip(st),
                    "detail": detail,
                }
            )

    scoring = stages.get("batch_scoring") or {}
    for name in ("value", "winners", "box", "dip"):
        info = scoring.get(name)
        label = f"Batch scoring · {name}"
        if info is None:
            rows.append(
                {
                    "id": f"batch_scoring.{name}",
                    "label": label,
                    "status": None,
                    "chip": "idle",
                    "detail": "not started",
                }
            )
        else:
            st = info.get("status")
            detail = info.get("detail") or (
                f"scored={info.get('candidates_scored')} "
                f"survivors={info.get('survivors')}"
            )
            rows.append(
                {
                    "id": f"batch_scoring.{name}",
                    "label": label,
                    "status": st,
                    "chip": _stage_chip(st),
                    "detail": detail,
                }
            )

    cache = stages.get("cache_saved") or {}
    shortlists = stages.get("shortlists") or {}
    if not cache:
        rows.append(
            {
                "id": "cache_saved",
                "label": "Shortlist cache",
                "status": None,
                "chip": "idle",
                "detail": "not started",
                "shortlists": shortlists,
            }
        )
    else:
        parts: list[str] = []
        for k in ("value", "winners", "box", "dip"):
            saved = cache.get(k)
            cands = shortlists.get(k) if isinstance(shortlists.get(k), list) else []
            n = len(cands)
            if saved and n:
                parts.append(f"{k}: {n} candidate{'s' if n != 1 else ''}")
            elif saved:
                parts.append(f"{k}: saved")
            else:
                parts.append(f"{k}: missing")
        ok = all(bool(v) for v in cache.values()) if cache else False
        rows.append(
            {
                "id": "cache_saved",
                "label": "Shortlist cache",
                "status": "success" if ok else "partial",
                "chip": "ok" if ok else "warn",
                "detail": ", ".join(parts),
                "shortlists": shortlists,
            }
        )

    return rows


def _format_token_count(n: int | float | None) -> str:
    try:
        return f"{int(n or 0):,}"
    except (TypeError, ValueError):
        return "0"


def _format_duration_ms(ms: int | float | None) -> str:
    try:
        value = int(ms or 0)
    except (TypeError, ValueError):
        return "0s"
    if value < 1000:
        return f"{value}ms"
    return f"{value / 1000:.1f}s"


def _format_cost_usd(amount: int | float | None) -> str:
    try:
        value = float(amount or 0)
    except (TypeError, ValueError):
        return "$0.00"
    if value >= 1.0:
        return f"${value:,.2f}"
    if value >= 0.01:
        return f"${value:.2f}"
    if value > 0:
        return f"${value:.4f}"
    return "$0.00"


def _format_delta_pct(delta: float | None) -> str:
    if delta is None:
        return "—"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.0f}%"


def _ingestion_run_summary(stages: dict | None) -> dict[str, Any]:
    """Compact ingestion stats for recent-run cards."""
    from selector.llm.usage import extract_run_llm_snapshot

    snap = extract_run_llm_snapshot(stages)
    if not snap:
        return {"llm_tokens": 0, "llm_cost_usd": 0.0, "llm_calls": 0}
    totals = snap.get("totals") or {}
    return {
        "llm_tokens": int(totals.get("total_tokens", 0) or 0),
        "llm_cost_usd": float(totals.get("estimated_cost_usd", 0) or 0),
        "llm_calls": int(totals.get("calls", 0) or 0),
        "avg_cost_per_batch_usd": totals.get("avg_cost_per_batch_usd"),
    }


def _llm_usage_for_health(
    stages: dict | None,
    baselines: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build dashboard view-model for daily-ingestion LLM usage."""
    from selector.llm.usage import build_llm_drift_report, extract_run_llm_snapshot

    raw = extract_run_llm_snapshot(stages)
    if not raw:
        return None

    totals = raw.get("totals") or {}
    by_strategy = raw.get("by_strategy") or {}
    model = raw.get("model") or "—"
    drift_report = build_llm_drift_report(raw, baselines)
    totals_drift = drift_report.get("totals") or {}

    strategies: list[dict[str, Any]] = []
    for name in ("value", "winners", "box", "dip"):
        info = by_strategy.get(name)
        if not isinstance(info, dict):
            continue
        strat_drift = (drift_report.get("strategies") or {}).get(name, {})
        drifts = strat_drift.get("drifts") or {}

        strategies.append(
            {
                "name": name,
                "calls": int(info.get("calls", 0) or 0),
                "retries": int(info.get("retries", 0) or 0),
                "total_tokens": int(info.get("total_tokens", 0) or 0),
                "prompt_tokens": int(info.get("prompt_tokens", 0) or 0),
                "output_tokens": int(info.get("output_tokens", 0) or 0),
                "cached_tokens": int(info.get("cached_tokens", 0) or 0),
                "thoughts_tokens": int(info.get("thoughts_tokens", 0) or 0),
                "elapsed_ms": int(info.get("elapsed_ms", 0) or 0),
                "estimated_cost_usd": float(info.get("estimated_cost_usd", 0) or 0),
                "avg_cost_per_batch_usd": info.get("avg_cost_per_batch_usd"),
                "avg_tokens_per_batch": info.get("avg_tokens_per_batch"),
                "avg_prompt_tokens_per_batch": info.get("avg_prompt_tokens_per_batch"),
                "avg_output_tokens_per_batch": info.get("avg_output_tokens_per_batch"),
                "cost_per_symbol_usd": info.get("cost_per_symbol_usd"),
                "tokens_per_symbol": info.get("tokens_per_symbol"),
                "prompt_tokens_per_symbol": info.get("prompt_tokens_per_symbol"),
                "output_tokens_per_symbol": info.get("output_tokens_per_symbol"),
                "payload_tokens_per_symbol": info.get("payload_tokens_per_symbol"),
                "payload_chars_per_symbol": info.get("payload_chars_per_symbol"),
                "system_prompt_estimated_cost_usd": info.get("system_prompt_estimated_cost_usd"),
                "system_prompt_estimated_tokens": info.get("system_prompt_estimated_tokens"),
                "retry_rate": info.get("retry_rate"),
                "candidates_scored": info.get("candidates_scored"),
                "survivors": info.get("survivors"),
                "status": info.get("status"),
                "drift": {
                    "payload_tokens_per_symbol": drifts.get("payload_tokens_per_symbol") or {},
                    "tokens_per_symbol": drifts.get("tokens_per_symbol") or {},
                    "cost_per_symbol": drifts.get("cost_per_symbol_usd") or {},
                    "total_cost": drifts.get("estimated_cost_usd") or {},
                    "severity": strat_drift.get("severity") or "ok",
                },
            }
        )

    batch_drift_by_key = {
        (b.get("strategy"), b.get("batch")): b
        for b in (drift_report.get("batches") or [])
    }

    def _batch_row(batch: dict[str, Any], bd: dict[str, Any]) -> dict[str, Any]:
        drifts = bd.get("drifts") or {}
        scored_at = batch.get("scored_at") or batch.get("run_started_at")
        return {
            "strategy": batch.get("strategy") or "?",
            "batch": batch.get("batch") or "?",
            "symbols": ", ".join(batch.get("symbols") or []),
            "symbol_count": int(batch.get("symbol_count", 0) or 0),
            "status": batch.get("status") or "?",
            "attempts": int(batch.get("attempts", 1) or 1),
            "total_tokens": int(batch.get("total_tokens", 0) or 0),
            "prompt_tokens": int(batch.get("prompt_tokens", 0) or 0),
            "output_tokens": int(batch.get("output_tokens", 0) or 0),
            "elapsed_ms": int(batch.get("elapsed_ms", 0) or 0),
            "estimated_cost_usd": float(batch.get("estimated_cost_usd", 0) or 0),
            "cost_per_symbol_usd": batch.get("cost_per_symbol_usd"),
            "tokens_per_symbol": batch.get("tokens_per_symbol"),
            "prompt_tokens_per_symbol": batch.get("prompt_tokens_per_symbol"),
            "output_tokens_per_symbol": batch.get("output_tokens_per_symbol"),
            "payload_chars": batch.get("payload_chars"),
            "payload_tokens": batch.get("payload_tokens"),
            "payload_chars_per_symbol": batch.get("payload_chars_per_symbol"),
            "payload_tokens_per_symbol": batch.get("payload_tokens_per_symbol"),
            "system_prompt_chars": batch.get("system_prompt_chars"),
            "system_prompt_estimated_tokens": batch.get("system_prompt_estimated_tokens"),
            "system_prompt_estimated_cost_usd": batch.get("system_prompt_estimated_cost_usd"),
            "run_id": batch.get("run_id"),
            "run_date": batch.get("run_date"),
            "run_started_at": batch.get("run_started_at"),
            "scored_at": scored_at,
            "run_at": _format_run_time(scored_at or batch.get("run_started_at")),
            "run_at_short": _format_run_time_compact(scored_at or batch.get("run_started_at")),
            "run_date_label": batch.get("run_date") or "—",
            "drift": {
                "payload_tokens_per_symbol": drifts.get("payload_tokens_per_symbol") or {},
                "tokens_per_symbol": drifts.get("tokens_per_symbol") or {},
                "cost_per_symbol": drifts.get("cost_per_symbol_usd") or {},
                "latency": drifts.get("elapsed_ms") or {},
                "severity": bd.get("severity") or "ok",
            },
        }

    batches = []
    for batch in raw.get("batches") or []:
        if not isinstance(batch, dict):
            continue
        key = (batch.get("strategy"), batch.get("batch"))
        bd = batch_drift_by_key.get(key, {})
        batches.append(_batch_row(batch, bd))

    batches.sort(key=lambda b: float(b.get("estimated_cost_usd", 0) or 0), reverse=True)

    return {
        "phase": raw.get("phase") or "daily_ingestion",
        "model": model,
        "pricing_note": "Est. Gemini paid-tier list price (input + output + cache)",
        "baseline": {
            "sample_size": int((baselines or {}).get("sample_size", 0) or 0),
            "window": int((baselines or {}).get("window", 0) or 0),
        },
        "drift_max_severity": drift_report.get("max_severity") or "ok",
        "totals": {
            "calls": int(totals.get("calls", 0) or 0),
            "retries": int(totals.get("retries", 0) or 0),
            "total_tokens": int(totals.get("total_tokens", 0) or 0),
            "prompt_tokens": int(totals.get("prompt_tokens", 0) or 0),
            "output_tokens": int(totals.get("output_tokens", 0) or 0),
            "cached_tokens": int(totals.get("cached_tokens", 0) or 0),
            "thoughts_tokens": int(totals.get("thoughts_tokens", 0) or 0),
            "elapsed_ms": int(totals.get("elapsed_ms", 0) or 0),
            "estimated_cost_usd": float(totals.get("estimated_cost_usd", 0) or 0),
            "avg_cost_per_batch_usd": totals.get("avg_cost_per_batch_usd"),
            "avg_tokens_per_batch": totals.get("avg_tokens_per_batch"),
            "avg_prompt_tokens_per_batch": totals.get("avg_prompt_tokens_per_batch"),
            "avg_output_tokens_per_batch": totals.get("avg_output_tokens_per_batch"),
            "cost_per_symbol_usd": totals.get("cost_per_symbol_usd"),
            "tokens_per_symbol": totals.get("tokens_per_symbol"),
            "prompt_tokens_per_symbol": totals.get("prompt_tokens_per_symbol"),
            "output_tokens_per_symbol": totals.get("output_tokens_per_symbol"),
            "payload_tokens_per_symbol": totals.get("payload_tokens_per_symbol"),
            "payload_chars_per_symbol": totals.get("payload_chars_per_symbol"),
            "payload_tokens_total": totals.get("payload_tokens_total"),
            "payload_chars_total": totals.get("payload_chars_total"),
            "system_prompt_estimated_cost_usd": totals.get("system_prompt_estimated_cost_usd"),
            "system_prompt_estimated_tokens": totals.get("system_prompt_estimated_tokens"),
            "avg_system_prompt_estimated_cost_usd": totals.get("avg_system_prompt_estimated_cost_usd"),
            "retry_rate": totals.get("retry_rate"),
            "drift": {
                "total_cost": totals_drift.get("estimated_cost_usd") or {},
                "payload_tokens_per_symbol": totals_drift.get("payload_tokens_per_symbol") or {},
                "tokens_per_symbol": totals_drift.get("tokens_per_symbol") or {},
                "system_prompt_cost": totals_drift.get("system_prompt_estimated_cost_usd") or {},
                "avg_batch_cost": totals_drift.get("avg_cost_per_batch_usd") or {},
                "avg_batch_tokens": totals_drift.get("avg_tokens_per_batch") or {},
                "cost_per_symbol": totals_drift.get("cost_per_symbol_usd") or {},
            },
        },
        "strategies": strategies,
        "batches": batches,
    }


def _assemble_llm_usage_for_health(
    recent_raw: list[dict[str, Any]],
    baselines: dict[str, Any] | None,
    today_stages: dict | None,
) -> dict[str, Any] | None:
    """Build LLM health panel from batch history; today optional for drift badges."""
    from datetime import date

    from scoring.output_quality import build_quality_summary
    from selector.llm.usage import aggregate_llm_batches, extract_run_llm_snapshot

    batches = _llm_batches_history(recent_raw, max_days=14)
    if not batches:
        return None

    today_usage = _llm_usage_for_health(today_stages, baselines)
    today_drift: dict[str, Any] = {"totals": {}, "strategies": {}}
    model = "gemini-2.5-flash"
    drift_max_severity = "ok"
    baseline_meta = {
        "sample_size": int((baselines or {}).get("sample_size", 0) or 0),
        "window": int((baselines or {}).get("window", 0) or 0),
    }

    if today_usage is not None:
        today_drift = {
            "totals": (today_usage.get("totals") or {}).get("drift") or {},
            "strategies": {
                s["name"]: s.get("drift") or {}
                for s in (today_usage.get("strategies") or [])
            },
        }
        model = str(today_usage.get("model") or model)
        drift_max_severity = str(today_usage.get("drift_max_severity") or "ok")
        baseline_meta = today_usage.get("baseline") or baseline_meta
    else:
        for run in recent_raw:
            snap = extract_run_llm_snapshot(run.get("stages") or {})
            if snap:
                model = str(snap.get("model") or model)
                break

    agg = aggregate_llm_batches(batches, model=model)
    today_s = str(date.today())
    today_batches = [
        b
        for b in batches
        if isinstance(b, dict) and str(b.get("run_date") or "")[:10] == today_s
    ]

    return {
        "phase": "daily_ingestion",
        "model": model,
        "pricing_note": "Est. Gemini paid-tier list price (input + output + cache)",
        "baseline": baseline_meta,
        "drift_max_severity": drift_max_severity,
        "today_has_llm": today_usage is not None,
        "totals": {**agg["totals"], "drift": today_drift["totals"]},
        "strategies": [
            {**s, "drift": today_drift["strategies"].get(s["name"], {})}
            for s in agg["strategies"]
        ],
        "batches": batches,
        "today_drift": today_drift,
        "quality_summary": build_quality_summary(batches),
        "today_quality": build_quality_summary(today_batches),
    }


def _llm_batches_history(
    recent_runs: list[dict[str, Any]],
    *,
    max_days: int = 14,
) -> list[dict[str, Any]]:
    """Merge batch rows from recent ingestion runs for the batch breakdown table."""
    from selector.llm.usage import build_llm_baselines, build_llm_drift_report, extract_run_llm_snapshot

    cutoff = date.today() - timedelta(days=max(1, max_days) - 1)
    rows: list[dict[str, Any]] = []

    for i, run in enumerate(recent_runs):
        run_date_raw = run.get("date")
        if run_date_raw:
            try:
                run_day = date.fromisoformat(str(run_date_raw)[:10])
                if run_day < cutoff:
                    continue
            except ValueError:
                pass

        snap = extract_run_llm_snapshot(run.get("stages") or {})
        if not snap:
            continue

        prior_runs = recent_runs[i + 1 :]
        baselines = build_llm_baselines(prior_runs)
        drift_report = build_llm_drift_report(snap, baselines)
        batch_drift_by_key = {
            (b.get("strategy"), b.get("batch")): b for b in (drift_report.get("batches") or [])
        }

        run_id = run.get("id")
        run_date = str(run_date_raw)[:10] if run_date_raw else None
        run_started_at = run.get("started_at")

        for batch in snap.get("batches") or []:
            if not isinstance(batch, dict):
                continue
            enriched = dict(batch)
            enriched.setdefault("run_id", run_id)
            enriched.setdefault("run_date", run_date)
            enriched.setdefault("run_started_at", run_started_at)
            if not enriched.get("scored_at"):
                enriched["scored_at"] = run_started_at

            key = (enriched.get("strategy"), enriched.get("batch"))
            stored_debug = enriched.get("drift_debug") if isinstance(enriched.get("drift_debug"), dict) else {}
            bd = batch_drift_by_key.get(key, {})
            drifts = stored_debug.get("drifts") or bd.get("drifts") or {}
            severity = stored_debug.get("severity") or bd.get("severity") or "ok"
            scored_at = enriched.get("scored_at") or run_started_at
            rows.append(
                {
                    "strategy": enriched.get("strategy") or "?",
                    "batch": enriched.get("batch") or "?",
                    "symbols": ", ".join(enriched.get("symbols") or []),
                    "symbol_list": enriched.get("symbols") or [],
                    "symbol_count": int(enriched.get("symbol_count", 0) or 0),
                    "status": enriched.get("status") or "?",
                    "attempts": int(enriched.get("attempts", 1) or 1),
                    "total_tokens": int(enriched.get("total_tokens", 0) or 0),
                    "prompt_tokens": int(enriched.get("prompt_tokens", 0) or 0),
                    "output_tokens": int(enriched.get("output_tokens", 0) or 0),
                    "thoughts_tokens": int(enriched.get("thoughts_tokens", 0) or 0),
                    "cached_tokens": int(enriched.get("cached_tokens", 0) or 0),
                    "elapsed_ms": int(enriched.get("elapsed_ms", 0) or 0),
                    "estimated_cost_usd": float(enriched.get("estimated_cost_usd", 0) or 0),
                    "cost_per_symbol_usd": enriched.get("cost_per_symbol_usd"),
                    "tokens_per_symbol": enriched.get("tokens_per_symbol"),
                    "payload_chars": int(enriched.get("payload_chars", 0) or 0),
                    "payload_tokens": int(enriched.get("payload_tokens", 0) or 0),
                    "payload_tokens_per_symbol": enriched.get("payload_tokens_per_symbol"),
                    "payload_breakdown": enriched.get("payload_breakdown") or {},
                    "system_prompt_estimated_tokens": enriched.get("system_prompt_estimated_tokens"),
                    "system_prompt_estimated_cost_usd": enriched.get("system_prompt_estimated_cost_usd"),
                    "run_id": enriched.get("run_id"),
                    "run_date": run_date,
                    "run_started_at": run_started_at,
                    "scored_at": scored_at,
                    "run_at": _format_run_time(scored_at),
                    "run_at_short": _format_run_time_compact(scored_at),
                    "drift": {
                        "payload_tokens_per_symbol": drifts.get("payload_tokens_per_symbol") or {},
                        "latency": drifts.get("elapsed_ms") or {},
                        "severity": severity,
                    },
                    "output_quality": enriched.get("output_quality") or {},
                }
            )

    rows.sort(
        key=lambda b: (b.get("scored_at") or b.get("run_started_at") or "", b.get("strategy") or ""),
        reverse=True,
    )
    return rows


def _batch_debug_from_run(
    run: dict[str, Any] | None,
    *,
    strategy: str,
    batch: str,
    recent_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    from selector.llm.usage import (
        analyze_batch_drift,
        build_symbol_section_baselines,
        extract_run_llm_snapshot,
        find_batch_in_llm_usage,
    )

    if not run:
        return None
    stages = run.get("stages") or {}
    llm_usage = extract_run_llm_snapshot(stages) or (stages.get("llm_usage") if isinstance(stages.get("llm_usage"), dict) else None)
    if not llm_usage:
        return None

    batch_row = find_batch_in_llm_usage(llm_usage, strategy=strategy, batch=batch)
    if not batch_row:
        return None

    if recent_runs is None:
        from health_status import get_recent_statuses

        recent_runs = get_recent_statuses(14)

    from scoring.batch_scorer import BATCH_SIZE

    symbol_baselines = build_symbol_section_baselines(
        recent_runs,
        strategy,
        exclude_run_id=run.get("id"),
    )
    analysis = analyze_batch_drift(batch_row, symbol_baselines, batch_size=BATCH_SIZE)

    drift_debug = batch_row.get("drift_debug") if isinstance(batch_row.get("drift_debug"), dict) else {}
    drifts = drift_debug.get("drifts") or {}
    payload_drift = drifts.get("payload_tokens_per_symbol") or {}
    latency_drift = drifts.get("elapsed_ms") or {}
    breakdown = batch_row.get("payload_breakdown") if isinstance(batch_row.get("payload_breakdown"), dict) else {}
    drift_report = llm_usage.get("drift_report") if isinstance(llm_usage.get("drift_report"), dict) else {}
    llm_io = batch_row.get("llm_io") if isinstance(batch_row.get("llm_io"), dict) else {}

    return {
        "run_id": run.get("id"),
        "run_date": run.get("date"),
        "run_started_at": run.get("started_at"),
        "run_finished_at": run.get("finished_at"),
        "strategy": strategy,
        "batch": batch,
        "batch_row": batch_row,
        "symbols": batch_row.get("symbols") or [],
        "payload_tokens_per_symbol": batch_row.get("payload_tokens_per_symbol"),
        "payload_breakdown": breakdown,
        "llm_io": llm_io,
        "output_quality": batch_row.get("output_quality") if isinstance(batch_row.get("output_quality"), dict) else {},
        "analysis": analysis,
        "drift": {
            "severity": drift_debug.get("severity"),
            "payload_tokens_per_symbol": payload_drift,
            "latency": latency_drift,
        },
        "drift_report": {
            "baseline_sample_size": drift_report.get("baseline_sample_size"),
            "baseline_window": drift_report.get("baseline_window"),
            "max_severity": drift_report.get("max_severity"),
            "totals": drift_report.get("totals"),
        },
        "run_logs_meta": (stages.get("run_logs") or {}) if isinstance(stages.get("run_logs"), dict) else {},
    }


def _flatten_fm_stages(stages: dict) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, label in (
        ("shortlist", "Shortlist"),
        ("holdings", "Holdings"),
        ("brain", "Wolf Brain"),
        ("executor", "Executor"),
        ("intents", "Intents"),
    ):
        info = (stages or {}).get(key)
        if info is None:
            rows.append(
                {
                    "label": label,
                    "status": None,
                    "chip": "idle",
                    "detail": "not started",
                }
            )
        else:
            st = info.get("status")
            rows.append(
                {
                    "label": label,
                    "status": st,
                    "chip": _stage_chip(st),
                    "detail": info.get("detail") or "",
                }
            )
    return rows


def _load_shortlists_from_disk() -> dict[str, list[dict[str, Any]]]:
    from datetime import date

    from cache.shortlist_cache import load_shortlist

    out: dict[str, list[dict[str, Any]]] = {}
    for name in ("value", "winners", "box", "dip"):
        cands = load_shortlist(name, date.today())
        if cands:
            out[name] = cands
    return out


_SHORTLIST_STRATEGIES = ("value", "winners", "box", "dip")


def _resolve_shortlists_for_health(stages: dict | None) -> dict[str, list[dict[str, Any]]]:
    """Prefer shortlists on today's health run; per-strategy fallback when missing.

    Always returns all 4 strategy keys (defaulting to []) so a strategy that
    genuinely found zero candidates today is shown as "0 candidates" rather
    than silently disappearing from the UI — that used to look identical to a
    missing/broken pipeline stage.
    """
    from_stages = (stages or {}).get("shortlists") or {}
    if not isinstance(from_stages, dict):
        from_stages = {}

    disk_cache: dict[str, list[dict[str, Any]]] | None = None
    cron_cache: dict[str, list[dict[str, Any]]] | None = None

    def _disk() -> dict[str, list[dict[str, Any]]]:
        nonlocal disk_cache
        if disk_cache is None:
            disk_cache = _load_shortlists_from_disk()
        return disk_cache

    def _cron() -> dict[str, list[dict[str, Any]]]:
        nonlocal cron_cache
        if cron_cache is None:
            from cache.shortlist_cache import fetch_shortlists_from_cron

            cron_cache = fetch_shortlists_from_cron() or {}
        return cron_cache

    out: dict[str, list[dict[str, Any]]] = {}
    for name in _SHORTLIST_STRATEGIES:
        stage_val = from_stages.get(name)
        if name in from_stages and isinstance(stage_val, list):
            # Pipeline recorded this strategy (including explicit []).
            out[name] = stage_val
        else:
            out[name] = _disk().get(name) or _cron().get(name) or []
    return out


@router.get("/health", response_class=HTMLResponse)
async def health_page(request: Request):
    """Health Check page — gated; shows Not authorized with no data if denied."""
    authed = is_authorized(request)
    email = _session_email(request)
    allowed_configured = bool(authorized_email())

    recent = []
    today_row = None
    today_rows: list[dict] = []
    shortlists: dict[str, list] = {}
    llm_usage: dict[str, Any] | None = None
    db_error: str | None = None
    fm_today: list[dict] = []
    fm_recent: list[dict] = []
    fm_not_started = False
    fm_error: str | None = None
    if authed:
        from datetime import date

        try:
            from health_status import get_recent_statuses, get_status

            recent_raw = get_recent_statuses(14)
            today_row = get_status(date.today())
            today_id = (today_row or {}).get("id")
            baseline_runs = [r for r in recent_raw if r.get("id") != today_id]

            from selector.llm.usage import build_llm_baselines

            llm_baselines = build_llm_baselines(baseline_runs)

            for r in recent_raw[:5]:
                stages = r.get("stages") or {}
                ingestion = _ingestion_run_summary(stages)
                recent.append(
                    {
                        "date": r.get("date"),
                        "run_at": _format_run_time(r.get("started_at")),
                        "overall": r.get("overall_status") or "unknown",
                        "overall_chip": _stage_chip(r.get("overall_status")),
                        "stages": _flatten_stages(stages),
                        "llm_tokens": ingestion["llm_tokens"],
                        "llm_cost_usd": ingestion["llm_cost_usd"],
                        "llm_calls": ingestion["llm_calls"],
                    }
                )
            today_stages = (today_row or {}).get("stages") or {}
            today_rows = _flatten_stages(today_stages)
            shortlists = _resolve_shortlists_for_health(today_stages)
            llm_usage = _assemble_llm_usage_for_health(recent_raw, llm_baselines, today_stages)
        except Exception as e:
            import logging

            logging.exception("health page: failed to load health_status from database")
            from db.connection import connection_hint

            detail = str(e).strip() or type(e).__name__
            db_error = (
                "Could not load pipeline health from the database. "
                f"{detail}{connection_hint(e)}"
            )

        try:
            from fund_manager_health import get_recent_day_summaries, get_runs_for_day
            from db import repository as repo_db

            fm_raw = get_runs_for_day(date.today())
            seen_wolves: set[str] = set()
            for r in fm_raw:
                wid = r.get("wolf_id") or ""
                if not wid or wid in seen_wolves:
                    continue
                seen_wolves.add(wid)
                wolf = repo_db.get_wolf(wid) or {}
                fm_today.append(
                    {
                        "wolf_id": wid,
                        "wolf_name": wolf.get("wolf_name") or wid,
                        "strategy": wolf.get("strategy_code") or "—",
                        "overall": r.get("overall_status") or "unknown",
                        "overall_chip": _stage_chip(r.get("overall_status")),
                        "run_at": _format_run_time(r.get("started_at")),
                        "error": r.get("error_detail") or "",
                        "stages": _flatten_fm_stages(r.get("stages") or {}),
                    }
                )
            fm_not_started = not fm_today and not fm_error
            for day in get_recent_day_summaries(5):
                total = int(day.get("total") or 0)
                ok = int(day.get("ok") or 0)
                fm_recent.append(
                    {
                        "date": day.get("date"),
                        "overall": day.get("overall") or "unknown",
                        "overall_chip": _stage_chip(day.get("overall")),
                        "detail": f"{ok}/{total} wolves ok" if total else "no runs",
                    }
                )
        except Exception as e:
            import logging

            logging.exception("health page: failed to load fund_manager_runs")
            from db.connection import connection_hint

            detail = str(e).strip() or type(e).__name__
            fm_error = (
                "Could not load fund manager health. "
                f"{detail}{connection_hint(e)}"
            )

    return TEMPLATES.TemplateResponse(
        request,
        "health.html",
        {
            "authorized": authed,
            "email": email,
            "allowed_configured": allowed_configured,
            "show_nav_health": authed,
            "recent": recent,
            "today": today_row,
            "today_rows": today_rows,
            "shortlists": shortlists,
            "llm_usage": llm_usage,
            "format_token_count": _format_token_count,
            "format_duration_ms": _format_duration_ms,
            "format_cost_usd": _format_cost_usd,
            "format_delta_pct": _format_delta_pct,
            "not_started": authed and not db_error and today_row is None,
            "db_error": db_error,
            "fm_today": fm_today,
            "fm_recent": fm_recent,
            "fm_not_started": authed and fm_not_started and not fm_error,
            "fm_error": fm_error,
            "cron_api_configured": bool(
                (os.getenv("DOSSIER_API_URL") or "").strip()
            ),
        },
    )


@router.post("/api/ops/run-pipeline")
async def api_run_pipeline(request: Request):
    """Trigger full morning pipeline on data-layer-cron (authorized ops only)."""
    if not is_authorized(request):
        return JSONResponse({"error": "Not authorized"}, status_code=403)

    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1] / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from dossier_sync import trigger_pipeline_run

    try:
        result = trigger_pipeline_run()
        if not result.get("started"):
            return JSONResponse(result, status_code=409)
        return result
    except Exception as e:
        return JSONResponse(
            {"error": "Pipeline trigger failed", "detail": str(e)},
            status_code=502,
        )


@router.get("/health/login")
async def health_login(request: Request, return_to: str | None = None):
    """Start Google OAuth via Supabase Auth (PKCE)."""
    if canon := _canonical_redirect(request):
        return canon

    verifier, challenge = _pkce_pair()
    request.session["pkce_verifier"] = verifier
    safe_return = _safe_return_url(return_to)
    if safe_return:
        request.session["return_to"] = safe_return

    params = {
        "provider": "google",
        "redirect_to": redirect_url(),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = f"{supabase_url()}/auth/v1/authorize?{urlencode(params)}"
    response = RedirectResponse(url)
    _set_pkce_cookie(response, verifier)
    return response


@router.get("/health/auth/callback")
async def health_auth_callback(request: Request, code: str | None = None):
    """Exchange ?code= for a session (PKCE) and store user email."""
    if canon := _canonical_redirect(request):
        return canon

    if not code:
        return HTMLResponse(
            "<p>Missing auth code. <a href='/health'>Back</a></p>",
            status_code=400,
        )

    verifier = _read_pkce_verifier(request)
    if not verifier:
        return HTMLResponse(
            "<p>Missing PKCE verifier (start login again). "
            "<a href='/health/login'>Log in</a></p>",
            status_code=400,
        )

    token_url = f"{supabase_url()}/auth/v1/token?grant_type=pkce"
    headers = {
        "apikey": supabase_anon_key(),
        "Authorization": f"Bearer {supabase_anon_key()}",
        "Content-Type": "application/json",
    }
    payload = {"auth_code": code, "code_verifier": verifier}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(token_url, headers=headers, json=payload)

    if resp.status_code >= 400:
        return HTMLResponse(
            f"<p>Auth exchange failed ({resp.status_code}). "
            f"<a href='/health/login'>Retry</a></p><pre>{resp.text}</pre>",
            status_code=400,
        )

    data = resp.json()
    user = data.get("user") or {}
    email = (user.get("email") or "").strip().lower()
    if not email:
        # Some responses nest under session
        email = ((data.get("session") or {}).get("user") or {}).get("email") or ""
        email = email.strip().lower()

    request.session.pop("pkce_verifier", None)
    request.session["user_email"] = email
    user_id = (user.get("id") or "").strip()
    if user_id:
        request.session["user_id"] = user_id
    request.session["access_token"] = data.get("access_token")
    response = RedirectResponse(_post_auth_redirect(request), status_code=303)
    _clear_pkce_cookie(response)
    return response


@router.get("/health/logout")
async def health_logout(request: Request, return_to: str | None = None):
    dest = _safe_return_url(return_to) or _default_app_url()
    request.session.clear()
    response = RedirectResponse(dest, status_code=303)
    _clear_auth_cookies(response)
    return response


@router.get("/api/ops/me")
async def api_ops_me(request: Request):
    """Current session identity for the Trading UI header (cookies required)."""
    email = _session_email(request)
    uid = session_user_id(request)
    return {
        "email": email,
        "user_id": str(uid) if uid else None,
        "authorized": is_authorized(request),
        "logged_in": bool(email),
    }


@router.post("/api/ops/reload-kite-session")
async def api_reload_kite_session(request: Request):
    """Re-read Kite token from Supabase and verify — does not run TOTP."""
    if not is_authorized(request):
        return JSONResponse({"error": "Not authorized"}, status_code=403)

    import asyncio
    import importlib.util
    import sys

    def _reload() -> dict:
        repo = Path(__file__).resolve().parents[1]
        backend = repo / "backend"
        backend_str = str(backend)
        if backend_str not in sys.path:
            sys.path.insert(0, backend_str)
        repo_str = str(repo)
        if repo_str not in sys.path:
            sys.path.insert(0, repo_str)

        path = backend / "fund_manager" / "kite_auth.py"
        spec = importlib.util.spec_from_file_location("_ops_kite_reload", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load kite_auth.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.clear_kite_session()

        from dashboard.external_health import _check_kite
        from db.kite_token_store import load_token_metadata

        check = _check_kite()
        meta = load_token_metadata()
        out: dict = {"check": check}
        if meta:
            out["token_synced_at"] = meta.get("generated_at")
            out["token_expires_at"] = meta.get("expires_at")
        if check.get("status") == "ok":
            out["status"] = "ok"
        else:
            out["status"] = "warn"
        return out

    try:
        return await asyncio.to_thread(_reload)
    except Exception as e:
        return JSONResponse(
            {"error": "Kite re-check failed", "detail": str(e)},
            status_code=502,
        )


@router.get("/api/ops/external-health")
async def api_external_health(request: Request):
    """Live status of every external service the pipeline/trading flow depends
    on (Zerodha Kite, yfinance, Gemini, Marketaux, Supabase)."""
    if not is_authorized(request):
        return JSONResponse({"error": "Not authorized"}, status_code=403)

    import asyncio

    from dashboard.external_health import run_all_checks_sync

    try:
        checks = await asyncio.to_thread(run_all_checks_sync)
    except Exception as e:
        return JSONResponse(
            {"error": "External health checks failed", "detail": str(e)},
            status_code=500,
        )
    return {"checks": checks}


@router.get("/api/ops/health-runs/{run_id}/batch-debug")
async def api_batch_debug(
    request: Request,
    run_id: str,
    strategy: str,
    batch: str,
):
    """Drift debug payload for one LLM batch within an ingestion run."""
    if not is_authorized(request):
        return JSONResponse({"error": "Not authorized"}, status_code=403)
    from health_status import get_run_by_id

    try:
        run = get_run_by_id(run_id)
        payload = _batch_debug_from_run(run, strategy=strategy.strip().lower(), batch=batch.strip())
        if not payload:
            return JSONResponse({"error": "Batch not found for run"}, status_code=404)
        return payload
    except Exception as e:
        from db.connection import connection_hint

        return JSONResponse(
            {
                "error": "Database unavailable",
                "detail": str(e),
                "hint": connection_hint(e).strip() or None,
            },
            status_code=503,
        )


@router.get("/api/ops/health-runs/{run_id}/logs")
async def api_run_logs(
    request: Request,
    run_id: str,
    event: str | None = None,
    strategy: str | None = None,
    batch: str | None = None,
    limit: int = 120,
):
    """Filtered run logs persisted during morning ingestion."""
    if not is_authorized(request):
        return JSONResponse({"error": "Not authorized"}, status_code=403)
    from health_status import filter_run_logs, get_run_by_id

    try:
        run = get_run_by_id(run_id)
        if not run:
            return JSONResponse({"error": "Run not found"}, status_code=404)
        stages = run.get("stages") or {}
        run_logs = stages.get("run_logs") if isinstance(stages.get("run_logs"), dict) else {}
        entries = filter_run_logs(
            run_logs,
            event=event,
            strategy=strategy.strip().lower() if strategy else None,
            batch=batch.strip() if batch else None,
            limit=max(1, min(int(limit or 120), 400)),
        )
        return {
            "run_id": run_id,
            "run_started_at": run.get("started_at"),
            "count": len(entries),
            "truncated": bool(run_logs.get("truncated")),
            "entries": entries,
        }
    except Exception as e:
        from db.connection import connection_hint

        return JSONResponse(
            {
                "error": "Database unavailable",
                "detail": str(e),
                "hint": connection_hint(e).strip() or None,
            },
            status_code=503,
        )


@router.get("/api/ops/health-status")
async def api_health_status(request: Request, n: int = 5):
    """JSON API — authorized email only."""
    if not is_authorized(request):
        return JSONResponse({"error": "Not authorized"}, status_code=403)
    from health_status import get_recent_statuses, get_status
    from datetime import date
    from db.connection import connection_hint

    try:
        return {
            "today": get_status(date.today()),
            "recent": get_recent_statuses(n),
            "email": _session_email(request),
        }
    except Exception as e:
        return JSONResponse(
            {
                "error": "Database unavailable",
                "detail": str(e),
                "hint": connection_hint(e).strip() or None,
            },
            status_code=503,
        )


def install_session_middleware(app) -> None:
    """Call once when mounting the dashboard on the FastAPI app."""
    kwargs: dict[str, Any] = {
        "secret_key": session_secret(),
        "max_age": _SESSION_MAX_AGE,
        "same_site": _session_same_site(),
        "https_only": _cookie_secure(),
    }
    domain = _cookie_domain()
    if domain:
        kwargs["domain"] = domain
    app.add_middleware(SessionMiddleware, **kwargs)
