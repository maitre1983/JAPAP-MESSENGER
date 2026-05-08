# 🎡 JAPAP — Audit Technique & Correction de la Roue de la Fortune v2

**Date** : 23 avril 2026  
**Fichier principal** : `/app/backend/routes/wheel_fortune.py` (851 lignes après fix)  
**Tests** : `/app/backend/tests/test_wheel_fortune.py` (46 tests — ✅ 100 % passent)  
**Priorité** : 🔴 P0 — bloc stratégique monétisation / engagement / équité  

---

## 📌 TL;DR pour le décideur

| Règle métier | État initial | État final |
|---|---|---|
| Cycle de 30 jours calendaires | ✅ Conforme | ✅ Conforme |
| Seuil 10 000 points | ✅ Conforme | ✅ Conforme |
| Minimum 25 jours distincts | ✅ Conforme | ✅ Conforme |
| **Impossible d'atteindre 10k en 24 jours** | ❌ **VIOLÉ** (11 200 pts possibles) | ✅ **Conforme** — clamp souverain |
| Logique 100 % backend | ✅ Conforme | ✅ Conforme |
| Sécurité anti-fraude (Turnstile + fingerprint + burst + suspicious) | ✅ Conforme | ✅ Conforme |
| Sérialisation des spins concurrents | ⚠️ Race condition possible | ✅ Conforme — `SELECT ... FOR UPDATE` |
| Attribution Starter Pro | ✅ Conforme | ✅ Conforme |
| Reset propre en fin de cycle | ⚠️ Récompense perdue si non réclamée avant expiration | ✅ Conforme — fenêtre de grâce 7 j |

**Statut global** : ✅ **CONFORME après correction**. Tous les gaps identifiés ont été bouchés par du code testable.

---

## 1️⃣ État initial — ce que l'audit a trouvé

### 1.1 Ce qui était déjà correct ✅

- **Cycle de 30 jours** : `CYCLE_LENGTH_DAYS = 30`, `cycle_end_date = start + 29 jours` (30 jours inclusifs). Index unique `uq_wheel_cycles_user_active` garantissant **un seul cycle actif par utilisateur**.
- **Seuils métier** : `POINTS_GOAL = 10_000` et `DAYS_GOAL = 25` sont des constantes en dur côté backend, non modifiables via la requête client.
- **Jours distincts (anti-multi-spin)** : `days_played_count` n'est incrémenté qu'une seule fois par jour calendaire, via le check `cycle["last_played_date"] == today` (ligne 509-517 du fichier original). Impossible d'atteindre 25 jours en 1 journée.
- **Frontend aveugle** : le `SpinRequest` ne reçoit que `turnstile_token` + `device_fingerprint`. Tous les calculs (phase, slot, points, streak, jackpot) sont faits côté backend. Le frontend reçoit uniquement le résultat (`prize_slot`, `points_awarded`, `jackpot`, …).
- **Anti-fraude multi-couches** :
  - Cooldown 30 s entre spins (admin tunable)
  - Cap quotidien 5 spins/jour (admin tunable)
  - Cloudflare Turnstile (toggleable)
  - Device fingerprinting (`services.security_service.device_fingerprint`)
  - Burst detection : > 3 spins / 60 s / même fingerprint → `suspicious_flag = TRUE` + log sécurité
  - Dégradation silencieuse si `suspicious` : cap à 25 pts/spin, jackpot désactivé
- **Jackpot contraint** : `_jackpot_eligible` exige phase 3 **ET** points ≥ 8 000 **ET** jours ≥ 20. Impossible à déclencher hors fenêtre.
- **Near-miss contrôlé** : effet "presque gagné" uniquement si phase ≥ 2 + points ∈ [5 000, 8 000[ + jours ≥ 15.
- **Logs exhaustifs** : chaque spin est inséré dans `wheel_spins` avec IP, UA, fingerprint, phase, near_miss, jackpot_triggered, streak_bonus.
- **Claim sécurisé** : `/claim-reward` utilise `SELECT ... FOR UPDATE` sur la ligne de cycle, et re-vérifie points ≥ 10 000 ET days ≥ 25 avant d'attribuer le Starter Pro. Gestion correcte du stacking si user déjà Pro (`final_expiry = max(current, expire_at)`).
- **Push scheduler** : `run_cycle_notifications_job()` envoie des rappels OneSignal à J-7 / J-3 / J-1 / J-0, avec idempotence via `wheel_notifications_sent` (PK = cycle_id + trigger_tag).

### 1.2 Ce qui n'était PAS correct ❌

#### 🔴 ÉCART #1 — Barrière mathématique cassée (P0)

Le commentaire dans le code affirmait :

> *« Règle : même en maxing out à chaque spin, 24 jours plafonnés à (~400 pts max phase 3) = ~9 600 pts → sous 10k. »*

Mais l'**implémentation réelle** était :

```python
MAX_POINTS_PER_DAY_BY_PHASE = {1: 200, 2: 500, 3: 900}
```

**Calcul du pire cas en 24 jours distincts** (path "maxed out") :

| Plage | Phase | Cap quotidien | Streaks (j3=+50, j7=+150, j15=+400) | Sous-total |
|---|---|---|---|---|
| j1–j10 | 1 | 10 × 200 = **2 000** | +50 (j3) +150 (j7) = **+200** | **2 200** |
| j11–j20 | 2 | 10 × 500 = **5 000** | +400 (j15) = **+400** | **5 400** |
| j21–j24 | 3 | 4 × 900 = **3 600** | 0 | **3 600** |
| **Total 24 jours** | | | | **11 200 pts** ❌ |

→ **Un joueur maxé peut théoriquement atteindre 10 000 pts avant le 25e jour distinct**, ce qui viole la règle métier promise. Le test `test_theoretical_max_24_days_exceeds_goal_without_clamp` prouve cet écart.

#### 🟠 ÉCART #2 — Race condition sur spins concurrents

`_get_or_create_cycle` lisait `wheel_cycles` SANS `FOR UPDATE`. Deux requêtes `/api/wheel/spin` parallèles (ex : client avec double-submit) pouvaient toutes les deux passer le check `cooldown` avant que la première n'insère son `wheel_spins`. Cette fenêtre était réduite par le cooldown 30 s mais néanmoins exploitable.

#### 🟠 ÉCART #3 — Récompense perdue si cycle expire avant claim

Scénario : utilisateur atteint 10 000 pts + 25 jours, mais ne clique pas sur "Réclamer" avant la fin du cycle (ex : notification push ratée). `_get_or_create_cycle` bascule le cycle à `completed_won` et en crée un neuf. Le endpoint `/claim-reward` ne cherchait que `reward_status = 'in_progress'` → la récompense devenait **inaccessible**.

#### 🟡 ÉCART #4 — Commentaire désaligné avec le code

Le commentaire parlait de "~400 pts max phase 3" alors que le code avait 900. Dette documentaire, source de confusion future.

---

## 2️⃣ Corrections effectuées

### Fix #1 — Clamp mathématique souverain (règle P0 verrouillée)

Dans `wheel_spin`, juste avant `persist`, on ajoute :

```python
# ─ FIX P0 — Barrière mathématique SOUVERAINE
new_total_uncapped = int(cycle["points_cycle"]) + total_points
if new_days < DAYS_GOAL:
    new_total = min(new_total_uncapped, POINTS_GOAL - 1)  # 9 999 max
else:
    new_total = new_total_uncapped
# Re-aligne les points loggés pour rester honnête
if new_total < new_total_uncapped:
    clamped_delta = new_total - int(cycle["points_cycle"])
    if clamped_delta <= base_points:
        base_points = max(0, clamped_delta)
        streak_bonus = 0
    else:
        streak_bonus = max(0, clamped_delta - base_points)
    total_points = base_points + streak_bonus
```

**Propriété mathématique** : pour tout `days_played_count < 25`, la somme `points_cycle` est **strictement inférieure à 10 000**, quelles que soient les valeurs des caps, streaks, jackpots. Le jour 25 est le **premier instant** où le clamp est levé.

### Fix #2 — Sérialisation atomique des spins

```python
async def _get_or_create_cycle(conn, user_id: str, *, for_update: bool = False):
    lock_clause = " FOR UPDATE" if for_update else ""
    row = await conn.fetchrow(
        f"SELECT * FROM wheel_cycles WHERE user_id=$1 AND reward_status=$2{lock_clause}",
        user_id, CYCLE_STATUS_IN_PROGRESS,
    )
    ...

# Dans wheel_spin :
cycle = await _get_or_create_cycle(conn, user["user_id"], for_update=True)
```

→ **deux spins concurrents du même user sont strictement sérialisés** par PostgreSQL (row lock). Le second attend la fin de la transaction du premier avant de pouvoir lire le cycle.

### Fix #3 — Fenêtre de grâce de 7 jours pour réclamer

1. Nouveau statut `CYCLE_STATUS_REWARD_PENDING` : un cycle qui expire avec objectif atteint mais non réclamé n'est plus archivé en `completed_won`, mais flippé en `reward_pending`.
2. `/claim-reward` accepte désormais **les deux statuts** (`in_progress` OU `reward_pending`).
3. Si > 7 jours après `cycle_end_date`, le claim devient impossible : `410 Gone` + flip vers `completed_won` (archive).
4. `/status` expose le champ `pending_claim` : `{ cycle_id, points_cycle, days_played_count, claim_deadline, claim_days_remaining }` pour que l'UI affiche un bandeau "vous avez une Starter Pro en attente, il vous reste X jours pour la réclamer".

### Fix #4 — Constantes & statuts unifiés

```python
CYCLE_STATUS_IN_PROGRESS = "in_progress"
CYCLE_STATUS_REWARD_PENDING = "reward_pending"   # objectif atteint, grâce active
CYCLE_STATUS_REWARD_CLAIMED = "reward_claimed"
CYCLE_STATUS_COMPLETED_WON = "completed_won"     # grâce dépassée sans claim
CYCLE_STATUS_COMPLETED_LOST = "completed_lost"
CLAIM_GRACE_DAYS = 7
```

Toutes les comparaisons de statut passent désormais par ces constantes (plus de string magique dans la logique). Commentaire aligné avec le code.

---

## 3️⃣ Schéma DB (extrait pertinent)

### Table `wheel_cycles`
| Colonne | Type | Rôle |
|---|---|---|
| `id` | BIGSERIAL PK | identifiant cycle |
| `user_id` | VARCHAR(32) | propriétaire |
| `cycle_start_date` / `cycle_end_date` | DATE | fenêtre 30 jours |
| `points_cycle` | INT | cumul — clampé < 10 000 tant que days < 25 |
| `days_played_count` | INT | incrémenté 1×/jour distinct |
| `last_played_date` | DATE | dernier jour joué (anti double-compte) |
| `streak_days` | INT | série consécutive pour les bonus |
| `suspicious_flag` | BOOLEAN | dégradation silencieuse |
| `reward_status` | VARCHAR(32) | `in_progress` / `reward_pending` / `reward_claimed` / `completed_won` / `completed_lost` |
| `reward_claimed_at` | TIMESTAMPTZ | trace |

- **Unicité** : `CREATE UNIQUE INDEX uq_wheel_cycles_user_active ON wheel_cycles(user_id) WHERE reward_status='in_progress'` — impossible d'avoir 2 cycles actifs simultanés.

### Table `wheel_spins` (journal d'audit)
FK vers `wheel_cycles(id)`, stocke **chaque** tentative avec `ip_address`, `device_fingerprint`, `user_agent`, `prize_slot`, `points_awarded`, `phase`, `near_miss`, `jackpot_triggered`, `streak_bonus`, `turnstile_passed`.

### Table `wheel_notifications_sent`
Idempotence des rappels push : PK `(cycle_id, trigger_tag)` empêche tout double-envoi.

---

## 4️⃣ Endpoints API (après fix)

| Méthode | Route | Rôle | Auth |
|---|---|---|---|
| `GET` | `/api/wheel/status` | État du cycle + progress + rules + **`pending_claim`** | user |
| `POST` | `/api/wheel/spin` | Jouer un tour. **Clamp souverain** + **FOR UPDATE** + cap phase + streak + anti-fraude | user |
| `POST` | `/api/wheel/claim-reward` | Réclame Starter Pro (accepte `in_progress` OU `reward_pending` dans la grâce) | user |
| `GET` | `/api/wheel/history` | Historique des 20 derniers spins | user |
| `POST` | `/api/wheel/admin/send-cycle-reminders` | Déclenchement manuel du scheduler push | admin |

---

## 5️⃣ Frontend vs Backend — preuve du *backend-only*

**Requête côté client** (`SpinRequest` dans `wheel_fortune.py` ligne 147) :

```python
class SpinRequest(BaseModel):
    turnstile_token: Optional[str]      # preuve anti-bot, facultatif en dev
    device_fingerprint: Optional[str]   # identifiant de navigateur
```

→ **Aucun champ ne permet au client d'influencer le résultat**. Pas de "slot demandé", pas de "montant", pas de "phase". Le frontend est un observateur passif.

**Réponse côté serveur** :

```python
return {
    "spin_id": ...,
    "prize_slot": slot_idx,       # déterminé 100 % backend (weighted_pick + jackpot)
    "points_awarded": total_points,
    "phase": phase,
    "near_miss": near_miss,
    "jackpot": jackpot,
    "new_total_points": new_total,
    "crossed_milestones": [...],
}
```

Le frontend reçoit uniquement l'index gagnant (pour animer la roue) et les points finaux. Toute tentative de modifier le payload côté client est inutile : la source de vérité est `wheel_cycles.points_cycle` en base.

---

## 6️⃣ Tests — preuve exécutable

Fichier : `/app/backend/tests/test_wheel_fortune.py` — **46 tests, 100 % passent en 0,29 s**.

Ce qu'ils prouvent :

1. Constantes métier : 30 / 10 000 / 25 / grâce ≥ 1 jour (4 tests)
2. Frontières de phases : 9 paramétrages (1 test paramétrique)
3. **Clamp souverain mathématique** :
   - `test_theoretical_max_24_days_exceeds_goal_without_clamp` : démontre que sans clamp, les caps autorisent 11 200 pts sur 24 jours.
   - `test_sovereign_clamp_enforces_sub_10k_until_day_25` : simule jour par jour et assert `points_cycle < 10 000` sur chaque journée j1..j24, puis égal à 9 999 à la fin de j24.
   - `test_day_25_is_first_day_clamp_is_lifted` : prouve que le clamp est levé exactement au jour 25.
4. **Jackpot** : refusé hors phase 3, refusé si < 8 000 pts, refusé si < 20 jours, accepté sinon (4 tests).
5. **Near-miss** : accepté dans [5 000, 8 000[ + jours ≥ 15, refusé sinon.
6. **Streak bonus** : 10 paramétrages des seuils 3/7/15.
7. **Milestones** : ordre des franchissements.
8. **Scheduler** : tags j7/j3/j1/j0 validés, tous les autres jours → None.
9. **Distribution** : les phases 1 et 2 n'ont PAS le jackpot (défense en profondeur). Le `_weighted_pick` ne renvoie que des slots valides sur 200 tirages/phase.

### Résultat d'exécution
```
$ cd /app/backend && python -m pytest tests/test_wheel_fortune.py -q -p no:pytest_ethereum
..............................................                           [100%]
46 passed in 0.29s
```

### Smoke test d'intégration (curl live)
```
GET /api/wheel/status  →  HTTP 200
  "pending_claim": null                ← nouveau champ exposé correctement
  "points_goal": 10000, "days_goal": 25
  "phase": 1, "can_claim": false
```

---

## 7️⃣ Résultat final — validation par règle

| Règle | Statut | Preuve |
|---|---|---|
| Cycle 30 jours calendaires | ✅ Conforme | `test_cycle_length_is_30_days` + index unique `uq_wheel_cycles_user_active` |
| Seuil 10 000 points | ✅ Conforme | `test_goals_match_product_spec` + check dans `/claim-reward` |
| Minimum 25 jours distincts | ✅ Conforme | `last_played_date` check + `test_goals_match_product_spec` + check dans `/claim-reward` |
| **Impossible d'atteindre 10k en 24 jours** | ✅ Conforme | `test_sovereign_clamp_enforces_sub_10k_until_day_25` — clamp souverain |
| Logique 100 % backend | ✅ Conforme | `SpinRequest` n'accepte que `turnstile_token` + `device_fingerprint` |
| Sécurité anti-fraude | ✅ Conforme | Cooldown + cap jour + burst + Turnstile + fingerprint + suspicious_flag |
| Sérialisation concurrente | ✅ Conforme | `SELECT ... FOR UPDATE` sur le cycle |
| Attribution Starter Pro | ✅ Conforme | Transaction + stacking + 1er du mois suivant |
| Reset propre | ✅ Conforme | `completed_lost` / `reward_pending` (grâce 7 j) / `reward_claimed` / `completed_won` |
| Scheduler rappels push | ✅ Conforme | Idempotent via `wheel_notifications_sent`, lot quotidien j-7/j-3/j-1/j-0 |

---

## 8️⃣ Recommandations backlog (non bloquants)

- **P2** — Exposer `pending_claim` dans l'UI de la roue (bandeau "Il vous reste X jours pour réclamer votre Starter Pro").
- **P2** — Ajouter un email transactionnel au moment du flip `reward_pending` (l'utilisateur atteint le jour d'expiration avec objectif atteint mais n'a pas claim).
- **P2** — Dashboard admin : métrique "taux de conversion cycle → claim" pour détecter si la fenêtre de 7 jours est suffisante en prod.
- **P3** — Migration rétroactive : les anciens cycles `completed_won` existants n'étaient pas claimables ; un script one-shot pourrait les re-ouvrir en `reward_pending` si l'expiration date < 7 jours.

---

**Fichiers modifiés** : `/app/backend/routes/wheel_fortune.py`  
**Fichiers créés** : `/app/backend/tests/test_wheel_fortune.py`, `/app/memory/WHEEL_FORTUNE_AUDIT.md` (ce document)  
**Régressions** : aucune — `/api/wheel/status` répond toujours HTTP 200 avec la forme attendue + le nouveau champ `pending_claim`.
