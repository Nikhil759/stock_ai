"""Tests for canonical auth host redirect behind Vercel/Railway proxy."""
from __future__ import annotations

import os

from starlette.requests import Request

from dashboard.auth_router import _canonical_redirect, _client_host


def _make_request(
    host: str,
    *,
    forwarded_host: str | None = None,
    vercel_id: str | None = None,
    path: str = "/health/login",
    query: str = "",
) -> Request:
    headers: list[tuple[bytes, bytes]] = [(b"host", host.encode())]
    if forwarded_host:
        headers.append((b"x-forwarded-host", forwarded_host.encode()))
    if vercel_id:
        headers.append((b"x-vercel-id", vercel_id.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": query.encode(),
        "headers": headers,
        "server": (host, 443),
        "client": ("127.0.0.1", 12345),
        "scheme": "https",
    }
    return Request(scope)


def test_client_host_prefers_forwarded_host() -> None:
    req = _make_request(
        "stockai-production-8023.up.railway.app",
        forwarded_host="www.wolfcapital.pro",
    )
    assert _client_host(req) == "www.wolfcapital.pro"


def test_no_redirect_when_proxied_via_vercel(monkeypatch) -> None:
    monkeypatch.setenv("FRONTEND_URL", "https://www.wolfcapital.pro")
    req = _make_request(
        "stockai-production-8023.up.railway.app",
        vercel_id="bom1::abc123",
    )
    assert _canonical_redirect(req) is None


def test_no_redirect_when_upstream_is_railway(monkeypatch) -> None:
    monkeypatch.setenv("FRONTEND_URL", "https://www.wolfcapital.pro")
    req = _make_request(
        "stockai-production-8023.up.railway.app",
        forwarded_host="www.wolfcapital.pro",
    )
    assert _canonical_redirect(req) is None


def test_redirect_direct_apex_browser_host(monkeypatch) -> None:
    monkeypatch.setenv("FRONTEND_URL", "https://www.wolfcapital.pro")
    req = _make_request("wolfcapital.pro")
    resp = _canonical_redirect(req)
    assert resp is not None
    assert resp.headers["location"] == "https://www.wolfcapital.pro/health/login"


def test_no_redirect_without_frontend_url(monkeypatch) -> None:
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    req = _make_request("stockai-production-8023.up.railway.app")
    assert _canonical_redirect(req) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            if fn.__code__.co_argcount == 1 and "monkeypatch" in fn.__code__.co_varnames:
                class _Patch:
                    def setenv(self, k, v):
                        os.environ[k] = v

                    def delenv(self, k, raising=False):
                        os.environ.pop(k, None)

                fn(_Patch())
            else:
                fn()
    print("auth redirect tests ok")
