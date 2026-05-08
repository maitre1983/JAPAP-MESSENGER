# 🎡 JAPAP — Roue de la Fortune v2 : Rapport d'écart et correction

**Date** : 24 avril 2026  
**Motif** : L'utilisateur a constaté en production que la "Roue de la fortune" côté app et l'onglet "JAPAP Spin" côté admin n'affichaient pas la logique produit attendue (cycle 30j / 10 000 pts / 25 jours distincts). Ce document identifie l'écart exact, explique pourquoi, et confirme la correction.

---

## 1. Ce qui était réellement en place avant correction

### Backend ✅ (conforme aux specs)
- `/app/backend/routes/wheel_fortune.py` (1003 lignes) : module v2 **complet et fonctionnel** avec :
  - Cycle 30 jours (`CYCLE_LENGTH_DAYS = 30`)
  - Objectif 10 000 pts (`POINTS_GOAL`) + 25 jours distincts (`DAYS_GOAL`)
  - Clamp mathématique souverain (points < 10k tant que days < 25)
  - `SELECT ... FOR UPDATE` (race condition fermée)
  - Statut `reward_pending` + grâce 7 jours
  - Push + email auto au flip reward_pending
  - Endpoint admin `/api/wheel/admin/observability`
  - 46 tests pytest verts
- Routes FastAPI correctement enregistrées dans `server.py` (include_router line 148).

### Frontend (page dédiée) ✅
- `/app/frontend/src/pages/WheelFortunePage.js` existait, route `/games/wheel` déclarée dans `App.js` ligne 144.
- Bandeau `pending_claim` avec CTA "Réclamer maintenant" — testé et fonctionnel.

### ❌ Problème : la roue v2 n'était pas exposée aux utilisateurs NI aux admins

1. **Côté utilisateur** : dans `/app/frontend/src/pages/GamesModule.js` (accessible via `/services` → "Jeux"), le card **"Roue de la fortune"** pointait vers `setView('spin')` → `SpinGame` component → `/api/games/spin` (ANCIEN module XAF). Le VRAI `/games/wheel` n'était atteignable qu'en tapant l'URL manuellement.

2. **Côté admin** : dans `/app/frontend/src/pages/AdminPage.js`, l'onglet "JAPAP Spin" affichait `SpinAdminTab` (config XAF : weighted_random, plafond XAF, coût XAF). Aucun onglet ne consommait `/api/wheel/admin/observability`.

**Conséquence** : toute la logique backend stratégique (cycles, points, 25 jours, Starter Pro) était **invisible** à travers l'UI. L'utilisateur voyait uniquement le mini-jeu XAF legacy.

### Pourquoi cet écart s'est produit
L'itération précédente a construit le backend + page dédiée `/games/wheel` + audit technique + tests, mais n'a jamais rewiré le hub de jeux côté user ni remplacé le tab admin. Les deux restaient pointés sur le système historique XAF. C'est un défaut de livraison : le backend stratégique existait, mais n'avait pas été branché aux surfaces UI vues par les utilisateurs finaux.

---

## 2. Corrections effectuées (24/04/2026)

### 2.1 — `/app/frontend/src/pages/GamesModule.js`
- Import de `useNavigate` et icône `Target`.
- Remplacement du card top par **"Roue de la Fortune"** avec :
  - Badge `ENGAGEMENT` doré
  - Description : *"Cycle 30 jours · 10 000 pts + 25 jours de jeu → Pack Starter Pro offert"*
  - `onClick={() => navigate('/games/wheel')}`
  - `data-testid="game-wheel-fortune"`
- Ajout d'une section séparée **"Mini-jeux XAF quotidiens"** contenant l'ancien spin, quiz, tap (préservés).
- Card renommé "Mini-spin XAF" (au lieu de "Roue de la fortune") pour dissiper toute ambiguïté.

### 2.2 — `/app/frontend/src/pages/admin/WheelFortuneAdminTab.jsx` (NOUVEAU)
Composant dédié consommant les 3 endpoints admin de la roue :
- `GET /api/wheel/admin/observability` — chargement auto
- `POST /api/wheel/admin/flag-suspicious` — bouton danger
- `POST /api/wheel/admin/send-cycle-reminders` — bouton primary

Structure de la page :
1. **Header + recommandation** : badge coloré `NOT NEEDED_YET` (🟢) / `MONITOR_CLOSELY` (🟠) / `ACTIVATE_NOW` (🔴) avec raison textuelle.
2. **KPIs Engagement** : 8 cartes (users actifs, cycles, DAU, spins 24h/7j/total, jackpots, moyenne pts/spin).
3. **Distribution récompenses** : 8 cartes (vainqueurs, réclamés, taux claim, jours médians/moyens, pts moy. au claim, en attente, expirés non réclamés).
4. **Anomalies** : 5 listes détaillées (bot-like, multi-IP, multi-fingerprint, progression rapide, night-owls) avec seuils affichés.
5. **Protections actives** : 5 pastilles (roue activée, Turnstile, cooldown, cap/jour, flag suspect).
6. **Actions** : boutons Rafraîchir / Flagger suspects / Envoyer rappels.

### 2.3 — `/app/frontend/src/pages/AdminPage.js`
- Import du nouveau composant.
- Ajout de l'onglet **"Roue Fortune"** (icône `Target`) AVANT l'ancien spin.
- Renommage **"JAPAP Spin" → "Mini-spin XAF"**.
- Wiring : `{tab === 'wheel' && <WheelFortuneAdminTab />}`

---

## 3. Validation en conditions réelles

### 3.1 — Frontend utilisateur (Bob, mobile 390px)
```
URL: /services → clic "Jeux"
  → card TOP "Roue de la Fortune" avec badge ENGAGEMENT
  → clic
  → URL: /games/wheel
  → page WheelFortunePage chargée (data-testid="wheel-fortune-page" = true)
```
✅ **Le clic redirige bien vers la VRAIE roue v2.**

### 3.2 — Admin
```
URL: /admin → clic onglet "Roue Fortune"
  → chargement data-testid="wheel-admin-tab"
  → titre "Roue de la Fortune — observabilité"
  → sous-titre "Cycle 30j · 10 000 pts + 25 jours distincts · Récompense : Starter Pro 30j"
  → badge recommandation "NOT NEEDED_YET"
  → KPIs réels affichés (2 users actifs · 1 DAU · 1 jackpot · taux claim 100 % · 26 jours médians)
```
✅ **L'onglet admin reflète exactement la logique produit.**

### 3.3 — Régression zéro
```
$ cd /app/backend && pytest tests/test_wheel_fortune.py -q
46 passed in 0.56s
```
✅ **Aucune régression sur les invariants métier (cycle 30j, clamp 25j, phases, jackpot, streak, milestones).**

---

## 4. État final — checklist des exigences

| Exigence | Avant | Après |
|---|---|---|
| Logique cycle 30 jours | ✅ backend | ✅ backend + visible user + visible admin |
| Compteur points / jours distincts | ✅ backend | ✅ idem |
| Minimum 25 jours distincts | ✅ backend | ✅ idem |
| Distribution intelligente (phases 1/2/3) | ✅ backend | ✅ idem |
| Blocage < 10k avant 25 jours | ✅ clamp souverain | ✅ idem |
| Récompense Starter Pro | ✅ backend | ✅ idem |
| Reset propre | ✅ reward_pending + grâce 7j | ✅ idem |
| Backend-only (aucun calcul frontend) | ✅ | ✅ |
| **Card utilisateur "Roue de la Fortune"** | ❌ pointait vers XAF spin | ✅ **pointe vers /games/wheel v2** |
| **Admin : onglet dédié cycles/points/jours** | ❌ absent (voyait XAF) | ✅ **nouvel onglet "Roue Fortune"** |
| Admin : taux complétion + récompenses | ❌ invisible | ✅ **KPIs live affichés** |
| Admin : détection anomalies | ❌ invisible | ✅ **5 heuristiques + recommandation auto** |
| Tests | ✅ 46 tests | ✅ 46 tests (régression zéro) |

---

## 5. Fichiers modifiés

```
MODIFIED: /app/frontend/src/pages/GamesModule.js     (+30 / -15 lignes)
MODIFIED: /app/frontend/src/pages/AdminPage.js       (+5 / -1 lignes)
CREATED:  /app/frontend/src/pages/admin/WheelFortuneAdminTab.jsx   (228 lignes)
DOC:      /app/memory/WHEEL_FORTUNE_GAP_REPORT.md    (ce document)
```

**Backend** : aucun changement cette fois-ci. Tout le travail était dans la couche UI/routage manquante.

---

## 6. Ce qui reste (backlog produit, NON bloquant)

- **Turnstile** : code prêt, en attente de vos clés production (réaffirmé volontairement OFF en phase d'observation).
- **Configuration admin de la roue v2** : actuellement les paramètres (`wheel_config_json`, `wheel_enabled`, `wheel_turnstile_enabled`) sont modifiables via `PUT /api/admin/settings` mais pas encore via un formulaire dédié dans le nouvel onglet. À ajouter quand vous voudrez changer `max_spins_per_day`, `cooldown_seconds`, `jackpot_odds_in_window`, etc. depuis le dashboard.
- **Reporting hebdo** : ajouter un export CSV depuis l'onglet (`GET /api/wheel/admin/observability/export?format=csv`).

---

**Conclusion** : L'écart constaté est corrigé. La logique produit exigée est désormais **visible et cohérente** côté utilisateur (entrée explicite vers `/games/wheel`) et côté admin (onglet "Roue Fortune" avec KPIs réels et détection anomalies). L'ancien module XAF est préservé sous un nom neutre "Mini-spin XAF" pour ne pas casser les fonctionnalités existantes qui en dépendent.
