# 🎡 JAPAP Roue de la Fortune — Rapport d'Observation Baseline (J0)

**Date** : 23 avril 2026 — **Phase** : observation contrôlée (Turnstile OFF)  
**Source** : `GET /api/wheel/admin/observability` (live, admin)  
**Cadence de reporting** : hebdomadaire (J+7, J+14, J+21, J+28)

---

## 🎯 Protocole d'observation — ce qui est monitoré

### A. Comportement utilisateur
- **Spins/utilisateur** : total + 24h + 7j + DAU
- **Jours joués par cycle** (distincts) : moyenne, médiane
- **Vitesse d'atteinte des 10 000 points** : `avg_days_to_win`, `median_days_to_win`

### B. Détection automatique d'abus
| Heuristique | Seuil | Fenêtre |
|---|---|---|
| Bot-like (spins/heure) | ≥ **30 spins/h** moyenné | 24 h |
| Multi-comptes même IP | ≥ **4 users distincts** sur même IP | 7 j |
| Multi-comptes même fingerprint | ≥ **3 users distincts** | 7 j |
| Progression anormale | > **1 800 pts/jour distinct** | cycle en cours |
| Night-owl pattern | > **55 % spins** entre 00 h–06 h UTC (≥ 20 spins) | 7 j |

### C. Distribution des récompenses
- `winners_total` = cycles ayant atteint 10 000 pts + 25 jours (reward_pending + reward_claimed + completed_won)
- `claimed_total` = cycles effectivement réclamés (reward_claimed)
- `claim_rate` = claimed / winners (doit tendre vers 100 % avec la grâce 7 j + email + push)

### D. Protections actives (passives mais strictes)
- Cooldown **30 s** entre spins
- Cap quotidien **5 spins/jour**
- `suspicious_flag` activé automatiquement sur burst > 3 spins/60 s même fingerprint
- Dégradation silencieuse : si flagged → cap 25 pts/spin, jackpot désactivé
- Clamp mathématique souverain : `points_cycle < 10 000` tant que `days < 25` (imparable)
- Sérialisation atomique (`SELECT ... FOR UPDATE`) contre les races

---

## 📊 Baseline J0 (23/04/2026)

> Données réelles extraites de l'endpoint live au démarrage de la phase.

```json
{
  "engagement": {
    "active_users": 2,       "active_cycles": 2,
    "total_cycles": 3,       "total_spins": 1,
    "spins_24h": 1,          "spins_7d": 1,          "dau_24h": 1,
    "claimed_rewards": 1,    "pending_claims": 0,
    "jackpots_total": 0,     "near_miss_total": 0,
    "avg_points_per_spin": 100.0
  },
  "reward_distribution": {
    "winners_total": 1,      "claimed_total": 1,
    "claim_rate": 1.0,       "avg_days_to_win": 26.0,
    "median_days_to_win": 26.0, "avg_points_at_claim": 10500.0
  },
  "anomalies": {
    "bot_like_accounts": [],       "same_ip_clusters": [],
    "same_fingerprint_clusters": [], "rapid_progression": [],
    "night_owls": [],              "anomaly_score": 0
  },
  "recommendation": { "action": "not_needed_yet" }
}
```

### Lecture rapide
- **2 utilisateurs actifs**, 3 cycles créés, 1 spin enregistré → trafic minimal (la roue n'a pas encore été lancée en masse).
- **1 claim réussi** (Bob, testé en live), **claim_rate = 100 %**.
- **Zéro anomalie détectée** → recommandation automatique : `not_needed_yet` (on reste en observation).
- Protections passives toutes en place (cooldown 30 s, cap 5/jour, Turnstile OFF comme demandé).

---

## 🛡️ Seuils de bascule automatique vers Turnstile

Le système calcule un **`anomaly_score`** et recommande automatiquement dans la réponse de l'endpoint :

| Score | Action recommandée | Message |
|---|---|---|
| < 5 | `not_needed_yet` | RAS — observation continue |
| 5–14 | `monitor_closely` | Anomalies modérées, surveiller |
| ≥ 15 ou ≥ 5 comptes bot-like | `activate_now` | **Activer Turnstile immédiatement** |

> Formule : `score = 3×bots + 2×same_ip + 2×same_fp + 2×rapid + 1×night_owls`  
> Si `activate_now` apparaît dans le JSON, un admin peut activer Turnstile en une ligne :
> `UPDATE admin_settings SET value='true' WHERE key='wheel_turnstile_enabled'`

---

## 🛠️ Outils admin disponibles (live)

| Méthode | Route | Rôle |
|---|---|---|
| `GET` | `/api/wheel/admin/observability` | Rapport complet (consommé par cron / UI admin) |
| `POST` | `/api/wheel/admin/flag-suspicious` | Flag en masse les comptes correspondant aux patterns les plus stricts (bot-like + fingerprint partagé) — dégradation silencieuse immédiate sur leurs cycles |
| `POST` | `/api/wheel/admin/send-cycle-reminders` | Déclenchement manuel du job de rappels push J-7/J-3/J-1/J-0 |

---

## 📅 Calendrier de reporting

| Date | Action |
|---|---|
| **J0** (aujourd'hui) | ✅ Baseline capturée — ce document |
| **J+7** | Premier rapport hebdomadaire : DAU, distribution, premiers patterns |
| **J+14** | Rapport + décision : Turnstile maintenu OFF ou bascule |
| **J+21** | Optimisation des seuils anomalie si nécessaire |
| **J+28** | Fin du 1er cycle complet — analyse de cohorte + recommandation produit |

Chaque rapport hebdomadaire utilise la **même structure JSON** + commentaire humain.

---

## ⚠️ Conditions d'alerte immédiate (sans attendre le rapport hebdomadaire)

Déclencher une action dès que **l'un** de ces signaux apparaît dans `/api/wheel/admin/observability` :

1. `recommendation.action == "activate_now"` → **activer Turnstile dans la journée**
2. `anomaly_score >= 10` deux jours consécutifs → **activer Turnstile + flag-suspicious en masse**
3. `engagement.jackpots_total` > 5 × `winners_total` → distribution anormale, **audit de la formule jackpot**
4. `reward_distribution.claim_rate` < 50 % après J+30 → **UX claim à refondre** (notif/CTA invisibles)
5. `engagement.dau_24h` chute > 60 % d'un jour à l'autre → **check technique** (bug ? banni d'un app store ?)

---

**Rapport mis à jour par** : agent backend (extraction automatique via endpoint live).  
**Fichier associé** : `/app/memory/WHEEL_FORTUNE_AUDIT.md` (audit technique P0 du 23/04/2026).
