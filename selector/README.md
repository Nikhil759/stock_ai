# Selector — shared LLM assets

Production screening runs on **`data-layer-cron`**:

```
cron/morning_ingestion.py → funnels/* → scoring/batch_scorer.py → shortlist cache
```

Deploy-time selection uses **`wolf_brain`** (reads the shortlist cache + dossiers).

This package holds shared Gemini client code and prompt templates:

| Path | Used by |
|------|---------|
| `selector/llm/client.py` | `wolf_brain/gemini.py` |
| `selector/config.py` | `wolf_brain/gemini.py` |
| `selector/prompts/*.txt` | `scoring/batch_scorer.py`, `wolf_brain` |

Prompt files:

- `strategy_*.txt` — batch scoring lenses (Phase D)
- `scoring_skeleton.txt` — shared scoring skeleton
- `daily_wolf_selection.txt` — wolf_brain deploy prompt
