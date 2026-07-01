"""Anonymous workspace IDs — no login, scoped by X-Workspace-Id header."""

from __future__ import annotations

import re
import uuid

# Existing single-tenant bots (pre-workspace deploy) are assigned here.
LEGACY_WORKSPACE_ID = "ws-default-legacy"

_WS_UUID = re.compile(
    r"^ws-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def is_valid_workspace_id(workspace_id: str) -> bool:
    if not workspace_id or len(workspace_id) > 64:
        return False
    if workspace_id == LEGACY_WORKSPACE_ID:
        return True
    return bool(_WS_UUID.match(workspace_id.strip()))


def normalize_workspace_id(raw: str) -> str:
    return (raw or "").strip()


def new_workspace_id() -> str:
    return f"ws-{uuid.uuid4()}"
