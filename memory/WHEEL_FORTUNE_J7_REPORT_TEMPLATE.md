# 🎡 JAPAP Roue de la Fortune — Rapport J+7 (Phase finale)

> **Ce document est un template auto-instanciable.**  
> Pour générer la version réelle à J+7, appeler `GET /api/wheel/admin/j7-report`
> avec un token admin. La réponse JSON remplit exactement les champs ci-dessous.

**Date de génération** : à remplacer par `response.generated_at`  
**Période** : 7 jours glissants depuis le démarrage de la phase d'observation  
**Statut** : 🔴 Clôture du sujet après lecture de ce rapport

---

## 1. 📊 Activité réelle

| Indicateur | Valeur | Source |
|---|---|---|
| **DAU 24h** (Daily Active Users sur la roue) | `activity.dau_24h` | `wheel_spins` 24h |
| **WAU** (hebdomadaire) | `activity.wau` | `wheel_spins` 7j |
| **Cycles total (lancés)** | `activity.cycles_total` | `wheel_cycles` |
| **Cycles terminés** | `activity.cycles_ended` | `reward_claimed + completed_won + completed_lost` |

**Lecture** : Le ratio cycles_total / cycles_ended indique combien de cycles sont encore en cours. Un rapport > 3:1 signifie que l'engagement initial est présent mais que la rétention sur la durée totale (30 jours) n'est pas encore démontrée.

---

## 2. 🎯 Performance du système

| Indicateur | Valeur | Lecture |
|---|---|---|
| **Completion rate** | `performance.completion_rate` × 100 % | Pourcentage atteignant l'objectif 10 000 pts + 25 jours |
| **Bloqués avant 25 jours** | `performance.blocked_below_goal_pct` × 100 % | Part des échecs dûs au manque de temps de jeu |
| **Temps moyen pour atteindre 10 000 pts** | `performance.avg_days_to_win` jours | Sur les cycles réclamés |
| **Points moyens à J+10** | `performance.avg_points_j10` | Exploitation possible si > 2 000 |
| **Points moyens à J+20** | `performance.avg_points_j20` | Exploitation possible si > 5 000 |
| **Points moyens à J+25** | `performance.avg_points_j25` | Doit être ≈ 10 000 pour les vainqueurs |

**Bornes de référence** :
- Completion rate cible : **5–20 %** (engagement sain)
- < 3 % → trop dur · > 35 % → trop facile ou exploité
- avg_days_to_win : 25–28 jours (plus bas = clamp backend contourné = impossible par design)

---

## 3. 🛡️ Comportement utilisateurs

### Vue d'ensemble
| Indicateur | Valeur |
|---|---|
| **Comptes flaggés suspicious** | `behaviour.suspicious_count` |
| **Bot-like accounts (24h)** | `behaviour.patterns.bot_like_accounts` |
| **Clusters multi-IP (7j)** | `behaviour.patterns.multi_ip_clusters` |
| **Clusters multi-fingerprint (7j)** | `behaviour.patterns.multi_fingerprint_clusters` |

### Liste des comptes suspects (`behaviour.suspicious_users`)
> Disponible dans l'admin via **/admin → Roue Fortune → onglet "Cycles utilisateurs" → filtre "Suspects"**.  
> Chaque ligne fournit `display_name`, `email`, `user_id` → **contact direct possible** par email ou chat interne.

Pour chaque suspect, les champs retournés sont :
```json
{ "user_id": "...", "cycle_id": 42, "email": "...", "display_name": "...",
  "points_cycle": 8500, "days_played_count": 18, "reward_status": "in_progress" }
```

**Action opérationnelle possible depuis l'admin** :
- Clic "Reset flag" après review → lève la dégradation silencieuse
- Si confirmé malveillant → ban via `/admin → Utilisateurs`

---

## 4. 🏆 Gagnants & conversion PRO

| Indicateur | Valeur |
|---|---|
| **Starter Pro attribués (total)** | `winners.clean_winners_count + winners.suspect_winners_count` |
| **Gains normaux (sains)** | `winners.clean_winners_count` |
| **Gains suspects (flaggés au moment du claim)** | `winners.suspect_winners_count` |

### Liste des gagnants normaux (`winners.clean_winners`)
> Chaque entrée : `display_name`, `email`, `points_cycle`, `days_played_count`, `cycle_id`.
> **Accessible dans l'admin** via **Roue Fortune → Cycles utilisateurs → filtre "Réclamés"**.
> Ces utilisateurs sont le **cœur de la cohorte Starter Pro** → à segmenter pour onboarding, upsell Pro payant à l'expiration.

### Liste des gagnants suspects (`winners.suspect_winners`)
> Review manuelle obligatoire avant activation d'un ban ou d'un reset. Contact email obligatoire avant toute action restrictive (faux positifs possibles).

---

## 5. 🔎 Analyse stratégique (conclusion unique)

**Verdict** : `analysis.verdict` — l'un parmi :
- 🟢 `healthy` : Le système fonctionne dans les bornes attendues.
- 🔴 `too_hard` : Le système est trop difficile — la majorité des utilisateurs échoue avant 25 jours.
- 🟠 `too_easy_or_exploited` : Le système est trop facile, ou exploité (patterns bot).
- ⚪ `insufficient_data` : Moins de 20 cycles terminés, pas de conclusion.

**Phrase unique** : `analysis.verdict_line`

> Le système retourne **une seule conclusion** par design. Pas d'hypothèses alternatives.

---

## 6. ✅ Recommandation produit (décision argumentée)

**Action** : `recommendation.action` — l'un parmi :
- `keep_as_is` → on ne touche à rien, le moteur converge.
- `adjust_harder` → augmenter `cooldown_seconds` (60s) + baisser `jackpot_odds_in_window` (15 %).
- `adjust_easier` → relever les bonus de streak 3j/7j/15j, ou ajouter un boost week-end.
- `turnstile_on` → **activer Turnstile immédiatement** (patterns d'exploitation détectés).

**Raison** : `recommendation.reason` (one-liner argumenté fondé sur les données).

### Application immédiate depuis l'admin
| Recommandation | Action à effectuer |
|---|---|
| `turnstile_on` | Roue Fortune → Configuration → toggle "Protection Turnstile" → Enregistrer (nécessite les clés Cloudflare dans `.env`) |
| `adjust_harder` | Roue Fortune → Configuration → Cooldown = 60 / Jackpot odds = 15 → Enregistrer |
| `adjust_easier` | Roue Fortune → Configuration → Bonus streak 3j/7j/15j → augmenter de 20 % → Enregistrer |
| `keep_as_is` | Rien. Ré-exécuter ce rapport dans 7 jours. |

---

## 7. 📈 Signal critique à surveiller (churn post-reward)

**Dans le graphique temporel** (admin → Roue Fortune → Évolution temporelle) :

> Si `completion_rate` **monte** et `dau_24h` **baisse** sur les 3 derniers jours
> → bandeau rouge "Churn post-reward détecté" apparaît automatiquement.
> Signification : les gagnants partent après avoir touché leur Starter Pro.
> Action : déclencher un **email post-claim** ("Vous avez encore 15 jours de Pro, lancez votre prochain cycle") ou introduire un **sur-cycle** (deuxième palier à 20 000 pts pour 60 jours de Pro).

---

## 8. 📁 Annexes techniques

- **Endpoint live** : `GET /api/wheel/admin/j7-report` → JSON complet (toutes les valeurs du rapport)
- **Timeseries** : `GET /api/wheel/admin/timeseries?days=7|14|30` → courbes
- **Export CSV** : `GET /api/wheel/admin/cycles/export.csv?status=...` → toutes les lignes pour analyse externe
- **Audit logs** : toutes les actions admin (force-claim, reset-suspicious, config update) sont journalisées dans `security_events` avec le user_id de l'admin et les détails.

---

## 9. 🛑 Clôture

Après lecture et application de la recommandation de ce rapport :

👉 **Le sujet Roue de la Fortune est clôturé.**

La prochaine étape produit est décidée en dehors de ce document (nouvelle feature, optimisation d'autres modules, etc.).

Le moteur restera observable en continu via `/api/wheel/admin/observability` et `/api/wheel/admin/strategic-kpis` pour alerter spontanément si les indicateurs basculent hors des bornes de santé.

---

*Générer la version live : `GET /api/wheel/admin/j7-report` — remplace chaque placeholder `response.xxx` par la valeur réelle.*
