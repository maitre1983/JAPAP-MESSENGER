# JAPAP Messenger — Super-App

> **JAPAP** is a mobile-first, all-in-one super-app combining a WhatsApp-style
> messenger, a fiat + crypto wallet, a marketplace (food / taxi / jobs /
> services), a crypto staking product (MIR on BSC), a hotspot-powered
> connectivity layer (**JAPAP Connect v2**), a crowdfunding platform with
> a community-elected jury, and an admin control center.
>
> 🚀 **Production**: https://japapmessenger.com  ·  28 000+ users  ·  5 langues UI (FR · EN · ES · AR with RTL · RU)

![JAPAP Logo](frontend/public/japap-logo.jpg)

---

## Table of contents

1. [Feature map](#feature-map)
2. [Architecture at a glance](#architecture-at-a-glance)
3. [Tech stack](#tech-stack)
4. [Directory layout](#directory-layout)
5. [Local development](#local-development)
6. [Environment variables](#environment-variables)
7. [Database](#database)
8. [Web Push (VAPID)](#web-push-vapid)
9. [PWA install flow](#pwa-install-flow)
10. [Admin dashboard](#admin-dashboard)
11. [Testing](#testing)
12. [Deployment](#deployment)
13. [Iteration changelog](#iteration-changelog)
14. [Operational safety rails](#operational-safety-rails)
15. [License & credits](#license--credits)

---

## Feature map

| Block            | What users get                                                                                                                              |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| **Home / Feed**  | Social feed with posts, stories, tips, comments, likes, mentions, OG-share, follow suggestions.                                             |
| **Messenger**    | WhatsApp-class chat: 1-1 & groups, voice notes, media, reactions, forwards (with chain view), translation, AI summarization, A/V calls (LiveKit). |
| **Services**     | Marketplace, food ordering, **Transport JAPAP** (rider + driver KYC + matching + tracking + cancel), jobs, hotspot rentals (Connect v2), JAPAP Staking (MIR on BSC). |
| **Wallet**       | Multi-currency balance (XAF, USD, EUR, NGN…), deposit (NowPayments USDT), withdraw, in-chat money transfers, transaction history.            |
| **Games (P3)**   | **Wheel of Fortune v2** (30-day cycle, 10 000 pts + 25 days, anti-bot Turnstile), **Tap Challenge** (backend-authoritative anti-cheat), **Quiz JAPAP** (5Q sessions, anti-bruteforce, learning mode, daily challenge with streak, anti-repetition picker, AI-generated content via Claude), **Quiz Champion par Pays** (Free + Paid escrow with 10% commission), **Quiz Duel** (peer-to-peer with tiebreaker temps + WhatsApp viral share + rematch loop). |
| **Profile**      | Personal info, KYC, task inbox, referral link with visual tier editor, Pro plans (Star / Hot / Ultima / VIP), Web Push (OneSignal), preferred UI language & auto-translate. |
| **Crowdfunding** | Project pitches with rich media, community voting + ⚖️ **Jury system** (community-elected, premium SVG/PDF certificate auto-generated and uploaded to Cloudflare R2, vote-multiplier, scannable QR), recruit-reminder + viral share. |
| **Reels**        | TikTok-style vertical feed with IntersectionObserver autoplay, snap scroll, native portrait/landscape/square handling (iter240m). |
| **Payments** 🔒  | Hubtel MoMo · Paystack · USDT (TRC20/BEP20) · Orange Money · Wave — **real-money rails, never touched outside dedicated payment iterations**. |
| **Admin**        | Stats, messaging campaigns, ads, payments, staking controls, **Games & Engagement** dashboards, **Quiz Champion KPIs**, **Erreurs IA** (Claude RCA + bulk-action), **Fiche Admin 7-onglets** (overview, transactions, KYC, restrictions, notes, posts, crowdfunding) accessible from any user mention via `<UserNameLink/>`, system settings. |

---

## Architecture at a glance

```
┌───────────────────────────────────────────────────────────────────────┐
│                         FRONTEND (CRA + React)                         │
│   React Router · Tailwind · shadcn/ui · Socket.IO client · i18n        │
│   PWA: manifest.json · service-worker (sw.js) · install prompt         │
└────────────┬──────────────────────────────────────────────────────────┘
             │  REST (axios, /api/*)          │  WebSocket (/socket.io)
             │  Web Push (VAPID, /api/push/*) │
┌────────────▼──────────────────────────────────────────────────────────┐
│                  BACKEND (FastAPI + python-socketio)                   │
│   Routers:                                                             │
│     auth · messaging · feed · wallet · pro · admin · admin_messaging   │
│     staking · connect · push · referrals · ads · stats · tasks         │
│   Services: messaging_worker · push_service · email_renderer           │
│   Middleware: rate_limit · CORS · cookie-auth                          │
└────────────┬──────────────────────────────────────────────────────────┘
             │  asyncpg (Neon PgBouncer-style pool, statement_cache=0)
┌────────────▼──────────────────────────────────────────────────────────┐
│              NEON POSTGRESQL  —  all app data, 28 913 users            │
│   users · conversations · messages · posts · wallets · staking_*       │
│   email_campaigns · push_subscriptions · admin_settings · …            │
└───────────────────────────────────────────────────────────────────────┘

External integrations:
  • Resend (email, webhook + Svix signature verification)
  • Emergent LLM Key → Claude Sonnet 4.5 (admin AI templates)
  • Web3.py → Binance Smart Chain (staking on-chain sync, read-only)
  • pywebpush (VAPID Web Push, self-hosted)
```

---

## Tech stack

**Backend**
- Python 3.11 · FastAPI · Uvicorn + Gunicorn
- `asyncpg` (Neon, `statement_cache_size=0`, `max_size=50`, `command_timeout=20`, `max_inactive_connection_lifetime=60` — see iter240l-prodfix)
- `python-socketio` for realtime chat / calls / notifications
- `cairosvg` + `qrcode[svg]` for premium SVG → PDF certificates (Jury Crowdfunding, iter240l)
- `pywebpush` + `py_vapid` for Web Push (legacy — OneSignal preferred since iter71)
- `web3` for read-only BSC staking mirror
- `emergentintegrations` (Claude Sonnet 4.5 via Emergent LLM Key)
- `svix` for Resend webhook signature verification
- `resend` SDK for email delivery
- `boto3` for Cloudflare R2 (S3-compatible) asset storage (avatars, certificates, video thumbnails)

**Frontend**
- React 18 (CRA) · React Router · Tailwind CSS · Shadcn/UI (`/components/ui/`)
- Socket.IO client · axios · react-i18next · sonner (toasts)
- `@phosphor-icons/react` for iconography
- Service Worker (`public/sw.js`) + Web App Manifest for PWA installability
- Web3Modal / wagmi (inside `crypto_legacy/` — MetaMask connect for staking)

**Database**
- Neon PostgreSQL (remote, pooled). All migrations are idempotent `CREATE
  TABLE IF NOT EXISTS …` executed on startup.

---

## Directory layout

```
/app
├── backend/
│   ├── server.py                    — FastAPI app + Socket.IO, startup hooks
│   ├── database.py                  — asyncpg pool (Neon-safe), migrations
│   ├── routes/
│   │   ├── auth.py                  — cookie-based JWT, bcrypt, brute-force
│   │   ├── messaging.py             — chat 1-1 / groups, send-money, voice…
│   │   ├── calls.py                 — 1-1 & group calls (WebRTC signaling)
│   │   ├── feed.py                  — posts, stories, likes, comments, tips
│   │   ├── wallet.py                — balances, deposit, withdraw, transfers
│   │   ├── pro.py                   — subscription tiers (Star/Hot/…)
│   │   ├── staking.py               — MIR staking (plans, positions, admin)
│   │   ├── admin_messaging.py       — email campaigns + Resend webhooks
│   │   ├── push.py                  — Web Push VAPID endpoints (iter70)
│   │   ├── realtime.py              — Socket.IO fan-out helpers
│   │   └── …
│   ├── services/
│   │   ├── push_service.py          — pywebpush dispatcher + DB table DDL
│   │   ├── email_renderer.py        — MJML → HTML with template vars
│   │   ├── messaging_worker.py      — background worker for queued emails
│   │   └── settings_service.py     — admin_settings key/value store
│   ├── middleware/rate_limit.py     — per-IP + per-user sliding-window limits
│   ├── tests/                       — pytest regression (see §Testing)
│   └── requirements.txt
│
├── frontend/
│   ├── public/
│   │   ├── index.html               — PWA meta tags (iOS + Android)
│   │   ├── manifest.json            — installable PWA manifest
│   │   ├── sw.js                    — Service Worker (cache + push + click)
│   │   ├── offline.html             — branded offline fallback
│   │   ├── pwa-icon-{192,512}.png   — PWA icons + maskable variant
│   │   ├── apple-touch-icon.png     — iOS home-screen icon (180×180)
│   │   └── japap-logo.jpg           — master brand asset
│   ├── src/
│   │   ├── App.js                   — router + providers + <InstallPWA/>
│   │   ├── index.js                 — SW registration
│   │   ├── pages/
│   │   │   ├── FeedPage.js          — /feed
│   │   │   ├── ChatPage.js          — /chat & /chat/:convId
│   │   │   ├── ServicesPage.js      — /services (hub + JAPAP Staking view)
│   │   │   ├── WalletPage.js        — /wallet
│   │   │   ├── ProfilePage.js       — /profile (+ push toggle)
│   │   │   ├── AdminPage.js         — /admin (stats/messaging/staking/…)
│   │   │   └── admin/
│   │   │       ├── MessagingAdminTab.jsx
│   │   │       ├── StakingAdminTab.jsx
│   │   │       └── …
│   │   ├── components/
│   │   │   ├── layout/Layout.js           — sidebar + bottom nav (safe-area)
│   │   │   ├── InstallPWA.jsx             — Android + iOS install prompts
│   │   │   ├── PushNotificationsToggle.jsx— /profile push opt-in card
│   │   │   └── …
│   │   ├── services/webpush.js      — browser Push API wrapper (VAPID)
│   │   ├── crypto_legacy/           — imported JAPAP Staking ZIP (MIR/BSC)
│   │   ├── CryptoStakingApp.js      — wrapper around legacy module
│   │   ├── context/ (Auth, Realtime, Tasks)
│   │   ├── locales/ (fr.json, en.json)
│   │   └── index.css                — mobile-first safety rails
│   ├── package.json
│   └── .env                         — frontend-only env (REACT_APP_*)
│
├── memory/
│   ├── PRD.md                       — single source of truth for this app
│   ├── test_credentials.md          — admin + test user creds
│   └── *.md                         — postmortems, runbooks
│
└── test_reports/                    — auto-generated per iteration
```

---

## Local development

```bash
# 1) Services are supervised — you normally don't run them by hand.
sudo supervisorctl status          # frontend + backend + (historical) mongodb
sudo supervisorctl restart backend # apply .env / dependency changes
sudo supervisorctl restart frontend

# 2) Backend dev loop (auto-reloads on save):
tail -f /var/log/supervisor/backend.*.log

# 3) Frontend dev loop:
#    CRA + HMR runs on port 3000 behind Kubernetes ingress.
#    Browser-facing URL is REACT_APP_BACKEND_URL from frontend/.env.

# 4) Run tests:
cd /app/backend
RATE_LIMIT_ENABLED=false python -m pytest tests/ -q -p no:pytest_ethereum
```

### Installing a new dependency

```bash
# Python — edit & freeze so requirements.txt stays pinned
pip install <pkg> --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/
pip freeze > /app/backend/requirements.txt

# JS — yarn only (npm breaks the lockfile)
cd /app/frontend && yarn add <pkg>
```

---

## Environment variables

> ⚠️ Never commit real secrets. `.env` files live outside Git.

### `backend/.env`

| Key                              | Purpose                                             |
| -------------------------------- | --------------------------------------------------- |
| `MONGO_URL`                      | Legacy — kept for schema compat, unused at runtime  |
| `DB_NAME`                        | Same as above                                       |
| `DATABASE_URL`                   | **Neon PostgreSQL connection string** (required)    |
| `JWT_SECRET`                     | HMAC key for cookie-auth JWTs                       |
| `EMERGENT_LLM_KEY`               | Universal LLM key (Claude / Gemini / GPT / Sora)    |
| `RESEND_API_KEY`                 | Transactional email provider                        |
| `RESEND_WEBHOOK_SECRET`          | Svix signature verification for Resend webhooks     |
| `VAPID_PRIVATE_KEY_B64`          | *Removed in iter71 — use OneSignal instead*          |
| `VAPID_PUBLIC_KEY`               | *Removed in iter71*                                  |
| `VAPID_SUBJECT`                  | *Removed in iter71*                                  |
| `ONESIGNAL_APP_ID`               | OneSignal App ID (public, safe to ship to client)    |
| `ONESIGNAL_REST_API_KEY`         | OneSignal REST API key (server-only, never expose)   |
| `BSC_RPC_URL`                    | Binance Smart Chain RPC (staking read-only)         |
| `MIR_CONTRACT_ADDRESS`           | MIR staking contract address on BSC                 |
| `MESSAGING_*`                    | Kill switches (`real_send_enabled`, rate caps…)     |

### `frontend/.env`

| Key                             | Purpose                                              |
| ------------------------------- | ---------------------------------------------------- |
| `REACT_APP_BACKEND_URL`         | Public backend URL (no trailing slash)               |
| `REACT_APP_VAPID_PUBLIC_KEY`    | *Removed in iter71 — use OneSignal App ID*           |
| `REACT_APP_ONESIGNAL_APP_ID`    | Same value as backend `ONESIGNAL_APP_ID`             |
| `REACT_APP_WEB3MODAL_PROJECT_ID`| WalletConnect v2 Project ID (staking)                |
| `REACT_APP_FIREBASE_*`          | Optional — legacy staking expected these keys        |

---

## Database

- **Provider**: [Neon](https://neon.tech) — PostgreSQL 15 with a PgBouncer-
  style pooler.
- **Critical**: the asyncpg pool must be created with
  `statement_cache_size=0` (see `database.py`), otherwise cached prepared
  statements become invalid the moment a `CREATE TABLE IF NOT EXISTS … ADD
  COLUMN IF NOT EXISTS …` migration runs on another connection —
  observable in prod as random HTTP 500 on `/api/staking/stake`,
  `/dashboard`, etc.
- **Migrations**: all DDL is `IF NOT EXISTS`-guarded and runs at startup in
  `database.py::init_db()` + per-module helpers
  (e.g. `push_service.ensure_table()`).

---

## Web Push (OneSignal)

JAPAP uses **OneSignal Web Push SDK v16** (iter71) for every push
notification — both transactional (chat messages, tips, money transfers,
revshare credits) and marketing campaigns (admin broadcasts from the
OneSignal dashboard). A single system, a single operator dashboard.

### Server side
- `services/push_service.py` builds and POSTs notifications to the
  OneSignal REST API (`https://api.onesignal.com/notifications`).
- Users are targeted by **External ID** (our internal `user_id`) via
  `include_aliases.external_id`. The frontend tags the OneSignal
  subscription with `OneSignal.login(user_id)` so the server never needs
  to know OneSignal's internal player/subscription IDs.
- Triggered automatically from `routes/realtime.py` on:
  - `notify_tip`               → title: `💸 {sender} t'a envoyé un tip`
  - `notify_money`             → title: `💰 {sender} t'a envoyé {amount}`
  - `notify_comment`           → title: `💬 {sender} a commenté` *(offline only)*
  - `notify_connect_revshare`  → title: `🎉 Revshare Connect : +{amount}`
  - `notify_new_message_offline` → title: `💬 {sender}` *(DM / group msg to an offline recipient)*
- `notify_like` is deliberately socket-only — no push (low signal, high noise).
- Common OneSignal 400 "All included players are not subscribed" is
  silently swallowed as `skipped=user_not_subscribed` so fan-out never
  poisons the caller's realtime flow.

### Service Worker isolation
- Our custom app-shell SW (`public/sw.js`) runs at scope `/` and owns
  offline/cache + navigations.
- OneSignal's SW (`public/OneSignalSDKWorker.js`) runs at the side
  scope `/push/onesignal/` and owns push delivery + click-through. The
  two never fight over the same scope.

### API surface (all prefixed with `/api/push/`)

| Method | Path                 | Auth    | Description                                                 |
| ------ | -------------------- | ------- | ----------------------------------------------------------- |
| GET    | `/public-key`        | public  | Returns `{provider:"onesignal", app_id, configured}`        |
| POST   | `/test-vapid`        | admin   | Send a test push to target user (kept the iter70 path name) |

> **Note** — the old `POST /subscribe` / `POST /unsubscribe` endpoints
> from the iter70 VAPID stack have been removed. OneSignal manages
> subscriptions entirely on the client via its SDK.

### Client side
- `src/services/webpush.js` — `subscribePush()`, `unsubscribePush()`,
  `identifyUser(userId)`, plus feature-detection helpers. Thin wrapper
  around the OneSignal v16 `OneSignalDeferred` API.
- `src/components/PushNotificationsToggle.jsx` — the opt-in card mounted
  inside `/profile`. Activate / Disable / Send test.
- `src/context/AuthContext.js` — auto-tags the OneSignal subscription
  with the logged-in user's `user_id` on every session refresh, so
  server-side targeting by External ID works the instant the user opts in.

### Rotating the OneSignal REST API key
1. OneSignal dashboard → **Settings → Keys & IDs** → **Generate New API Key**.
2. Paste into `backend/.env` as `ONESIGNAL_REST_API_KEY`.
3. `sudo supervisorctl restart backend` to reload the env.
4. No DB migration needed — subscriptions stay valid.

---

## PWA install flow

### Android (and any Chromium-based browser)
The browser fires `beforeinstallprompt`; `<InstallPWA />` catches it,
suppresses the default mini-infobar, and shows a branded banner:

```
   ┌─────────────────────────────────────────────────────────┐
   │ [logo]  Installer JAPAP                 [ Installer ]   │
   │         Accès rapide · Hors-ligne friendly              │
   └─────────────────────────────────────────────────────────┘
```
One tap → `deferredPrompt.prompt()` → user installs.
Dismiss → 7-day localStorage snooze.

### iOS Safari 16.4+
iOS doesn't fire `beforeinstallprompt`. After a 4.5s grace period we show
a bottom sheet with a 3-step visual guide:

1. **Touchez 🔗 "Partager"** (Safari bottom bar)
2. **Sélectionnez ➕ "Sur l'écran d'accueil"**
3. **JAPAP est prêt** — launch from your home screen

### Requirements met for installability
- ✅ Served over HTTPS (Kubernetes ingress handles TLS)
- ✅ Valid `manifest.json` with `start_url`, `display=standalone`,
  `icons[]` including a `purpose=maskable` 512×512
- ✅ Registered service worker with a `fetch` handler (`sw.js`)
- ✅ `theme-color` meta + Apple touch icon + `apple-mobile-web-app-capable`

---

## Admin dashboard

Accessible at `/admin` for users with `role=admin`.

| Tab          | Purpose                                                                                                       |
| ------------ | ------------------------------------------------------------------------------------------------------------- |
| **Stats**    | KPIs — DAU/WAU, messages/day, wallets, staking TVL.                                                           |
| **Ads**      | Promoted content management.                                                                                  |
| **Payments** | Deposits, withdrawals, adjustments, disputes.                                                                 |
| **Messaging**| Email campaigns · AI-generated templates (Claude Sonnet 4.5) · segments · **Batch & Safety** (iter 82: live queue stats, audience cap, worker rate/min, batch size, real-send kill switch, requeue failed) · webhook logs. |
| **Parrainage** | Referrals list + anti-fraud blocking + **visual tier editor** (iter 82: add / edit / reorder / delete reward tiers from the UI — no JSON). |
| **Staking**  | Live metrics, per-plan edit (APY bps, min/max stake, early fee, active), force on-chain sync, hard-lock flags. |

---

## Testing

Pytest suites live in `/app/backend/tests/`. Always run with:

```bash
cd /app/backend
RATE_LIMIT_ENABLED=false python -m pytest tests/ -q -p no:pytest_ethereum
```

> `-p no:pytest_ethereum` disables the pytest plugin shipped by `web3`
> that conflicts with our fixtures.

Current regression (iter82): **84/84 green**
- `test_iter82_batch_tiers.py` (6) — batch safety settings roundtrip + referral tiers edited via dashboard
- `test_iteration78_privacy_settings.py` — privacy + post visibility
- `test_iteration77_og_social.py` — Open Graph SSR + social counters
- `test_iteration76_feed_share_comments.py` — comments + external share
- `test_iteration74_autocurrency.py` (11) — currency auto-detection at signup + /preferences
- `test_iteration73_autolang.py` (8) — auto-detect priority order, signup wiring, country map
- `test_iteration72_i18n.py` (15) — 11-language bundles parity, /preferences accept/reject
- `test_iteration71_onesignal.py` (7) — OneSignal REST surface + service
- `test_iteration67_staking.py` (8) — MIR plans, monitoring, admin edits
- `test_iteration64_messaging_center.py` (22) — campaigns, queue, rate limit
- `test_iteration66_individual_targeting.py` (7) — smart segments

Frontend is validated via the `testing_agent_v3_fork` playbook on 4 mobile
viewports (iPhone SE 320, Android 360, iPhone X 375, iPhone Plus 414).

---

## Deployment

### Two environments
| Env             | URL                                | Purpose                              |
| --------------- | ---------------------------------- | ------------------------------------ |
| **PREVIEW**     | `japap-refactor.preview.emergentagent.com` (rotating) | Active development & QA           |
| **PRODUCTION**  | **https://japapmessenger.com**     | Live users (28k+), real transactions |

- Hosted inside a Kubernetes container managed by Emergent.
- Supervisor processes: `backend` (Uvicorn on `0.0.0.0:8001`) + `frontend`
  (CRA dev server on `0.0.0.0:3000`).
- Ingress routes `/api/*` → 8001, everything else → 3000.
- **Never** change ports or bind addresses — supervisor + ingress coupling.
- Deploy = Emergent "Deploy" button → pushes preview to japapmessenger.com.

### Readiness probes (iter240l-prodfix)

| Endpoint            | What it proves                                    | Use case                  |
| ------------------- | ------------------------------------------------- | ------------------------- |
| `GET /api/health`   | uvicorn is alive (in-memory ping only)            | Liveness — k8s probe      |
| `GET /api/health/db` | Postgres reachable, pool not saturated, query<3s | **Readiness — monitoring** |

`/api/health/db` returns a JSON payload with live pool stats:
```json
{ "status": "healthy", "db_ms": 331.7,
  "pool": {"size": 6, "max_size": 50, "min_size": 5, "idle": 5} }
```
External monitoring (UptimeRobot / BetterStack) **must** poll
`/api/health/db` every 60s — `/api/health` alone misses pool exhaustion
(the failure mode that caused the 24h P0 outage on 15/05/2026).

### Manual smoke after a deploy

```bash
curl -sS https://japapmessenger.com/api/health/db | python3 -m json.tool
# Expect: status=healthy, db_ms<2000, pool.max_size>=50

time curl -sS -X POST https://japapmessenger.com/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"x@x","password":"x","captcha_id":"JAPAP_E2E_BYPASS_2026","captcha_answer":"0"}'
# Expect: HTTP 401 in <1.5s. If it hangs → pool is saturated, page on-call.
```

---

## Iteration changelog

Each iteration has a dedicated block in `/app/memory/PRD.md`. Highlights:

| Iter | Focus                                                                           |
| ---- | ------------------------------------------------------------------------------- |
| 64   | Admin Messaging Center + Resend webhooks (Svix)                                 |
| 66   | Individual email targeting + safe-send kill switches + Step 2 controlled batch  |
| 67   | Crypto Staking soft launch (MIR rebrand, admin endpoints)                       |
| 68   | Admin Staking UI + Neon pooler fix (`statement_cache_size=0`)                   |
| 68b  | Removed duplicate "Crypto" menu; live admin↔user plan sync                      |
| 69   | PWA installable (manifest, SW, icons) + Install prompt (Android + iOS)          |
| 70   | Web Push (VAPID), `/profile` opt-in, fan-out on tips / money / messages / …     |
| 71   | **Replaced VAPID with OneSignal** — single system for transactional + campaigns |
| 72   | 11-language UI (EN·FR·PT·ES·AR·SW·LN·YO·HI·BN·TA), single source of truth, RTL |
| 73   | Auto-detect signup language (navigator + country + CF-IPCountry + Accept-Language) |
| 74   | Auto-detect wallet currency at signup (country-mapped) + extended /preferences     |
| 76   | Feed comments bug fix + external share modal (WhatsApp / Facebook / copy link)   |
| 77   | Open Graph SSR (`/api/og/post/:id`) + cover (3:1) & avatar (1:1) crop with `react-easy-crop` + social counters |
| 78   | Privacy & Settings page (account visibility, post visibility, follow approval) + OneSignal follower notifications |
| 79   | Full purge of hardcoded French strings across Auth / Profile / Services / Feed |
| 80   | Deep-link post-login redirect + Services/Feed empty-state translations          |
| 81   | "Suggested for you" follow suggestions on single-post pages                     |
| 82   | Admin Messaging Batch & Safety + visual referral tiers editor                   |
| 82/83 | Wheel of Fortune v2 — engagement engine, 30-day cycle, 10 000 pts + 25 days, Turnstile anti-bot |
| 105–115 | Tap Challenge backend-authoritative + Quiz JAPAP (5Q sessions, anti-cheat, Turnstile) + admin **Games & Engagement** dashboards |
| 108  | **AI Error Monitor** (FE+BE pipeline, dedup `error_groups`, admin dashboard with Claude RCA) |
| 118  | Quiz UX phase 2 — global vs per-question timer, auto-advance, ✅/❌ live reveal, anti-bruteforce |
| 120  | Quiz dynamic per-question pacing + confetti + sounds                            |
| 122  | Quiz **Mode Apprentissage** (highlight correct answer on mistake, admin toggle) |
| 125  | Quiz **Champion par Pays** Phase 3.A/B/C — Free + Paid (atomic JAPAP escrow, 10% commission, 24h expiry) + notifications + frontend pages |
| 127–129 | Phase 3.D — WhatsApp viral share `ChallengeShare`, auto-promote/expire scheduler `quiz_champion_scheduler.py`, ledger-backed admin KPIs |
| 130  | **Phase 3.E — Anti-répétition + Défi quotidien + Génération IA** — `quiz_question_picker.py` (smart priority: never-seen > seen-old > fallback, configurable distribution 50/20/15/15 Africa/Sport/Econ/World), `daily_quiz_streak`, `quiz_ai_generator.py` (Claude Sonnet 4.5, distribution-aware insert) |
| 131  | Quiz Duel **tiebreaker temps** (≥0.20s diff) + `CompletedDuelView` (avatars + scores + 🏆 + WhatsApp + Rejouer) |
| 132  | Quiz Duel **viralisation** — push notif au challenger sur completion (3 variantes), `POST /duel/{token}/rematch` (rôles inversés, auto-discover dernier run), `GET /duel/me/rank` (top X% percentile sur 30j) |
| 133  | **Transport JAPAP rider/driver MVP** — téléphone client + avatar visibles au chauffeur après accept (`ClientContactCard` + Call/WhatsApp), `GET /transport/{ride}/tracking` léger (eta + distance + stage), statut distinct `cancelled_by_driver`, timer admin `transport_rider_cancel_after_seconds=60` |
| 134  | **P0 stability blast** — Quiz écran blanc post-Q5 (closure scoping `isDaily`), Wheel 500 (datetime tz-naive vs aware coercion), Tap "Soumission impossible" (race `setRun` vs timer → `runIdRef`), **`ErrorBoundary` global** (FR UI + Réessayer + Accueil + sendBeacon), defensive fallbacks (jamais `return null`) |
| 135  | Tap backend authoritative audit (run_id race fix iter134 + admin clamp test propagated runtime) + **Admin draft sync** (`setDraft(r.data.config)` après save → fin de l'illusion "valeur non persistée") |
| 136  | **CRITICAL** — Bouton "Enregistrer" Quiz admin → ErrorBoundary bleue. Root cause : `QuizUpdate` Pydantic `extra='forbid'` + 10 nouveaux champs Phase 3.E manquants → 422 → `detail` array d'objets → React Error #31. **Double fix** : 10 champs ajoutés au schéma + nouveau `utils/errorMessage.js` (`extractErrorMessage` handle string/array/object/network) |
| 137  | **AI Error Monitor wired E2E** — découverte que l'infra existait depuis iter108 mais peu utilisée. ErrorBoundary redirigé vers `/api/errors/report`. Nouveau `utils/axiosErrorReporter.js` interceptor global qui auto-reporte tous les 4xx/5xx (throttle 1/sig/60s). Module auto-deduit du pathname. Audit instantané : 43 groupes ouverts / 8114 occurrences captés |
| 138  | **Cleanup admin Erreurs IA** — nouveau `POST /api/admin/errors/bulk-action` (action groupée par filtres ou signatures explicites avec cutoff time-based anti-kill-legit). Boutons "Tout marquer corrigé" + "Tout ignorer" dans le dashboard. 44 groupes nettoyés en 1 click |
| 139  | **Auth UX fluidity + PWA bump** — autoComplete + inputMode mobiles, OTP double-submit guard, message resent i18n 11 langues, `extractErrorMessage` partout. Manifest `id` + `start_url=/?source=pwa` + `launch_handler.navigate-existing` + shortcut Jeux. SW v4-iter83 → **v5-iter139** invalide tous les caches anciens |
| 158/178 | **USD canonical migration** — wallet balances normalised to USD as the canonical store, FX layer for display in user-preferred currency (XAF / EUR / NGN / …). Idempotent migration block runs at boot |
| 237c | **Deferred-start workers** — video transcode, migration broadcast, Hubtel MoMo status check, seller reminder, crowdfunding recruit reminder, quiz champion scheduler — all kicked off +30s after uvicorn ready, so cold-start is fast and individual worker faults don't block the API surface |
| 239d/h/n | **Pro `VideoPlayer`** — IntersectionObserver autoplay, click-to-pause, fullscreen, scrubbable progress, mute toggle, mobile-safe `playsInline`, **auto aspect-ratio detection** via `onLoadedMetadata` (iter239n) |
| 240j-k | **Fiche Admin (7-tab user detail modal)** — overview, transactions, KYC, restrictions, notes, posts, crowdfunding tabs. `UserNameLink` applied globally so every user mention everywhere clicks through to the modal. `GET /api/admin/users/{user_id}/detail` is the single source of truth |
| 240l   | **Système Jury Crowdfunding** — `crowdfunding_jury_members` table (vote_multiplier, certificate_url, certificate_pdf_url), badge ⚖️ Jury on public profiles, stats endpoints, premium SVG certificate auto-generated and uploaded to Cloudflare R2, PDF fallback via `cairosvg` for LinkedIn-shareable downloads, real JAPAP logo embedded base64 + scannable QR |
| 240l-gamefix | **Admin "Activité Jeux" rebuilt from transactions** — old tables (quiz_game_history, fortune_wheel_spins, mini_spin_history) were unreliable/empty → `_build_game_activity_from_tx()` aggregates from `transactions` table (single SQL with CTEs) — Quiz/Roue/Spin/Staking + collapsible `all_transaction_types` debug breakdown for admins |
| 240l-msgfix | **Admin → user notification fix** — `} catch {}` without binding ate the real error → toast always said "Action échouée". Now `catch (e)` surfaces `(status · detail)` and backend validates 422 empty / 404 unknown user / auto-logs to `admin_user_notes` for audit trail |
| 240l-prodfix | **🚨 P0 production outage (24h) fix** — DB pool `max_size=10` exhausted in prod → every DB-bound route hung forever, `/api/health` stayed green so monitoring missed it. Pool now sized for production (`min=5/max=50, command_timeout=20s, max_inactive_connection_lifetime=60s`) + new `GET /api/health/db` readiness probe + client-side `axios timeout: 15000` on login |
| 240m   | **Video orientation portrait/paysage/carré** — `FeedPage/PostDetailPage/ChatPage` flipped `aspectRatio="16/9"` → `"auto"` (ReelsPage already did). `VideoPlayer` now detects `isPortrait/isSquare/isLandscape` from resolved ratio, caps `maxHeight: 80vh` on portrait (prevents 2133px-tall desktop layouts), `object-fit: contain` for portrait/square, exposes `data-orientation` attribute |

---

## Operational safety rails

A few hard rules — they exist because breaking them **has bitten us**:

1. **`messaging_real_send_enabled` defaults to `false`** and was forcibly
   reset to `false` on every deployment up to iter 82. From iter 82 onwards,
   the admin controls it **live from the UI** (*Admin → Messaging →
   Batch & Safety*) with a double-confirm alert.
2. **Referral tiers** are edited from the UI (*Admin → Parrainage → Paliers
   de parrainage*) since iter 82 — never through code.
3. **Staking hard-locks** (`staking_trading_enabled`, `_transfers_`,
   `_swaps_`, `_deposits_`, `_withdrawals_`) are silently ignored by
   `PUT /api/admin/staking/settings`. Staking soft-launch is **stake-only**.
4. **Never modify the MIR smart contract** or any Solidity. The product
   rebrands *on top* of an immutable on-chain contract via read-only
   `web3.py` sync + local DB mirror.
5. **`asyncpg.create_pool(..., statement_cache_size=0)`** — see
   [Database](#database).
6. **Frontend always calls `process.env.REACT_APP_BACKEND_URL`**; backend
   always reads `os.environ['MONGO_URL']` / `os.environ['DATABASE_URL']`.
   No hardcoded URLs.
7. **Backend authoritative for ALL game logic** (iter134/135) — Tap, Quiz,
   Wheel, Duel scores/timers/sessions are validated server-side. Frontend
   is display-only. Anti-cheat caps (`tap_max_taps_per_second` ×
   `tap_duration_seconds`) live in admin settings and are clamped at submit.
8. **Pydantic `extra='forbid'` on admin PUT endpoints** must always declare
   every new setting key (iter136 lesson). When adding a new admin setting:
   1) extend the Pydantic schema in `routes/admin_*.py`, 2) add it to the
   `*_DEFAULTS` dict in `services/games_settings.py` or
   `services/settings_service.py`, 3) add bounds in the validation block.
9. **Use `extractErrorMessage(error)` (`utils/errorMessage.js`) instead of
   `e.response?.data?.detail`** in toast.error / JSX render — Pydantic 422
   returns an array of `{type, loc, msg, input}` objects which crashes
   React with Error #31 if rendered directly (iter136 lesson).
10. **All FE crashes & 4xx/5xx auto-report** to `/api/errors/report`
    (iter137) via the global axios interceptor + ErrorBoundary. Check
    *Admin → Erreurs IA* daily; bulk-fix after each deployment via
    *Tout marquer corrigé* (iter138).
11. **DB connection pool must stay sized for production** (iter240l-prodfix
    P0 lesson). The `asyncpg.create_pool(...)` config in `database.py` is
    `min_size=5, max_size=50, command_timeout=20, max_inactive_connection_lifetime=60`.
    **Do not lower** these without testing 30 concurrent logins. The
    regression test in `tests/test_iter240l_prodfix_pool.py` locks the
    sizing in place.
12. **🛑 NEVER TOUCH PAYMENT ROUTES**. Hubtel MoMo, Paystack, USDT
    (TRC20/BEP20), Orange Money, Wave are real-money integrations
    serving real customers. Any change there must go through a dedicated
    integration playbook + signed-off staging test, *never* as a
    side-effect of another iteration. The string `"never_modify_payment"`
    appears in CI guards for this reason.
13. **Every admin action that mutates user state must auto-log to
    `admin_user_notes`** (iter240l-msgfix lesson). The pattern is a
    best-effort `INSERT … (note='[ACTION KIND] details')` inside the
    same connection, in a `try/except: pass` so the user-visible action
    never fails because the audit log did.
14. **`pages/*.js` callers of `<VideoPlayer />` must pass
    `aspectRatio="auto"`** unless they have a strong reason to force a
    ratio (e.g. a fixed 16:9 hero banner). Forcing `"16/9"` everywhere
    is what broke portrait videos for months (iter240m lesson).

---

## License & credits

Proprietary — © JAPAP 2026. All rights reserved.

Built with ❤️ on Emergent, with:
- The open Web Push stack (VAPID, W3C Push API, Notifications API)
- Tailwind / Shadcn UI / Phosphor Icons
- Neon PostgreSQL, Resend, Web3Modal, wagmi
- OneSignal (transactional + campaigns), Cloudflare Turnstile, NowPayments
- LiveKit (A/V calls), h3 (geo-indexing), recharts
- Claude Sonnet 4.5, Gemini 2.5 Flash, GPT-5.2 via the Emergent Universal LLM Key

For questions, incidents, or feature requests, ping the JAPAP ops team
or open a thread in the internal `#japap-dev` channel.
