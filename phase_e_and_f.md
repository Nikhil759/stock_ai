# Wolf Capital — Phase E+F Handover Prompt (Supabase version)
## Structured Health Status Store (Supabase) + Login-Gated Dashboard (Supabase Auth)

Paste this whole document into Cursor as the task brief. This supersedes the earlier
Streamlit-native-auth version of this handover — use Supabase for both the status
store and login, since Supabase is already the planned database/backend for this app.

---

## Context

Phases A-D produce console logs at every stage. This phase adds a structured status
store in Supabase (Postgres) alongside those logs, and builds a Streamlit dashboard
gated by Supabase Auth (Google OAuth, PKCE flow), restricted to one specific email.

**Prerequisite (already done outside this prompt):** Google OAuth is configured as a
provider inside Supabase's dashboard, the developer's email is added as a Google
test user, and the Streamlit app's redirect URL is allow-listed in Supabase's URL
configuration.

---

## Part 1 — Health status store (Supabase table)

**File:** `logging/health_status.py`

Create a Supabase table `health_status`:
```sql
create table health_status (
  date date primary key,
  started_at timestamptz,
  stages jsonb,
  overall_status text
);
```

- At every stage of `morning_ingestion.py` (fetch, technicals, market context, each
  strategy's funnel, each strategy's batch scoring, each strategy's cache save),
  upsert the row for today's date via the Supabase Python client (`supabase-py`),
  merging into the `stages` JSON rather than overwriting the whole row each time.
- Write incrementally as each stage completes — not all at once at the end — so
  "today's live status" reflects real progress while the run is still happening.
- `stages` JSON shape (same as previously designed):
```json
{
  "fetch":           {"status": "success", "detail": "200/200 fetched"},
  "technicals":      {"status": "success", "detail": "200/200 computed, engine=ta"},
  "market_context":  {"status": "success", "detail": "Nifty +0.4%, VIX 13.2"},
  "funnels": {
    "value":   {"status": "success", "in": 200, "out": 25},
    "winners": {"status": "success", "in": 200, "out": 53},
    "box":     {"status": "success", "in": 200, "out": 3},
    "dip":     {"status": "success", "in": 200, "out": 3}
  },
  "batch_scoring": {
    "value":   {"status": "success", "candidates_scored": 25, "survivors": 7},
    "winners": {"status": "success", "candidates_scored": 53, "survivors": 12},
    "box":     {"status": "success", "candidates_scored": 3,  "survivors": 2},
    "dip":     {"status": "success", "candidates_scored": 3,  "survivors": 1}
  },
  "cache_saved": {"value": true, "winners": true, "box": true, "dip": true}
}
```
- `overall_status`: `"success"` if every stage succeeded, `"partial"` if some
  strategies failed, `"failed"` if shared prep itself failed.
- Provide read functions: `get_status(date)` and `get_recent_statuses(n=5)`, both
  querying Supabase directly.

---

## Part 2 — Supabase Auth login (Google, PKCE flow)

**File:** `dashboard/dashboard_app.py`

Use `supabase-py`'s auth methods — **use the PKCE flow specifically, not the
implicit/fragment flow**, because Streamlit's server-side script can read a URL
query parameter but cannot read a URL fragment (which is JS-only), and the implicit
flow returns its token in a fragment.

```python
from supabase import create_client

supabase = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Step 1: trigger login
def login():
    result = supabase.auth.sign_in_with_oauth({
        "provider": "google",
        "options": {"redirect_to": APP_REDIRECT_URL, "flow_type": "pkce"}
    })
    st.link_button("Log in with Google", result.url)

# Step 2: handle the redirect back (Supabase appends ?code=... to the URL)
code = st.query_params.get("code")
if code and "user" not in st.session_state:
    session = supabase.auth.exchange_code_for_session({"auth_code": code})
    st.session_state["user"] = session.user
```

- Store the resulting user (specifically `user.email`) in `st.session_state` for the
  rest of that browser session.
- **Known limitation to accept, not solve**: `st.session_state` does not survive a
  full page reload/new browser tab — the user will need to log in again if they
  close and reopen the app. For a single-person dashboard, this is an acceptable
  trade-off; do not build additional cookie-persistence plumbing for this unless
  asked.
- `SUPABASE_URL` and `SUPABASE_ANON_KEY` come from `secrets.toml` (or `.env`,
  matching however the rest of the project already manages Supabase credentials) —
  do not hardcode them.

---

## Part 3 — Access control & navigation gating

- Add a constant `AUTHORIZED_EMAIL` (placeholder in this prompt — developer fills in
  their real email in config, not hardcoded in source visible to Cursor).
- Build this as a multi-page Streamlit app (`st.navigation`/`st.Page`).
- The **Health Check** page must:
  - Not appear in the navigation sidebar unless `st.session_state["user"].email == AUTHORIZED_EMAIL`
  - Additionally re-check the email match at the top of the page itself (defense in
    depth, in case the page is reached directly by URL)
  - Show a plain "Not authorized" message with no dashboard data if the check fails
    or no user is logged in

---

## Part 4 — Dashboard views (on the Health Check page)

Same as previously designed, now reading from the Supabase `health_status` table:

1. **Last 5 days strip** — one row per day via `get_recent_statuses(5)`, colored
   status chips per stage (green/red/gray)
2. **Today's live checklist** — stage-by-stage status for today, showing "not
   started" clearly if the pipeline hasn't run yet
3. **Drill-down on click** — expand any stage to show its full `detail` field

---

## Acceptance criteria

- [ ] `health_status` table exists in Supabase and is upserted incrementally during
      a full pipeline run, not all at once at the end
- [ ] Logging in with the authorized Google email (via Supabase, PKCE flow) shows
      the Health Check nav item and full dashboard
- [ ] Logging in with any other Google email, or not logging in, hides the nav item
      entirely, and directly visiting the page shows "Not authorized" with no data
- [ ] Last-5-days view reflects real historical rows from Supabase, not mock data
- [ ] No Supabase key, client secret, or the authorized email is hardcoded directly
      in source files committed to version control