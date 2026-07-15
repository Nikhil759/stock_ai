"""Gemini structured-output calls for Wolf Brain."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from google.genai import types

from selector.config import GEMINI_MODEL
from selector.llm.client import get_client

log = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_deploy_prompt: str | None = None
_daily_prompt: str | None = None


def load_deploy_prompt() -> str:
    global _deploy_prompt
    if _deploy_prompt is None:
        _deploy_prompt = (_PROMPTS_DIR / "deploy.txt").read_text(encoding="utf-8")
    return _deploy_prompt


def load_daily_review_prompt() -> str:
    global _daily_prompt
    if _daily_prompt is None:
        _daily_prompt = (_PROMPTS_DIR / "daily_review.txt").read_text(encoding="utf-8")
    return _daily_prompt


def generate_brain(
    *,
    mode: str,
    user_content: str,
    response_schema: type,
):
    """One structured Gemini call for deploy or daily_review."""
    system = (
        load_deploy_prompt() if mode == "deploy" else load_daily_review_prompt()
    )
    client = get_client()
    config = types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=response_schema,
    )
    log.debug("[WOLF BRAIN] -> generate_content mode=%s chars=%d", mode, len(user_content))
    t0 = time.monotonic()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_content,
        config=config,
    )
    elapsed = time.monotonic() - t0
    log.info("[WOLF BRAIN] Gemini %s returned in %.1fs", mode, elapsed)
    log.debug("[WOLF BRAIN] raw=%s", (response.text or "")[:800])
    return response
