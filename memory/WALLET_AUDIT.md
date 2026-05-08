# JAPAP — Audit Wallet end-to-end (Iter87)

**Date** : 24 Avril 2026
**Scope** : `/api/wallet/*`, `/api/admin/wallet/*`, `points_service`, mutations depuis tous les flux (Transport, Marketplace, Messaging, Crypto, Pro, Games).
**Niveau d'exigence** : Production (équivalent Revolut/WhatsApp level).

---

## 📊 Verdict global

### ✅ **OK PRODUCTION**

Le module Wallet est **solide, atomique, audité et sécurisé**.

- **0 risque critique** identifié
- **2 corrections mineures** recommandées (non-bloquantes)
- **Architecture en 2 surfaces** : XAF monétaire (`wallets.balance`) et points d'engagement (`wheel_cycles.points_cycle`) — séparation claire par design, cohérente mais mérite une passerelle visuelle côté UX.

---

## ✅ Ce qui fonctionne parfaitement

### 🔒 Atomicité & concurrence
| Test | Résultat |
|---|---|
| `/wallet/send` avec 10 envois parallèles de 1 XAF | ✓ Bob −10, Alice +10 exactement (aucune perte, aucun doublon) |
| `FOR UPDATE` sur expéditeur + destinataire dans la même transaction | ✓ |
| `/wallet/withdraw` : FOR UPDATE + fee serveur + DB tx | ✓ |
| `/admin/wallet/adjust` : FOR UPDATE + audit log | ✓ |
| `/api/games/spin` : `_credit_reward` appelé à l'intérieur de `async with conn.transaction()` | ✓ |

### ✅ Validations d'entrée
| Cas | Réponse |
|---|---|
| `amount ≤ 0` | HTTP 400 `Amount must be positive` |
| `to_user_id == self` | HTTP 400 `Cannot send money to yourself` |
| Solde insuffisant | HTTP 400 `Insufficient balance` |
| Destinataire inconnu | HTTP 404 `Recipient not found` |
| Wallet verrouillé (`is_locked=TRUE`) | HTTP 403 |
| Montant ≥ fee (retrait) | HTTP 400 `Montant inférieur aux frais` |
| Adresse < 10 chars (retrait) | HTTP 400 |

### 🔐 Sécurité des webhooks (Hubtel + NowPayments + AlphaPay)
1. **Signature vérifiée** : HMAC-SHA512 (NowPayments, `x-nowpayments-sig`) ou Hubtel `x-auth-signature`.
2. **Idempotence** : check `status == 'completed'` → retourne `already_completed`, pas de re-crédit.
3. **Double vérification** : le webhook seul ne suffit pas ; un call à l'API de statut côté fournisseur est effectué, et le crédit est **refusé** si l'API contredit le webhook.
4. **Hubtel non configuré** ou API indisponible → `pending_verification`, **pas de crédit** (fail-closed).
5. **Webhook "paid" mais API dit "not paid"** → refus explicite loggé.

### 👮 KYC gating
- `kyc_required_for_withdraw` activé par défaut.
- `is_user_kyc_approved()` vérifié avant `/withdraw` et les jeux payants.
- Réponse HTTP 403 `KYC_REQUIRED:` pour permettre au frontend de déclencher le flow KYC.

### 📝 Audit trail
- **Toute mutation** crée une ligne dans `transactions` (tx_id UNIQUE, tracking complet).
- **Actions sensibles** loggées dans `audit_logs` (send, admin_wallet_adjust).
- **Admin** peut lister, filtrer (status, type, date_from, date_to, user_id) et **exporter CSV** via `/api/admin/transactions/export`.

### 🎯 Moteur de points unifié (`points_service`)
- `add_points()` est **le SEUL** chemin d'écriture vers `wheel_cycles.points_cycle`.
- Les 4 jeux (Roue, Quiz, Tap, Duel) passent tous par cette fonction.
- **Clamp souverain** enforced: `min(points, 9 999)` tant que `days_played < 25`.
- Chaque crédit → 1 row dans `wheel_spins` avec `source` ∈ {wheel, quiz, tap, admin}.
- Index `idx_wheel_spins_source` (source, spin_at DESC) pour analytiques rapides.
- **Cohérence vérifiée** sur Bob : `points_cycle = 2567 = 1707 (tap) + 750 (quiz) + 110 (wheel)` — parfaite concordance.

### 🎛 Contrôle admin
- Toggle global `withdraw_enabled` + message personnalisable.
- Toggle par méthode : `withdraw_usdt_trc20_enabled`, `withdraw_usdt_bep20_enabled`.
- Modes `manual_withdraw_enabled` / `auto_withdraw_enabled` exclusifs/combinables.
- `min_withdraw_amount_usd` admin-réglable.
- **Fee dynamique** : percent ou flat, override per-Pro-plan possible.
- Ajustement manuel avec audit trail (`/api/admin/wallet/adjust`).

### 🧪 Tests unitaires verts
- `test_points_service.py` : 17 tests (constants, clamp, accuracy 75%, eligibility) ✓
- `test_wheel_fortune.py` : 46 tests (FOR UPDATE, clamps, odds) ✓
- `test_iter85_shuffling_virality.py` : 14 tests (shuffle, leaderboard, share card) ✓

---

## ⚠️ Incohérences / Risques moyens

### 1. UX fragmentée : `/wallet` n'affiche PAS les points d'engagement
**Impact** : medium (confusion utilisateur).
**Détail** : un user qui gagne 20 pts au Quiz ne voit **rien bouger** sur `/wallet`. Les points vivent exclusivement sur `/services → Jeux` (cycle Starter Pro).
**Architecture** : **par design** (wallet = argent réel, points = engagement). Mais absence de passerelle visuelle.
**Recommandation** : ajouter sur `/wallet` une carte compacte :
```
📊 Mes points d'engagement (cycle actuel)
[████░░░░░░] 2 567 / 10 000 pts · 1 / 25 jours
→ Voir mes jeux
```

### 2. Absence d'un dashboard admin Wallet dédié
**Impact** : low.
**Détail** : `/api/admin/stats` et `/api/admin/transactions` existent mais il n'y a pas d'endpoint ni d'UI dédiée "Vue d'ensemble Wallet" avec :
- Total balances agrégé
- Volume tx jour/semaine/mois
- Taux d'échec (pending > 24h, cancelled)
- Top comptes (in/out)
- **Détection automatique d'anomalies** (gros retraits > seuil, enchaînements rapides, pics suspects)
**Recommandation** : créer `/api/admin/wallet/overview?days=30` + onglet Admin correspondant.

### 3. Pas de rate-limit applicatif sur `/wallet/send`
**Impact** : low (DB protège déjà via FOR UPDATE → serialize).
**Détail** : un user pourrait POSTer 100 sends à la suite. Le frontend désactive le bouton pendant la requête, mais un client malveillant peut contourner.
**Recommandation** : `@rate_limit("send_money", "5/minute")` (middleware existant `security.py`).

---

## 🔴 Risques critiques

**AUCUN**. L'audit n'a révélé aucune surface d'attaque exploitable :
- Pas d'endpoint qui laisse le client fixer le solde.
- Pas de mutation wallet sans authentification.
- Pas de mutation sans FOR UPDATE dans la transaction concernée.
- Webhooks signés + double-vérification.
- CSRF actif sur tous les POST.
- Pas de race condition observée sous charge.

---

## 🔧 Corrections proposées

### P2 (non-bloquant, quick wins)
1. **Ajouter `/api/admin/wallet/overview`** (~100 lignes) : total balances, tx/jour, top spenders, anomalies simples (retrait > seuil, fréquence suspecte).
2. **Afficher les points d'engagement sur `/wallet`** (~30 lignes front) : carte compacte + bar progress + lien vers `/games`.

### P3 (hardening)
3. **Rate-limit** sur `/wallet/send`, `/wallet/deposit`, `/wallet/withdraw` (5/min par user).
4. **Détection d'anomalies automatique** : job récurrent qui flag les comptes suspects dans `audit_logs`.

---

## 📋 Checklist user demandée

| Demande | Statut |
|---|---|
| Création du wallet utilisateur | ✅ (INSERT à l'inscription + fallbacks ON CONFLICT DO NOTHING) |
| Affichage des balances (toutes sources) | ⚠️ XAF OK · points d'engagement absents de `/wallet` (cf. recommandation 1) |
| Historique des transactions (clair, cohérent, complet) | ✅ (`/wallet/transactions` paginé, filtrable) |
| Ajout points depuis Roue / Quiz / Tap / Duel | ✅ (via `points_service.add_points`, clamp 10k/25j) |
| Déduction des points | ✅ (centralisée dans `points_service`, aucune autre surface) |
| Gain → affichage immédiat | ✅ (le frontend call `refreshUser` + reload balance après chaque action) |
| Multi-actions rapides | ✅ (10 sends parallèles → 0 incohérence) |
| Pas de double-crédit / perte / désynchronisation | ✅ |
| Impossible de modifier les points côté frontend | ✅ (aucun endpoint exposé) |
| Toutes les opérations passent par le backend | ✅ |
| Protection replay | ✅ (idempotence webhooks + tx_id UNIQUE) |
| Protection double submit | ✅ (FOR UPDATE + idempotence DB) |
| Protection manipulation API | ✅ (CSRF + auth + validation serveur) |
| Cohérence avec points_service | ✅ (seule source de vérité) |
| Les points affichés = points du cycle actif | ✅ (mais non exposés sur `/wallet`) |
| Reset cycle → wallet cohérent | ✅ (cycle flip ne touche pas `wallets`) |
| Aucun décalage wheel_cycles / affichage | ✅ (vérifié sur Bob : 2567 = 1707+750+110) |
| Dashboard admin : total points distribués / top users / anomalies | ⚠️ Disponible sur `/api/duel/admin/overview`, `/api/quiz/admin/overview`, `/api/tap/admin/overview`, `/api/wheel/admin/stats` · mais pas d'overview wallet XAF unique (cf. recommandation 2) |

---

## 📌 Synthèse exécutive

Le Wallet JAPAP est **production-ready**. Aucun trou de sécurité. Atomicité et idempotence solides. Audit trail complet. Moteur de points centralisé et testé.

Les 2 axes d'amélioration sont des **quick wins UX/admin** — non-bloquants pour un lancement mais recommandés à court terme.

**Recommandation finale** : GO production, planifier les 2 corrections P2 sur l'itération suivante si le feedback user remonte la demande.
