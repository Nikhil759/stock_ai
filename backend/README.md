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
   `health_status` upserts (`cron/morning_ingestion.run_pipeline`). A second
   job at `30 10 * * 1-5` UTC (~4:00 PM IST) runs post-close dossier refresh
   only (`--close --skip-news`, no scoring). Manual dossier-only rebuild is
   still available via `POST /api/build`; post-close via `POST /api/build-close`.

### `stock_ai` web service — Phase E/F variables

The Trading UI now also serves the `/health` ops dashboard (Supabase Google
OAuth, PKCE). Add these on the `stock_ai` service:

- `SUPABASE_DATABASE_URL` — reads `health_status` for the dashboard
- `SUPABASE_PROJECT_URL` (or `SUPABASE_URL`) + `SUPABASE_ANON_KEY` — OAuth
- `AUTHORIZED_EMAIL` — the only email allowed to view `/health`
- `APP_REDIRECT_URL` — OAuth callback URL. With a Vercel frontend, use
  `https://<vercel-app>/health/auth/callback` (Vercel proxies it to Railway).
  Add the same URL in Supabase Auth → Redirect URLs.
- `DASHBOARD_SESSION_SECRET` — any random string
- `FRONTEND_URL` — your Vercel app URL (e.g. `https://wolf-capital.vercel.app`).
  Used for post-login redirect. If `APP_REDIRECT_URL` is unset, the OAuth
  callback defaults to `FRONTEND_URL/health/auth/callback`.
- `KITE_API_KEY`, `KITE_API_SECRET` — live Zerodha LTPs (token synced from your Mac;
  see **Kite token sync** below). Do **not** put TOTP creds on Railway — Zerodha
  blocks login from cloud IPs.

**In-process APScheduler on `stock_ai`** (`fund_scheduler.py`, UTC cron expressions):

| Job | Default cron | IST (approx.) |
|-----|--------------|---------------|
| Fund selector | `30 3 * * 1-5` | 9:00 AM weekdays |
| Morning deploy | `45 3 * * 1-5` | 9:15 AM weekdays |
| Supabase evening auto-exit | `30 11 * * 1-5` | 5:00 PM weekdays |
| Supabase daily fund manager | `25 3 * * 1-5` | 8:55 AM weekdays |

Override with `FUND_SELECTOR_CRON`, `FUND_MORNING_CRON`, `WOLF_EVENING_CRON`, `WOLF_DAILY_CRON`.
SQLite fund scheduler auto-enables on Railway when
`WOLF_ENABLE_SQLITE_CRON=1`. Supabase evening and daily schedulers auto-enable on
Railway (`RAILWAY_ENVIRONMENT`); set `WOLF_EVENING_SCHEDULER_ENABLED=1` or
`WOLF_DAILY_SCHEDULER_ENABLED=1` locally to test. Manual runs:
`python -m scripts.run_evening_all_supabase_wolves`,
`python -m scripts.run_daily_review_all_supabase_wolves` (from `backend/` with
`PYTHONPATH=.:backend` from repo root).

Apply `fund_manager_health/schema.sql` in Supabase once for per-wolf health rows on `/health`.

### Kite token sync (local Mac → Supabase → Railway)

Zerodha blocks TOTP auto-login from Railway. Refresh on your Mac and upsert to
`kite_auth_tokens` in Supabase; `stock_ai` reads it for live LTPs.

**Prerequisites:** `kite_auth_tokens` table exists (see `db/schema.sql`),
`AUTHORIZED_EMAIL` matches your Google login, `SUPABASE_DATABASE_URL` in repo
`.env`.

```bash
# One-off test (from backend/)
python -m scripts.refresh_kite_token --sync
# or via the launchd wrapper:
./scripts/kite_token_sync_job.sh
```

**launchd (recommended on macOS)** — handles login + wake-after-sleep catch-up:

```bash
chmod +x backend/scripts/kite_token_sync_job.sh
# Edit REPO path in kite_token_sync_job.sh if needed, then:
cp backend/scripts/com.wolfcapital.kite-token-sync.plist ~/Library/LaunchAgents/
cp backend/scripts/com.wolfcapital.kite-token-catchup.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.wolfcapital.kite-token-sync.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.wolfcapital.kite-token-catchup.plist
```

- `kite-token-sync` — 6:05 AM IST weekdays + on login (`RunAtLoad`)
- `kite-token-catchup` — every 30 min on weekday mornings (6–11 AM IST) if the Mac
  was asleep at 6:05

Logs: `/tmp/wolfcapital-kite-token.log`

**cron alternative** (if you prefer crontab):

```cron
35 0 * * 1-5 cd /path/to/stock_ai/backend && /path/to/.venv/bin/python -m scripts.refresh_kite_token --sync
```

Repo `.env` needs `KITE_USER_ID`, `KITE_PASSWORD`, `KITE_TOTP_SECRET` (TOTP only
on your Mac). Railway `stock_ai` only needs `KITE_API_KEY` + `KITE_API_SECRET`.

**Vercel:** set `RAILWAY_PUBLIC_URL` to your Railway API host. The Vercel build
generates rewrites that proxy `/health/*` and `/api/ops/*` to Railway so login
cookies stay on the Vercel domain (first-party).

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
