# Backend (Wolf Capital API)

FastAPI web service for the paper-trading bot. Deployed separately from the
`data-layer-cron` Railway service.

## Local dev

```bash
# 1. Build dossiers first (repo root — needs network for yfinance)
cd ..
python -m data_layer.build

# 2. Start API
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Deploy / screen uses **dossier-powered** selection (`selector` funnel + LLM on
full dossier JSON), not live yfinance screeners. Requires `dossiers/` at the
repo root (200 JSON files) and `GEMINI_API_KEY` in `../.env`.

Quick validation without the UI:

```bash
python -m selector.funnel value          # Phase 1 only
python -m selector.run value             # full pipeline → intentions/
cd backend && python -c "from dossier_screen import screen; print(screen('value', 100000, use_llm=True)['candidates'])"
```

Open http://localhost:8000/app

## Railway deploy (`stock_ai` web service)

The repo has **two** Railway services sharing one GitHub repo:

| Service | Root Directory | Config file | Start command |
|---------|----------------|-------------|---------------|
| `data-layer-cron` | `/` (repo root) | `railway.json` | `python -m data_layer.build` + cron |
| `stock_ai` (web API) | **`backend`** | `backend/railway.toml` | `uvicorn main:app --host 0.0.0.0 --port $PORT` |

**Critical:** the `stock_ai` service must use Root Directory **`backend`**. If it
points at the repo root, Railway applies `railway.json` (the cron job) and the
public URL returns 502 because no web server is running.

### One-time Railway setup for `stock_ai`

1. Service → **Settings** → **Source** → Root Directory: `backend`
2. **Variables:** `GEMINI_API_KEY` (required for LLM screening)
3. **Networking:** public URL enabled (e.g. `stockai-production-….up.railway.app`)
4. **Remove cron schedule** on this service if present (cron belongs on `data-layer-cron` only)
5. Redeploy

### Verify

```bash
curl -i "https://YOUR-RAILWAY-URL.up.railway.app/api/health"
# expect HTTP/2 200 and {"status":"ok"}
```

Point Vercel's `RAILWAY_PUBLIC_URL` at this URL and redeploy the frontend.

**Dossiers on Railway:** screening reads `dossiers/` from the repo-root
`data_layer` path. The web service must see the same dossiers the cron builds
(shared volume mount at repo root, or run `data_layer.build` in-process).
Until that is wired, production deploy may return "No dossiers found".

Strategy knowledge markdown in `backend/knowledge/` is legacy; live screening
uses `selector/prompts/` with full dossier JSON.
