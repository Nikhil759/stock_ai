"""
Thin Gemini wrapper (google-genai unified SDK).

Handles: API key/env loading, prompt assembly, per-strategy context caching
(with a hard fallback when the content is too small to cache), and a
schema-guaranteed structured-output call.

Gemini explicit context caching requires a minimum cached prefix (~1024 tokens
for gemini-2.5-flash). Batch scoring appends a static reference block
(batch_cache_pad.txt) so skeleton + strategy + daily market context clears
that floor for every strategy lens.
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
_SCORING_DIR = Path(__file__).resolve().parents[2] / "scoring"
BATCH_SKELETON_PATH = _SCORING_DIR / "batch_skeleton.txt"
BATCH_CACHE_PAD_PATH = _SCORING_DIR / "batch_cache_pad.txt"
# API-enforced floor for gemini-2.5-flash explicit caches (chars/4 estimate).
BATCH_CACHE_MIN_EST_TOKENS = int(os.getenv("LLM_CACHE_MIN_TOKENS", "1024"))

_client: genai.Client | None = None
_client_lock = threading.Lock()

_cache_lock = threading.Lock()
_cache_registry: dict[str, str | None] = {}   # strategy -> cache resource name, or None (fallback)
_batch_scoring_cache: dict[tuple[str, str], str | None] = {}
_skeleton_cache: str | None = None
_batch_skeleton_cache: str | None = None
_batch_cache_pad: str | None = None
_strategy_block_cache: dict[str, str] = {}

LLM_USE_CONTEXT_CACHE = os.getenv("LLM_USE_CONTEXT_CACHE", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)


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


def load_batch_skeleton() -> str:
    global _batch_skeleton_cache
    if _batch_skeleton_cache is None:
        _batch_skeleton_cache = BATCH_SKELETON_PATH.read_text(encoding="utf-8")
    return _batch_skeleton_cache


def load_batch_cache_pad() -> str:
    """Static reference block appended to explicit cache prefix (clears min-token floor)."""
    global _batch_cache_pad
    if _batch_cache_pad is None:
        _batch_cache_pad = BATCH_CACHE_PAD_PATH.read_text(encoding="utf-8").strip()
    return _batch_cache_pad


def estimate_batch_cache_tokens(text: str) -> int:
    return max(0, len(text) // 4)


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


def _market_context_cache_key(shared_market_context: dict | None) -> str:
    import json

    mc = shared_market_context or {}
    return json.dumps(mc, sort_keys=True, separators=(",", ":"), default=str)


def batch_scoring_system_base(strategy: str) -> str:
    """Skeleton + strategy lens only (no daily market context)."""
    return load_batch_skeleton() + "\n\n" + load_strategy_block(strategy)


def batch_scoring_cache_instruction(
    strategy: str,
    shared_market_context: dict | None = None,
) -> str:
    """System instruction stored in Gemini context cache (includes daily MC + pad)."""
    import json

    combined = batch_scoring_system_base(strategy)
    mc = shared_market_context or {}
    if mc:
        combined += (
            "\n\nTODAY'S MARKET CONTEXT (same for all stocks in each batch):\n"
            + json.dumps(mc, separators=(",", ":"), default=str)
        )
    pad = load_batch_cache_pad()
    if pad:
        combined += "\n\n" + pad
    est = estimate_batch_cache_tokens(combined)
    if est < BATCH_CACHE_MIN_EST_TOKENS:
        log.warning(
            "batch scoring cache instruction for %r still below min est tokens "
            "(%d < %d) after pad — explicit cache may fail",
            strategy,
            est,
            BATCH_CACHE_MIN_EST_TOKENS,
        )
    return combined


def get_or_create_batch_scoring_cache(
    strategy: str,
    shared_market_context: dict | None = None,
    *,
    ttl: str = "3600s",
) -> str | None:
    """Explicit Gemini context cache for batch scoring prefix."""
    if not LLM_USE_CONTEXT_CACHE:
        return None

    mc_key = _market_context_cache_key(shared_market_context)
    registry_key = (strategy, mc_key)
    with _cache_lock:
        if registry_key in _batch_scoring_cache:
            return _batch_scoring_cache[registry_key]

        combined = batch_scoring_cache_instruction(strategy, shared_market_context)
        log.debug(
            "attempting batch scoring context cache for %r (%d chars, ~%d tokens est.)",
            strategy,
            len(combined),
            len(combined) // 4,
        )
        try:
            cache = get_client().caches.create(
                model=GEMINI_MODEL,
                config=types.CreateCachedContentConfig(
                    display_name=f"batch-scoring-{strategy}",
                    system_instruction=combined,
                    ttl=ttl,
                ),
            )
            _batch_scoring_cache[registry_key] = cache.name
            log.info("batch scoring context cache created for %r: %s", strategy, cache.name)
        except Exception as e:
            log.info(
                "batch scoring context cache unavailable for %r (%s); "
                "falling back to inline system_instruction",
                strategy,
                e,
            )
            _batch_scoring_cache[registry_key] = None
        return _batch_scoring_cache[registry_key]


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


def generate_batch_scoring(
    strategy: str,
    user_content: str,
    response_schema: type,
    *,
    cache_name: str | None = None,
):
    """Batch scoring call — batch skeleton + strategy lens (+ daily MC when cached)."""
    client = get_client()
    if cache_name:
        config = types.GenerateContentConfig(
            cached_content=cache_name,
            response_mime_type="application/json",
            response_schema=response_schema,
        )
    else:
        config = types.GenerateContentConfig(
            system_instruction=batch_scoring_system_base(strategy),
            response_mime_type="application/json",
            response_schema=response_schema,
        )

    log.debug(
        "-> generate_content batch_scoring strategy=%s cache=%s user_content=%d chars",
        strategy,
        "hit" if cache_name else "miss",
        len(user_content),
    )
    t0 = time.monotonic()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_content,
        config=config,
    )
    elapsed = time.monotonic() - t0
    usage = getattr(response, "usage_metadata", None)
    log.debug(
        "<- generate_content batch_scoring strategy=%s took=%.2fs tokens(prompt=%s, cached=%s, response=%s)",
        strategy,
        elapsed,
        getattr(usage, "prompt_token_count", "?"),
        getattr(usage, "cached_content_token_count", "?"),
        getattr(usage, "candidates_token_count", "?"),
    )
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
