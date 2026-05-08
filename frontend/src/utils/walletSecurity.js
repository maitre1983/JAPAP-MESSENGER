/**
 * walletSecurity.js — iter221
 *
 * Centralised, defensive guards for the JAPAP wallet UI. ALL wallet
 * components MUST go through these helpers before rendering or sending
 * provider/server data to the UI. The goal is to neutralise XSS, IDOR,
 * Path Traversal and invalid payload injections at a single chokepoint.
 *
 * Pure functions only — no React, no global state, no async I/O.
 */

const ALLOWED_ICON_DOMAINS = [
  'japapmessenger.com', 'cdn.japapmessenger.com',
  'nowpayments.io', 'static.nowpayments.io', 's3.amazonaws.com',
];

export const ALLOWED_CURRENCIES = [
  'GHS', 'XAF', 'XOF', 'NGN', 'USD', 'EUR', 'USDT', 'BTC', 'ETH', 'TRX', 'BNB', 'USDC',
];

const ALLOWED_PROVIDER_STATUSES = {
  pending: 'En attente',
  processing: 'En cours',
  completed: 'Confirmé',
  failed: 'Échoué',
  waiting: 'En attente du paiement',
  confirming: 'Confirmation en cours',
  confirmed: 'Paiement confirmé',
  finished: 'Crédité avec succès',
  expired: 'Paiement expiré',
  partially_paid: 'Paiement partiel détecté',
};

const JAPAP_ORIGINS = [
  typeof window !== 'undefined' ? window.location.origin : '',
  'https://japapmessenger.com',
  'https://www.japapmessenger.com',
].filter(Boolean);

export function isSafeUrl(url, allowedDomains = ALLOWED_ICON_DOMAINS) {
  if (!url || typeof url !== 'string') return false;
  try {
    const { protocol, hostname } = new URL(url);
    if (protocol !== 'https:') return false;
    return allowedDomains.some(d => hostname === d || hostname.endsWith('.' + d));
  } catch { return false; }
}

export function isSafeTxId(id) {
  return typeof id === 'string' && /^[a-zA-Z0-9_-]{4,128}$/.test(id.trim());
}

export function isSafeUid(uid) {
  return typeof uid === 'string' && /^[a-zA-Z0-9_-]{4,64}$/.test(uid.trim());
}

export function sanitizeNote(note, maxLen = 100) {
  if (!note || typeof note !== 'string') return '';
  return note
    .replace(/https?:\/\/\S+/gi, '')
    .replace(/[<>"'`]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, maxLen);
}

export function validateAmount(amount, min = 1, max = 10_000_000) {
  const n = parseFloat(amount);
  if (!Number.isFinite(n)) return { valid: false, reason: 'Montant invalide' };
  if (n <= 0) return { valid: false, reason: 'Le montant doit être positif' };
  if (n < min) return { valid: false, reason: `Montant minimum : ${min}` };
  if (n > max) return { valid: false, reason: `Montant maximum : ${max.toLocaleString('fr-FR')}` };
  return { valid: true, value: Math.round(n * 100) / 100 };
}

export function validateConversion(preview, expectedUsd) {
  if (!preview || typeof preview !== 'object') return false;
  if (expectedUsd != null) {
    const diff = Math.abs(parseFloat(preview.amount_usd) - parseFloat(expectedUsd));
    if (diff > 0.01) return false;
  }
  if (!ALLOWED_CURRENCIES.includes(preview.provider_currency)) return false;
  const rate = parseFloat(preview.exchange_rate);
  if (preview.exchange_rate != null && (!Number.isFinite(rate) || rate <= 0 || rate > 1_000_000)) return false;
  const local = parseFloat(preview.provider_amount);
  if (!Number.isFinite(local) || local <= 0) return false;
  if (preview.display_note) preview.display_note = String(preview.display_note).slice(0, 200);
  return true;
}

export function safeProviderStatus(rawStatus) {
  return ALLOWED_PROVIDER_STATUSES[rawStatus] || '';
}

export function isSafeWhatsAppUrl(url) {
  if (!url || typeof url !== 'string') return false;
  try {
    const { protocol, hostname } = new URL(url);
    return protocol === 'https:' && (hostname === 'wa.me' || hostname === 'api.whatsapp.com');
  } catch { return false; }
}

export function isValidQrPayload(obj) {
  if (typeof obj !== 'object' || obj === null || Array.isArray(obj)) return false;
  const ALLOWED_KEYS = ['t', 'uid', 'v', 'ts', 'sig', 'type', 'id'];
  const keys = Object.keys(obj);
  if (keys.length === 0 || keys.length > 8) return false;
  if (!keys.every(k => ALLOWED_KEYS.includes(k))) return false;
  if (!Object.values(obj).every(v => ['string', 'number'].includes(typeof v))) return false;
  return true;
}

export function isSafeJapapUrl(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return false;
    return JAPAP_ORIGINS.includes(parsed.origin);
  } catch { return false; }
}

export function isSafeQrUrl(qrUrl, apiBase) {
  if (!qrUrl || typeof qrUrl !== 'string') return false;
  try {
    const full = qrUrl.startsWith('http') ? qrUrl : `${apiBase}${qrUrl}`;
    const parsed = new URL(full);
    const apiParsed = new URL(apiBase);
    return parsed.origin === apiParsed.origin && /\.(png|svg|jpg|webp)(\?.*)?$/.test(parsed.pathname);
  } catch { return false; }
}

export function sanitizeErrorReport(report) {
  return {
    source: 'frontend',
    module: String(report.module || '').slice(0, 64),
    severity: ['low', 'medium', 'high'].includes(report.severity) ? report.severity : 'medium',
    message: String(report.message || '')
      .replace(/tx[_=][a-zA-Z0-9_-]+/gi, 'tx=[redacted]')
      .replace(/payment[_=][a-zA-Z0-9_-]+/gi, 'payment=[redacted]')
      .slice(0, 300),
    stack: String(report.stack || '').slice(0, 400),
    url: typeof window !== 'undefined' ? window.location.pathname : '',
  };
}

export function safeCurrencyList(currencies, maxCount = 50) {
  if (!Array.isArray(currencies)) return [];
  return currencies.filter(c => c &&
    typeof c.code === 'string' && /^[A-Z]{3}$/.test(c.code) &&
    typeof c.name === 'string' && c.name.length <= 50 &&
    typeof c.symbol === 'string' && c.symbol.length <= 10
  ).slice(0, maxCount);
}

export function isValidRate(rate) {
  const n = Number(rate);
  return Number.isFinite(n) && n > 0 && n < 10_000_000;
}

export function isValidLocalAmount(amount) {
  const n = Number(amount);
  return Number.isFinite(n) && n > 0;
}

export function maskId(id, prefixLen = 6, suffixLen = 4) {
  if (!id || typeof id !== 'string' || id.length <= prefixLen + suffixLen) return id;
  return `${id.slice(0, prefixLen)}…${id.slice(-suffixLen)}`;
}
