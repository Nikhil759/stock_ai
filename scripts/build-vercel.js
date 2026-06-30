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

const config = `window.__API_BASE__ = ${JSON.stringify(apiBase)};\n`;
fs.writeFileSync(path.join(out, 'config.js'), config);

console.log('Built public/ for Vercel');
console.log('  API base:', apiBase || '(same origin)');
