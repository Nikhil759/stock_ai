#!/usr/bin/env node
/** Build static frontend for Vercel — copies UI and injects Railway API URL. */
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const out = path.join(root, 'public');

function normalizeBase(raw) {
  let b = String(raw || '').trim().replace(/\/$/, '');
  if (!b) return '';
  if (!/^https?:\/\//i.test(b)) b = 'https://' + b;
  return b;
}

const apiBase = normalizeBase(
  process.env.RAILWAY_PUBLIC_URL ||
  process.env.RAILWAY_URL ||
  process.env.VITE_API_BASE ||
  ''
);

if (!apiBase) {
  console.warn('Warning: RAILWAY_PUBLIC_URL not set — config.js will use same-origin /api (local dev only).');
}

fs.mkdirSync(out, { recursive: true });

const htmlSrc = path.join(root, 'Trading Bot.dc.html');
const htmlDst = path.join(out, 'index.html');
fs.copyFileSync(htmlSrc, htmlDst);
fs.copyFileSync(path.join(root, 'support.js'), path.join(out, 'support.js'));
fs.copyFileSync(path.join(root, 'wolf_logo.png'), path.join(out, 'wolf_logo.png'));
fs.copyFileSync(path.join(root, 'manifest.webmanifest'), path.join(out, 'manifest.webmanifest'));
fs.copyFileSync(path.join(root, 'sw.js'), path.join(out, 'sw.js'));

const config = `window.__API_BASE__ = "";\n`;
fs.writeFileSync(path.join(out, 'config.js'), config);

// Auth/session cookies are first-party on Vercel — proxy all /api/* to Railway
// so deploy/bots/trades share the same session as /api/ops/me.
const rewrites = [{ source: '/app', destination: '/index.html' }];
if (apiBase) {
  rewrites.push(
    { source: '/health', destination: `${apiBase}/health` },
    { source: '/health/:path*', destination: `${apiBase}/health/:path*` },
    { source: '/api/:path*', destination: `${apiBase}/api/:path*` },
  );
}

const vercelConfig = {
  buildCommand: 'npm run build',
  outputDirectory: 'public',
  rewrites,
  headers: [
    {
      source: '/sw.js',
      headers: [
        { key: 'Cache-Control', value: 'no-cache' },
        { key: 'Service-Worker-Allowed', value: '/' },
      ],
    },
    {
      source: '/manifest.webmanifest',
      headers: [
        { key: 'Content-Type', value: 'application/manifest+json' },
      ],
    },
  ],
};

fs.writeFileSync(
  path.join(root, 'vercel.json'),
  JSON.stringify(vercelConfig, null, 2) + '\n'
);

console.log('Built public/ for Vercel');
console.log('  API base:', '(same-origin via /api proxy)');
if (apiBase) {
  console.log('  API proxy: /health/* and /api/* ->', apiBase);
}
