// Local dev: empty base → same origin (uvicorn serves UI + API at localhost:8000).
// Vercel build overwrites public/config.js with RAILWAY_PUBLIC_URL.
window.__API_BASE__ = '';
