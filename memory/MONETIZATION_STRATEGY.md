# JAPAP — Stratégie de Monétisation
_Document stratégique · iter90 · 24/04/2026_
_Pas de code — vision + flows + recommandations concrètes_

---

## 🎯 Résumé exécutif

JAPAP est aujourd'hui une **machine d'engagement** : Messaging + Feed social + Wallet + Jeux (Roue + Quiz + Tap + Duel) + Marketplace + Crypto Hub + Connect. La rétention est forte (cycle 30 jours, 10 000 pts + 25 jours + 75 % quiz = Starter Pro).

La question stratégique n'est pas _"comment monétiser cette app ?"_ — l'infrastructure de paiement (Wallet atomique + webhooks NowPayments + Hubtel + 4 méthodes de retrait) est déjà en place. La question est : **quels leviers actionner en priorité pour maximiser le revenu par utilisateur actif sans casser l'engagement ?**

Ce document pose 5 leviers classés par **impact attendu / effort technique** :

| Priorité | Levier | Impact | Effort | Horizon |
|:---:|---|:---:|:---:|:---:|
| **P0** | Frais de transaction Wallet (send/withdraw/swap) | 🟢 très haut | 🟢 faible (infra existe) | 1-2 semaines |
| **P0** | Abonnement JAPAP PRO (paywall features premium) | 🟢 très haut | 🟡 moyen (UX + features) | 3-4 semaines |
| **P1** | Marketplace commission + promoted listings | 🟡 haut | 🟡 moyen | 4-6 semaines |
| **P1** | Jeux premium (boosters, entrées Duel payantes) | 🟡 moyen | 🟢 faible | 2 semaines |
| **P2** | Publicité native (Feed + Reels) | 🟡 moyen | 🔴 élevé (self-serve admin) | 6-8 semaines |

---

## 1. 💰 Modèle économique — Panorama

### 1.1. Sources de revenu identifiées

#### A. Wallet (fintech-like)
JAPAP gère déjà les USDT (dépôt, retrait, send, swap potentiel). Chaque transaction peut supporter des frais :
- **Send P2P** : frais fixe ou pourcentage (actuellement 0 XAF — opportunité).
- **Withdraw** : frais déjà configurés (`withdraw_fee_mode` / `withdraw_fee_value` côté admin). Par plan PRO, ces frais sont réduits → levier d'upsell PRO.
- **Deposit** : pas de frais entrants (standard fintech) — garder ainsi.
- **Swap XAF ↔ USDT** (futur) : spread de change 1-2 %.

**Estimation revenu annuel potentiel** (assumptions : 10 000 MAU, 30 % actifs Wallet, tx moyenne 5 USD, 20 tx/mois/user) :
- 2 000 users × 20 tx × 12 mois × 5 USD × 0.5 % frais = **12 000 USD / an** (base)
- avec 5 % de frais swap/withdraw = **60 000 USD / an**

#### B. Abonnement JAPAP PRO
Déjà esquissé (`is_pro` flag, table `pro_plans`). Leviers premium :
- Frais retrait réduits (1 % vs 3 %).
- Badge PRO visible dans Feed + Messenger.
- Limites supérieures : group size, file upload, messages archivés.
- Accès avancé Roue : bonus jackpot +10 %, multiplicateur points.
- **Analytics Feed** : portée, insights posts, best time to post.
- Appels vidéo HD / sans pub.

**Pricing recommandé** : 2 tiers
- **PRO** — 2 990 XAF/mois (≈ 5 USD) · core premium
- **PRO+** — 7 990 XAF/mois (≈ 13 USD) · business features (boost campagnes, API marketplace)

**Estimation** : 10 000 MAU × 3 % conversion PRO × 5 USD × 12 = **18 000 USD / an** (conservateur)
Avec 1 % PRO+ = **+15 600 USD / an** → total **~ 33 600 USD / an**

#### C. Marketplace
Commission sur chaque vente + promoted listings + subscription "vendeur PRO".
- Commission standard : 5 % (inspiré Etsy, eBay).
- Promoted listings : 500 XAF / 24h boost.
- Badge "Vendeur Vérifié" : 5 000 XAF one-shot (KYC enhanced).

#### D. Jeux
Monétisation douce (ne JAMAIS casser l'engagement gratuit core) :
- Entrée **Duel** avec enjeu : paie 100 pts pour challenger, winner takes 150 (house retains 50 = rake 25 %).
- **Boosters** éphémères : "2x points next 24h" pour 500 XAF.
- **Spins supplémentaires** : max 3/jour gratuits → 4e+ à 200 XAF.

⚠️ **Règle d'or** : ne jamais payer pour gagner de façon permanente. Only pay to accelerate or replay.

#### E. Publicité native
- Ads injectées dans Feed et Reels (déjà framework ads.py existant).
- CPM 2-5 USD sur marchés FR / africains.
- Self-serve admin dashboard : campagnes auto-ciblées par pays / langue / segment.

#### F. Services B2B
- API JAPAP Connect pour opérateurs telecom (partenariat data).
- SMS transactionnels en marque blanche (via Hubtel).
- Messaging worker location → solutions KYC/OTP pour fintech partenaires.

### 1.2. Modèle recommandé

**Freemium hybride** :
1. Coeur (chat, feed, wallet basic, 1 run Tap/jour, 3 spins/jour) → **gratuit**
2. Abonnement PRO → **revenus récurrents**
3. Frais d'usage Wallet → **revenus transactionnels**
4. Marketplace commission → **revenus à l'échelle**
5. Publicité → **couche de revenu sur gratuit**

---

## 2. ⚙️ Architecture technique — Points de paiement

### 2.1. Infrastructure existante (à exploiter)

| Composant | État | Rôle pour la monétisation |
|---|:---:|---|
| Wallet atomique (FOR UPDATE) | ✅ prod | Déducteur unique pour tout achat in-app |
| Webhooks NowPayments | ✅ prod | Entrée USDT (deposits) |
| Hubtel Card Checkout | ✅ prod | Entrée XAF (carte bancaire africaine) |
| Admin settings (`admin_settings`) | ✅ prod | Config dynamique des frais/montants sans redéploiement |
| `transactions` table | ✅ prod | Audit trail 100 % des mouvements |
| `wallet_transactions` idempotence | ✅ prod | Zéro double-débit |
| `pro_plans` + `is_pro` flag | ✅ prod (sous-utilisé) | Support abonnement déjà câblé |
| Admin dashboards Recharts | ✅ prod | KPIs revenus livrables en 1 semaine |

### 2.2. Points d'injection paiement (flux)

```
User action ──► Pre-check balance ──► transaction (FOR UPDATE) ──► credit/debit wallet ──► emit event ──► update UI
                                          │
                                          └─► admin_alerts si suspicious
```

**Mapping levier → endpoint :**
- **PRO subscription renewal** → `/api/pro/subscribe` (débit mensuel auto Wallet)
- **Tx fees** → injection dans `/api/wallet/send` + `/api/wallet/withdraw` (les endpoints existent, ajouter `fee = amount * rate`)
- **Marketplace order** → `/api/marketplace/orders` (commission = price × 0.05 créditée au wallet JAPAP house)
- **Duel enjeu** → extension `/api/duel/create-from-quiz` avec paramètre `stake`
- **Boosters** → nouveau `/api/store/buy` qui crée un `user_boosters` row + débit

### 2.3. Sécurité transactionnelle

- Toutes les opérations de monétisation passent par le même pattern `async with conn.transaction()` + `FOR UPDATE`.
- Rate-limiting actif sur `/wallet/send` (5/min) — à étendre à `/pro/subscribe` (3/min) pour éviter spam.
- Admin alerts automatiques sur abus (achat > 100 USD/jour/user, chaînes de boosters successifs).

### 2.4. Comptabilité / Pilotage

Créer 2 comptes "house" internes :
- `house_fees_account` (frais wallet)
- `house_commission_account` (marketplace)

Chaque transaction monétisée **crédite** un de ces 2 comptes avec une `type` dédiée (`fee`, `commission`). Le dashboard admin "Revenue" consomme ces comptes pour les KPIs.

---

## 3. 📊 Conversion & UX — Points critiques

### 3.1. Où l'utilisateur paie

**Moments "hot" identifiés** (maximum d'intention d'achat) :

1. **Juste après un gain de jeu** → proposer PRO (multiplicateur x2 pts). Le user est dans un état émotionnel favorable.
2. **Avant de perdre un streak** (roue jour 24 sans cycle complet) → "Récupérez votre cycle avec PRO".
3. **Lors du retrait USDT** → comparaison frais plan gratuit vs PRO ("Économisez 15 USD en passant PRO aujourd'hui").
4. **Dans le chat groupé** (après 7j) → "Débloquez l'archive des messages avec PRO".
5. **Dans la Marketplace** → "Boostez votre annonce pour 500 XAF, +400 % de visibilité".

### 3.2. Pourquoi il paie

**Motivations par persona** :

| Persona | Trigger d'achat | Levier prioritaire |
|---|---|---|
| **Joueur fidèle** | Ne pas rater le Starter Pro | PRO + boosters |
| **Trader USDT** | Minimiser frais retrait | PRO (frais 1 %) |
| **Vendeur Marketplace** | Vendre plus vite | Promoted listings + PRO+ |
| **Creator Feed** | Audience & métriques | PRO (analytics + badge) |
| **Business** | Outils messagerie pro | PRO+ (API + SMS) |

### 3.3. Comment optimiser la conversion

#### A. Paywall doux
Pas de bloqueurs de features core. Toujours montrer ce que PRO débloque (préviews blurées, compteurs). Exemple : "+ 15 messages archivés disponibles avec PRO".

#### B. Trial 7 jours gratuits
Activation en 1 clic (pas de carte). Récupération auto à J-1 par push + email. Conversion standard industrie : 35-55 % sur cohortes actives.

#### C. Tarification localisée
Afficher en XAF pour CM/CI/SN, en EUR pour FR/BE, en USD ailleurs (currency_detector.py déjà prêt).

#### D. Friction zéro du côté paiement
Paiement initial via **carte Hubtel** (3-clics mobile ready) OU via **USDT** (pour les users déjà Wallet-funded). **Pas de Stripe** en priorité (frais élevés + pas optimal Afrique).

#### E. Récurrence = auto-débit Wallet
Après l'inscription, le renewal se fait **automatiquement** depuis le wallet de l'user. Plus besoin de relancer une transaction Hubtel chaque mois. Friction = 0.

### 3.4. Anti-patterns à éviter

❌ Vendre des "points" directs (casse l'équité du système d'engagement).
❌ Bloquer le chat 1-to-1 sur PRO (baisse d'usage core).
❌ Publicité intrusive (pop-ups, full-screen). Garder le native feed + reels slot.
❌ Tarifs > 10 USD sur marché africain (ceiling psychologique).

---

## 4. 🔐 Risques & fraude

### 4.1. Vecteurs d'abus identifiés

| Abus | Probabilité | Impact | Parade |
|---|:---:|:---:|---|
| Stolen cards on Hubtel | 🟡 moyen | 🔴 élevé | KYC avant retrait + Cloudflare Turnstile + 3DS Hubtel |
| Chargebacks carte | 🟡 moyen | 🟡 moyen | Délai 72h avant crédit wallet sur top-ups carte |
| Multi-accounts PRO (trial abuse) | 🟢 faible | 🟡 moyen | Fingerprint device + IP + email domain blacklist |
| Self-dealing Marketplace (vendeur=acheteur) | 🟡 moyen | 🟡 moyen | Détection admin (déjà flagué `self-duel blocker`, même logique) |
| Rate-limit bypass sur achats | 🟢 faible | 🟡 moyen | slowapi middleware déjà en place (iter89) |
| Money laundering via send P2P | 🟢 faible | 🔴 élevé | KYC enhanced au-dessus de 500 USD cumulé/jour + admin alerts |

### 4.2. Protections requises AVANT activation monétisation

1. **Cloudflare Turnstile** sur `/api/pro/subscribe` et `/api/marketplace/orders` (déjà câblé, attend clés prod).
2. **KYC obligatoire au-delà d'un seuil d'achats cumulés** (ex. 100 USD / 7j) — extension du KYC withdraw existant.
3. **Admin alerts** automatiques sur achats > 50 USD/jour/user (pattern trigger_large_withdraw déjà en place — réplication).
4. **Ledger "house" immutable** : append-only, jamais d'UPDATE sur les lignes de frais/commissions.
5. **Dispute workflow** (tickets support → admin review → refund via admin endpoint).

---

## 5. 📈 Dashboard Revenus — Spec admin

### 5.1. KPIs temps réel

À construire dans un nouvel onglet admin `/admin → Revenus` :

#### A. Header KPIs (4 cards)
- **Revenu brut 7j / 30j** (XAF + USD converted)
- **ARPU** (revenu / utilisateur actif)
- **MRR** (Monthly Recurring Revenue — PRO subs)
- **% Commission Marketplace** (% du GMV capté)

#### B. Timeseries (Recharts LineChart)
- Revenu quotidien par source (PRO / Frais Wallet / Marketplace / Jeux / Ads)
- Y-axis double : XAF & nombre de transactions

#### C. Funnel (Sankey ou tableau)
- Visiteurs → Inscrits → Actifs → PRO trial → PRO payants → Renouvellements

#### D. Cohort retention
- Cohort mensuelle, rétention à J7 / J30 / J90
- Highlight : cohort qui a converti PRO vs non (ROI acquisition)

#### E. Top payeurs / Alertes
- Top 20 users revenus (avec drill-down)
- Alertes chargeback / refund / fraude détectée

### 5.2. Endpoints backend à créer

- `GET /api/admin/revenue/overview?days=N` → KPIs + breakdown sources
- `GET /api/admin/revenue/timeseries?days=N&granularity=day|week`
- `GET /api/admin/revenue/cohorts?month=YYYY-MM`
- `GET /api/admin/revenue/top-payers?limit=20`
- `GET /api/admin/revenue/mrr` (snapshot actif subs)

---

## 6. 🎯 Roadmap proposée (24/04/2026 → 30/06/2026)

### Phase 1 — Quick wins (2 semaines)
- [ ] Activer frais `/wallet/send` configurable admin (0.5 % par défaut)
- [ ] Ajuster frais `/wallet/withdraw` free-tier vs PRO (3 % vs 1 %)
- [ ] Créer `house_fees_account` + audit trail
- [ ] Dashboard admin "Revenue overview" basique (3 KPIs : revenue 7j/30j, ARPU, MRR)

### Phase 2 — PRO launch (4 semaines)
- [ ] Tunnel inscription PRO (intro, tiers, paiement Hubtel/USDT, trial 7j)
- [ ] Features premium critiques : badge, frais réduits, analytics Feed, Roue bonus
- [ ] Paywall doux sur 5 moments hot identifiés
- [ ] Auto-renewal via wallet + relances push J-3
- [ ] Dashboard admin "PRO funnel" (conversion, churn, LTV)

### Phase 3 — Marketplace monetization (4 semaines)
- [ ] Commission 5 % auto sur orders completed
- [ ] Promoted listings (boost 24h)
- [ ] Badge Vendeur Vérifié (KYC enhanced)
- [ ] Dashboard admin "GMV + commissions"

### Phase 4 — Jeux premium (2 semaines)
- [ ] Duels avec enjeu
- [ ] Boosters "+2x points" 24h
- [ ] Spins supplémentaires payants

### Phase 5 — Advertising (6-8 semaines)
- [ ] Self-serve campaign builder (déjà foundation ads.py)
- [ ] Ciblage langue / pays / segment
- [ ] Native slot Feed + Reels (1 ad / 5 posts)
- [ ] Billing par CPM + dashboard annonceur

---

## 7. 🔑 Recommandations concrètes

1. **Commencer par Phase 1** — frais Wallet. Zéro changement UX, capture immédiate de revenu, teste l'appétence.
2. **Ne pas lancer PRO tant que Marketplace n'est pas solide** — sinon double-feature-push qui dilue la proposition de valeur.
3. **Localiser les tarifs en XAF** (ton marché dominant) avec affichage équivalent USD pour fiabilité.
4. **Instrumenter DÈS LE DÉBUT** avec le dashboard Revenus. Sinon, impossible de piloter.
5. **Protéger avant d'activer** : Cloudflare Turnstile + KYC enhanced + admin alerts = non-négociables avant tout revenu.
6. **Trial 7 jours gratuit PRO** = standard fintech, conversion industry-proof, 0 friction.
7. **Auto-débit Wallet PRO renewal** = différenciation vs concurrents qui forcent carte à chaque mois.
8. **Transparence totale** dans l'UI : toujours montrer les frais avant validation (compliance + trust).

---

## 8. TL;DR pour le décideur

> JAPAP est une **rare exception** : plateforme d'engagement où l'infra paiement est **déjà construite et testée** avant que la monétisation ne soit activée. Cela permet un lancement **sans dette technique**, **sans risque transactionnel**, et **avec observabilité complète dès J0**.
>
> Le chemin recommandé : activer les **frais Wallet** en semaine 1 (revenus immédiats), lancer **PRO** en mois 2 (revenus récurrents), puis **Marketplace commission** + **Jeux premium** en mois 3-4. La **publicité** en dernier, quand l'audience justifie l'effort self-serve.
>
> **Risques principaux** : fraude carte (Hubtel 3DS + Turnstile requis) et KYC insuffisant sur grosses transactions. Les 2 ont déjà des parades câblées dans la codebase.
>
> **Potentiel revenu annuel réaliste** (10 000 MAU) : **60 000 – 120 000 USD** dans les 12 premiers mois post-activation (hors Marketplace scale).
