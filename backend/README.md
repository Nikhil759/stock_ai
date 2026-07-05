# Backend (Wolf Capital API)

FastAPI web service for the paper-trading bot. Deployed separately from the
`data-layer-cron` Railway service.

## Local dev

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
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

Strategy knowledge markdown lives in `backend/knowledge/` so the web service
does not need the full monorepo root at runtime.
