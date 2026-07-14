"""
Phase F — FastAPI health dashboard (HTML) with Supabase Google OAuth (PKCE).

No Streamlit. Mounted under /health on the main FastAPI app.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import base64
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

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


def redirect_url() -> str:
    return (
        os.getenv("APP_REDIRECT_URL")
        or "http://127.0.0.1:8000/health/auth/callback"
    ).strip()


def session_secret() -> str:
    return (
        os.getenv("DASHBOARD_SESSION_SECRET")
        or os.getenv("SUPABASE_ANON_KEY")
        or "dev-insecure-session-secret"
    )


def _normalize_origin(raw: str) -> str:
    val = raw.strip().rstrip("/")
    if not val:
        return ""
    if not val.startswith(("http://", "https://")):
        val = "https://" + val
    return val


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


def is_authorized(request: Request) -> bool:
    allowed = authorized_email()
    if not allowed:
        return False
    return _session_email(request) == allowed


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
    if not cache:
        rows.append(
            {
                "id": "cache_saved",
                "label": "Shortlist cache",
                "status": None,
                "chip": "idle",
                "detail": "not started",
            }
        )
    else:
        parts = [f"{k}={'yes' if v else 'no'}" for k, v in cache.items()]
        ok = all(bool(v) for v in cache.values()) if cache else False
        rows.append(
            {
                "id": "cache_saved",
                "label": "Shortlist cache",
                "status": "success" if ok else "partial",
                "chip": "ok" if ok else "warn",
                "detail": ", ".join(parts),
            }
        )

    return rows


@router.get("/health", response_class=HTMLResponse)
async def health_page(request: Request):
    """Health Check page — gated; shows Not authorized with no data if denied."""
    authed = is_authorized(request)
    email = _session_email(request)
    allowed_configured = bool(authorized_email())

    recent = []
    today_row = None
    today_rows: list[dict] = []
    if authed:
        from health_status import get_recent_statuses, get_status
        from datetime import date

        recent_raw = get_recent_statuses(5)
        for r in recent_raw:
            stages = r.get("stages") or {}
            recent.append(
                {
                    "date": r.get("date"),
                    "overall": r.get("overall_status") or "unknown",
                    "overall_chip": _stage_chip(r.get("overall_status")),
                    "stages": _flatten_stages(stages),
                }
            )
        today_row = get_status(date.today())
        today_rows = _flatten_stages((today_row or {}).get("stages") or {})

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
            "not_started": authed and today_row is None,
        },
    )


@router.get("/health/login")
async def health_login(request: Request, return_to: str | None = None):
    """Start Google OAuth via Supabase Auth (PKCE)."""
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
    return RedirectResponse(url)


@router.get("/health/auth/callback")
async def health_auth_callback(request: Request, code: str | None = None):
    """Exchange ?code= for a session (PKCE) and store user email."""
    if not code:
        return HTMLResponse(
            "<p>Missing auth code. <a href='/health'>Back</a></p>",
            status_code=400,
        )

    verifier = request.session.get("pkce_verifier")
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
    request.session["access_token"] = data.get("access_token")
    return RedirectResponse(_post_auth_redirect(request), status_code=303)


@router.get("/health/logout")
async def health_logout(request: Request, return_to: str | None = None):
    dest = _safe_return_url(return_to) or _default_app_url()
    request.session.clear()
    return RedirectResponse(dest, status_code=303)


@router.get("/api/ops/me")
async def api_ops_me(request: Request):
    """Current session identity for the Trading UI header (cookies required)."""
    email = _session_email(request)
    return {
        "email": email,
        "authorized": is_authorized(request),
        "logged_in": bool(email),
    }


@router.get("/api/ops/health-status")
async def api_health_status(request: Request, n: int = 5):
    """JSON API — authorized email only."""
    if not is_authorized(request):
        return JSONResponse({"error": "Not authorized"}, status_code=403)
    from health_status import get_recent_statuses, get_status
    from datetime import date

    return {
        "today": get_status(date.today()),
        "recent": get_recent_statuses(n),
        "email": _session_email(request),
    }


def install_session_middleware(app) -> None:
    """Call once when mounting the dashboard on the FastAPI app."""
    # Cross-site session cookies (Vercel UI -> Railway API) need SameSite=None.
    is_prod = bool(os.getenv("RAILWAY_ENVIRONMENT"))
    kwargs: dict[str, Any] = {"secret_key": session_secret()}
    if is_prod:
        kwargs["same_site"] = "none"
        kwargs["https_only"] = True
    app.add_middleware(SessionMiddleware, **kwargs)
