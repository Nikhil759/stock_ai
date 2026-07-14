"""Shared helpers for Phase C math funnels."""

from __future__ import annotations

from typing import Any, Callable

DROP_EXAMPLES = 3
MIN_SURVIVORS_WARN = 5


def as_dict(dossier: Any) -> dict:
    """Accept a Dossier dataclass or an already-loaded dict."""
    if isinstance(dossier, dict):
        return dossier
    if hasattr(dossier, "to_dict"):
        return dossier.to_dict()
    raise TypeError(f"Unsupported dossier type: {type(dossier)!r}")


def symbol_of(d: dict) -> str:
    meta = d.get("meta") or {}
    return str(meta.get("ticker") or d.get("symbol") or "?").upper()


def apply_step(
    strategy: str,
    label: str,
    rows: list[dict],
    keep: Callable[[dict], tuple[bool, str | None, dict | None]],
) -> list[dict]:
    """
    Filter `rows` with `keep(dossier) -> (ok, drop_reason, pass_facts)`.

    `pass_facts` is merged into the row's funnel_reasons when the stock passes.
    Logs before→after counts and up to DROP_EXAMPLES concrete drop reasons.
    """
    before = len(rows)
    kept: list[dict] = []
    drops: list[tuple[str, str]] = []

    for row in rows:
        d = row["dossier"]
        ok, drop_reason, pass_facts = keep(d)
        if ok:
            if pass_facts:
                row = {
                    **row,
                    "funnel_reasons": {**row.get("funnel_reasons", {}), **pass_facts},
                }
            kept.append(row)
        else:
            drops.append((symbol_of(d), drop_reason or "failed"))

    print(f"[MATH FUNNEL] {strategy}: {before} → {len(kept)} ({label})")
    for sym, reason in drops[:DROP_EXAMPLES]:
        print(f"[MATH FUNNEL] {strategy}: dropped {sym} — {reason}")
    if len(drops) > DROP_EXAMPLES:
        print(
            f"[MATH FUNNEL] {strategy}: … {len(drops) - DROP_EXAMPLES} more dropped"
        )
    return kept


def wrap_dossiers(dossiers: list) -> list[dict]:
    """Seed funnel rows from raw dossiers."""
    rows = []
    for raw in dossiers:
        d = as_dict(raw)
        rows.append(
            {
                "symbol": symbol_of(d),
                "dossier": d,
                "funnel_reasons": {},
            }
        )
    return rows


def finish(strategy: str, rows: list[dict]) -> list[dict]:
    n = len(rows)
    print(f"[MATH FUNNEL] {strategy}: survivors={n}")
    if n < MIN_SURVIVORS_WARN:
        print(
            f"[MATH FUNNEL] {strategy}: WARNING — only {n} survivors "
            f"(below {MIN_SURVIVORS_WARN}); expected on some days, worth noticing"
        )
    if rows:
        sample = rows[0]
        print(
            f"[MATH FUNNEL] {strategy}: sample survivor {sample['symbol']} "
            f"reasons={sample.get('funnel_reasons')}"
        )
    return rows
