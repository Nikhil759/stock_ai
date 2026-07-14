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
full dossier JSON). Requires `dossiers/` at the repo root and `GEMINI_API_KEY`
in `../.env`.

Open http://localhost:8000/app

## Railway deploy (`stock_ai` web service)

The repo has **two** Railway services sharing one GitHub repo:

| Service | Root Directory | Config file | Start command |
|---------|----------------|-------------|---------------|
| `data-layer-cron` | `/` (repo root) | `railway.json` | `uvicorn data_layer.serve:app …` |
| `stock_ai` (web API) | **`/` (repo root)** | **`railway.web.toml`** | `cd backend && uvicorn …` |

### Critical: web service must use repo root

Do **not** set Root Directory to `backend/` for `stock_ai`. The build
context would exclude `selector/` and `data_layer/`, and screening cannot
run.

### One-time Railway setup for `stock_ai`

1. Service → **Settings** → **Source** → Root Directory: **leave empty / `/`**
2. Service → **Settings** → **Config file**: **`railway.web.toml`**
3. **Remove cron schedule** on this service (build cron is on `data-layer-cron`)
4. **Variables:**
   - `GEMINI_API_KEY` — LLM screening
   - `DOSSIER_API_URL` — `http://data-layer-cron.railway.internal:<PORT>`
     (use the **PORT** from `data-layer-cron` → Variables → `PORT`, e.g. `8080`)
   - `DOSSIER_API_TOKEN` — same secret as on `data-layer-cron`
5. Redeploy

### One-time Railway setup for `data-layer-cron`

1. Root Directory: `/`
2. Config file: `railway.json`
3. Volume attached (dossiers + shortlist cache persist here)
4. **Variables:** `DOSSIER_API_TOKEN` (shared with `stock_ai`), `GEMINI_API_KEY`
   (Phase D batch scoring), `SUPABASE_DATABASE_URL` (Phase E health_status),
   plus `MARKETAUX_API_KEY` etc.
5. Start command (from `railway.json`): `uvicorn data_layer.serve:app …`
6. Scheduled job runs inside the API via APScheduler (`30 2 * * 1-5` UTC
   weekdays): dossier build → funnels → batch LLM scoring → shortlist cache →
   `health_status` upserts (`cron/morning_ingestion.run_pipeline`). Manual
   dossier-only rebuild is still available via `POST /api/build`.

### `stock_ai` web service — Phase E/F variables

The Trading UI now also serves the `/health` ops dashboard (Supabase Google
OAuth, PKCE). Add these on the `stock_ai` service:

- `SUPABASE_DATABASE_URL` — reads `health_status` for the dashboard
- `SUPABASE_PROJECT_URL` (or `SUPABASE_URL`) + `SUPABASE_ANON_KEY` — OAuth
- `AUTHORIZED_EMAIL` — the only email allowed to view `/health`
- `APP_REDIRECT_URL` — **must be the production callback URL**, e.g.
  `https://<your-app>.up.railway.app/health/auth/callback` (also add this
  exact URL as a Redirect URL in the Supabase Auth settings)
- `DASHBOARD_SESSION_SECRET` — any random string

Verify cron API: `GET /health` on the cron service (public URL) → `count: 200`.

### Dossier sync flow

```
data-layer-cron (volume)  --private network-->  stock_ai
  builds dossiers daily       GET /api/dossiers     syncs to dossier_cache/
  serves internal API         before each screen
```

No shared volume needed on `stock_ai`.

```bash
curl -i "https://YOUR-RAILWAY-URL.up.railway.app/api/health"
# expect HTTP/2 200 and {"status":"ok"}
```

Point Vercel's `RAILWAY_PUBLIC_URL` at this URL and redeploy the frontend.

Strategy knowledge markdown in `backend/knowledge/` is legacy; live screening
uses `selector/prompts/` with full dossier JSON.
