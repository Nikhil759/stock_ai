"""
Thin Gemini wrapper (google-genai unified SDK -- backend/llm.py's
google.generativeai is EOL, see data_layer's own SDK-choice note; this is a
fresh package so it starts on the maintained SDK instead).

Handles: API key/env loading, the skeleton + strategy-lens prompt files,
per-strategy context caching (with a hard fallback when the content is too
small to cache), and a schema-guaranteed structured-output call.

HONEST CAVEAT: Gemini's explicit context caching has a hard minimum content
size (~1024 tokens for gemini-2.5-flash, confirmed live). The scoring
skeleton + one strategy lens is ~900-1000 tokens -- under that floor. Caching
is still attempted here (so it activates automatically if the skeleton grows
later, e.g. more rules or few-shot examples), but today it will always fail
its one-time creation attempt and fall back to sending the skeleton+lens as
a plain system_instruction on every call. That's strictly correct, just not
cost-discounted -- there is currently no cheaper way to run this prompt.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types

from ..config import ROOT, GEMINI_MODEL

load_dotenv(ROOT / ".env")

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_client: genai.Client | None = None
_client_lock = threading.Lock()

_cache_lock = threading.Lock()
_cache_registry: dict[str, str | None] = {}   # strategy -> cache resource name, or None (fallback)
_skeleton_cache: str | None = None
_strategy_block_cache: dict[str, str] = {}


def get_client() -> genai.Client:
    global _client
    with _client_lock:
        if _client is None:
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if not api_key:
                raise RuntimeError("GEMINI_API_KEY not set in .env or environment")
            log.debug("initializing Gemini client (model=%s)", GEMINI_MODEL)
            _client = genai.Client(api_key=api_key)
        return _client


def load_skeleton() -> str:
    global _skeleton_cache
    if _skeleton_cache is None:
        _skeleton_cache = (PROMPTS_DIR / "scoring_skeleton.txt").read_text()
    return _skeleton_cache


def load_strategy_block(strategy: str) -> str:
    if strategy not in _strategy_block_cache:
        path = PROMPTS_DIR / f"strategy_{strategy}.txt"
        _strategy_block_cache[strategy] = path.read_text()
    return _strategy_block_cache[strategy]


def _get_cached_content(strategy: str) -> str | None:
    """Try once per strategy to create an explicit context cache for the
    skeleton + that strategy's lens. Returns the cache resource name, or
    None if caching isn't usable (too small, API error, etc) -- callers
    must fall back to a plain system_instruction in that case."""
    with _cache_lock:
        if strategy in _cache_registry:
            return _cache_registry[strategy]

        combined = load_skeleton() + "\n\n" + load_strategy_block(strategy)
        log.debug("attempting context cache for %r (%d chars, ~%d tokens est.)",
                  strategy, len(combined), len(combined) // 4)
        try:
            cache = get_client().caches.create(
                model=GEMINI_MODEL,
                config=types.CreateCachedContentConfig(
                    display_name=f"selector-{strategy}",
                    system_instruction=combined,
                    ttl="3600s",
                ),
            )
            _cache_registry[strategy] = cache.name
            log.info("context cache created for %r: %s", strategy, cache.name)
        except Exception as e:
            log.info("context cache unavailable for %r (%s); "
                     "falling back to a plain system_instruction on every call", strategy, e)
            _cache_registry[strategy] = None
        return _cache_registry[strategy]


def generate_structured(strategy: str, user_content: str, response_schema: type):
    """One schema-guaranteed Gemini call: skeleton + strategy lens (cached
    when possible) as the system context, `user_content` as the only user
    turn. Returns the raw SDK response; `.parsed` is a `response_schema`
    instance when the model's output validated cleanly, else None."""
    client = get_client()
    cached_name = _get_cached_content(strategy)

    if cached_name:
        config = types.GenerateContentConfig(
            cached_content=cached_name,
            response_mime_type="application/json",
            response_schema=response_schema,
        )
    else:
        combined = load_skeleton() + "\n\n" + load_strategy_block(strategy)
        config = types.GenerateContentConfig(
            system_instruction=combined,
            response_mime_type="application/json",
            response_schema=response_schema,
        )

    log.debug("-> generate_content strategy=%s cache=%s user_content=%d chars",
              strategy, "hit" if cached_name else "miss", len(user_content))
    t0 = time.monotonic()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_content,
        config=config,
    )
    elapsed = time.monotonic() - t0
    usage = getattr(response, "usage_metadata", None)
    log.debug("<- generate_content strategy=%s took=%.2fs tokens(prompt=%s, cached=%s, response=%s) raw=%s",
              strategy, elapsed,
              getattr(usage, "prompt_token_count", "?"),
              getattr(usage, "cached_content_token_count", "?"),
              getattr(usage, "candidates_token_count", "?"),
              (response.text or "")[:500])
    return response


_final_prompt_cache: str | None = None
_daily_wolf_prompt_cache: str | None = None


def load_final_selection_prompt() -> str:
    global _final_prompt_cache
    if _final_prompt_cache is None:
        _final_prompt_cache = (PROMPTS_DIR / "final_selection.txt").read_text()
    return _final_prompt_cache


def load_daily_wolf_prompt() -> str:
    global _daily_wolf_prompt_cache
    if _daily_wolf_prompt_cache is None:
        _daily_wolf_prompt_cache = (PROMPTS_DIR / "daily_wolf_selection.txt").read_text()
    return _daily_wolf_prompt_cache


def generate_final(user_content: str, response_schema: type, *, wolf_mode: bool = False):
    """Structured call for portfolio / daily-Wolf intention selection."""
    client = get_client()
    system_instruction = load_daily_wolf_prompt() if wolf_mode else load_final_selection_prompt()
    label = "daily wolf" if wolf_mode else "final selection"
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_schema=response_schema,
    )
    log.debug("-> generate_content [%s] user_content=%d chars", label, len(user_content))
    log.debug("%s input payload:\n%s", label, user_content)
    t0 = time.monotonic()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_content,
        config=config,
    )
    elapsed = time.monotonic() - t0
    log.debug("<- generate_content [%s] took=%.2fs raw=%s", label, elapsed, response.text)
    return response
