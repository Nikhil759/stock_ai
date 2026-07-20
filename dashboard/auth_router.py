"""
Phase F — FastAPI health dashboard (HTML) with Supabase Google OAuth (PKCE).

No Streamlit. Mounted under /health on the main FastAPI app.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import base64
from datetime import datetime
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


def _canonical_redirect(request: Request) -> RedirectResponse | None:
    """Force auth on FRONTEND_URL host so apex/www and PWA share cookies."""
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
    db_error: str | None = None
    fm_today: list[dict] = []
    fm_recent: list[dict] = []
    fm_not_started = False
    fm_error: str | None = None
    if authed:
        from datetime import date

        try:
            from health_status import get_recent_statuses, get_status

            recent_raw = get_recent_statuses(5)
            for r in recent_raw:
                stages = r.get("stages") or {}
                recent.append(
                    {
                        "date": r.get("date"),
                        "run_at": _format_run_time(r.get("started_at")),
                        "overall": r.get("overall_status") or "unknown",
                        "overall_chip": _stage_chip(r.get("overall_status")),
                        "stages": _flatten_stages(stages),
                    }
                )
            today_row = get_status(date.today())
            today_stages = (today_row or {}).get("stages") or {}
            today_rows = _flatten_stages(today_stages)
            shortlists = _resolve_shortlists_for_health(today_stages)
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
