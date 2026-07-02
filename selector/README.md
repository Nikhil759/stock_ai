# Selector

Turns the strategy-neutral dossiers built by `data_layer` into a daily
trading intentions file for **one fixed strategy** (`value`, `winners`,
`box`, or `dip`). One bot instance = one strategy = one intentions file per
day. No execution/order-placement logic lives here — this package only
decides *what it would buy*.

## Layout
```
selector/
  config.py             # budget, per-stock cap, funnel cap, Gemini model
  funnel.py             # Phase 1 — deterministic math filter, run this alone to sanity-check a strategy
  strategies/
    value.py            # coarse numeric checks per strategy (no LLM)
    winners.py
    box.py
    dip.py
  prompts/               # verbatim prompt text, ported from llm_prompts.md
    scoring_skeleton.txt      # shared per-stock scoring rules
    strategy_value.txt        # strategy-specific lens, appended to the skeleton
    strategy_winners.txt
    strategy_box.txt
    strategy_dip.txt
    final_selection.txt       # portfolio-decision prompt (Phase 3)
  schemas.py             # StockVerdict (Phase 2), Pick/SkippedEntry/FinalPicks (Phase 3)
  llm/
    client.py            # google-genai wrapper: structured output + context caching (graceful fallback)
    scoring.py            # Phase 2 — score_stock / score_all (parallel, per-survivor)
    final.py              # Phase 3 — select_final (rank, allocate, clamp/validate)
  log_setup.py           # logging.getLogger(__name__) everywhere; this wires console + logs/ file handlers
  run.py                 # orchestrator — funnel -> score_all -> select_final -> intentions/<strategy>_<date>.json
intentions/              # output, one file per strategy per day (gitignored)
logs/                    # <strategy>_<date>.log — full DEBUG trace of a run (gitignored)
```

## Run
```
python -m selector.funnel value             # Phase 1 only — see which tickers survive the math filter, no LLM calls
python -m selector.llm.scoring dip --limit 5 # Phase 2 only — funnel + per-stock LLM verdicts, no final allocation
python -m selector.run value                 # full pipeline — writes intentions/value_<date>.json
python -m selector.run value --verbose       # same, but also prints DEBUG detail to the console
```

## Logging
Every module logs through the standard `logging` module (`logging.getLogger(__name__)`,
loggers named `selector.funnel`, `selector.llm.scoring`, `selector.llm.client`,
`selector.llm.final`, `selector.run`). `log_setup.setup_logging()` is called
once at the top of each `__main__` entry point and wires two handlers:

- **Console** — INFO by default: funnel summary + each survivor's checks,
  each stock's verdict (`decision`, `conviction`, buy/stop/target, timing),
  final selection's input/output and every clamp/drop, phase timings. Pass
  `--verbose`/`-v` to bump the console to DEBUG too.
- **File** (`logs/<strategy>_<date>.log`) — always full DEBUG, regardless of
  `--verbose`. This is the one to check after a local run if you want to
  verify *why* a specific ticker was accepted/rejected: it has every
  dossier's per-check pass/fail from the funnel, the raw LLM request/response
  JSON for every call (prompt/response token counts, latency), each
  verdict's full thesis/signals/risks, and the final allocation's
  validation trail (raw LLM picks before clamping, and the reasoning for
  every clamp or drop).

Example: to see exactly why a ticker didn't make the funnel for `dip`,
run `python -m selector.run dip` then grep the log:
`grep RELIANCE logs/dip_<date>.log`.

## Phases
- **1 — Math funnel** (`funnel.py` + `strategies/*.py`): deterministic,
  free, no LLM calls. Loads every dossier, applies the strategy's coarse
  numeric/qualitative checks, ranks survivors by how many checks they
  passed, caps the list at `config.FUNNEL_MAX_SURVIVORS` (30). This exists
  purely to keep the LLM bill bounded — the checks here are intentionally
  loose; the real judgment happens in Phase 2.
- **2 — Per-stock scoring** (`llm/scoring.py`, `llm/client.py`): one Gemini
  call per surviving ticker (parallelized), lensed by the strategy-specific
  prompt appended to the shared scoring skeleton. Returns a `StockVerdict`
  (`buy` / `watch` / `skip`, conviction, buy/stop/target prices, thesis,
  risks, signals). Any LLM failure, schema mismatch, ticker mismatch, or
  nonsensical price defaults to a `skip` verdict rather than crashing the
  run — a bad LLM call should never block the other survivors.
- **3 — Final selection** (`llm/final.py`, `run.py`): one Gemini call per
  strategy per day. Takes every `buy`/`watch` verdict from Phase 2 plus the
  account state (budget, cash, per-stock cap), ranks head-to-head, and
  allocates whole shares across 1-3 picks — holding cash is a valid
  outcome. Prices are carried through unchanged from Phase 2, never
  re-invented. `run.py` then re-validates the LLM's own arithmetic
  (per-stock cap, total cash) and clamps/drops anything that slipped
  through, logging every correction, before writing the intentions file.

Each phase is independently runnable and each leaves the pipeline in a
valid state — an empty funnel or an all-skip scoring pass still produces a
clean intentions file with `picks: []` and cash held.

## Intentions file shape
```json
{
  "strategy": "value",
  "date": "2026-07-03",
  "budget": 10000,
  "market_context": { "...shared backdrop, copied from any dossier..." },
  "result": {
    "picks": [
      {"ticker": "PFC", "buy_price": 430.05, "stop_loss": 385.0, "sell_target": 750.0,
       "allocation_inr": 3870.45, "shares": 9, "conviction": 88, "rationale": "..."}
    ],
    "skipped": [{"ticker": "TCS", "reason": "per-stock decision: skip"}],
    "cash_held_inr": 133.6,
    "portfolio_note": "..."
  }
}
```
`sum(picks[].allocation_inr) + cash_held_inr` always equals `budget` exactly
— `llm/final.py` clamps/drops picks until that holds even if the model's
own arithmetic doesn't.

## Notes
- Needs `data_layer` dossiers already built (`python -m data_layer.build`)
  — this package never fetches data or modifies `data_layer`.
- Needs `GEMINI_API_KEY` in the repo-root `.env` for Phases 2 and 3.
- Requires: `google-genai pydantic python-dotenv` (see `requirements.txt`).
- Context caching (Gemini) is attempted per strategy in `llm/client.py`,
  but today's skeleton+strategy prompts (~900 tokens) sit just under
  Gemini's 1024-token minimum for explicit caching, so it logs a warning
  once per strategy and falls back to a plain `system_instruction` on every
  call — functionally identical, just without the small caching discount.
  If the prompts grow past ~1024 tokens later, caching activates
  automatically with no code change needed.
- `config.TEST_BUDGET` / `PER_STOCK_CAP_PCT` are placeholders for real
  account state — `run.py` builds a synthetic `account` dict with no open
  positions today; wiring in real broker balances/holdings is future work.
