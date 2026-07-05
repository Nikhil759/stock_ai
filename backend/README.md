## Railway deploy (`stock_ai` web service)

The repo has **two** Railway services sharing one GitHub repo:

| Service | Root Directory | Config file | Start command |
|---------|----------------|-------------|---------------|
| `data-layer-cron` | `/` (repo root) | `railway.json` | `python -m data_layer.build` + cron |
| `stock_ai` (web API) | **`/` (repo root)** | **`railway.web.toml`** | `cd backend && uvicorn …` |

### Critical: web service must use repo root

Do **not** set Root Directory to `backend/` for `stock_ai`. The build
context would exclude `selector/` and `data_layer/`, and screening cannot
run.

### One-time Railway setup for `stock_ai`

1. Service → **Settings** → **Source** → Root Directory: **leave empty / `/`**
2. Service → **Settings** → **Config file**: **`railway.web.toml`**
3. **Remove cron schedule** on this service (cron is only on `data-layer-cron`)
4. **Variables:** `GEMINI_API_KEY` (required for LLM screening)
5. **Volume:** attach the **same volume** as `data-layer-cron` so dossiers persist
6. Redeploy

### One-time Railway setup for `data-layer-cron`

1. Root Directory: `/`
2. Config file: `railway.json`
3. Volume attached (dossiers + sqlite cache)

### Verify

```bash
curl -i "https://YOUR-RAILWAY-URL.up.railway.app/api/health"
# expect HTTP/2 200 and {"status":"ok"}
```

Point Vercel's `RAILWAY_PUBLIC_URL` at this URL and redeploy the frontend.

**Dossiers on Railway:** attach the **same Volume** used by `data-layer-cron` to
the `stock_ai` web service. Screening reads `$RAILWAY_VOLUME_MOUNT_PATH/dossiers/`.
Without a shared volume, deploy succeeds but screening returns "No dossiers found".

Strategy knowledge markdown in `backend/knowledge/` is legacy; live screening
uses `selector/prompts/` with full dossier JSON.
