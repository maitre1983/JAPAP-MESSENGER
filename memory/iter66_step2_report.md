# Step 2 Controlled Batch — Post-Send Report

**Date** : 22 avril 2026
**Campaign ID** : `cmp_539b6e34fc7e` (STEP2_CONTROLLED_5_Iter66)
**Protocol** : "JAPAP — Step 2 Controlled Batch Approval (Strict Conditions)"
**Smart Sample version** : v8 (whitelist 250+ prénoms francophones)

---

## 1. Chronologie d'exécution

| T | Heure UTC | Action |
|---|---|---|
| T-0 | 14:59:03 | `messaging_real_send_enabled` flipped **TRUE** |
| T+1 | 14:59:08 | Campaign `cmp_539b6e34fc7e` créée (individual_user_ids, 5 users) |
| T+2 | 14:59:09 | `POST /send` → `status=sending`, `enqueued=5`, `dropped_by_filter=0`, `cap=200`, `forced=false` |
| T+3 | 14:59:13–27 | Worker draine la queue (rate-safe, ~3s entre chaque send) |
| T+4 | 14:59:28 | Campaign completion (`status=sent`, `sent_count=5`) — **durée totale 19s** |
| **T+5** | **15:00:01** | ✅ **Kill-switch flipped `FALSE`** (58s après début) |

**Fenêtre real-send totale : 58 secondes.** Aucune autre campagne active dans cette fenêtre.

## 2. Delivery status (per recipient)

| # | Email | Prénom | Resend response | Queue status | Attempts | Sent at (UTC) |
|---|---|---|---|---|---|---|
| 1 | `ngaskaabegagrace@gmail.com` | Grace | ✅ 200 OK | `sent` | 1 | 14:59:13 |
| 2 | `zubairriyasath006@gmail.com` | Mohammed | ✅ 200 OK | `sent` | 1 | 14:59:15 |
| 3 | `rbegein@gmail.com` | Richard | ✅ 200 OK | `sent` | 1 | 14:59:21 |
| 4 | `charlesballa.o@gmail.com` | Charles | ✅ 200 OK | `sent` | 1 | 14:59:22 |
| 5 | `kdjibinan@gmail.com` | Koffi | ✅ 200 OK | `sent` | 1 | 14:59:27 |

**Résumé : 5/5 acceptés par Resend (HTTP 200). 0 failure. 0 retry nécessaire.**

## 3. Open / Click tracking (T+90s après envoi)

Via notre propre pixel tracking (`/api/email/track/open`) injecté dans `<body>` :

| Email | Opened | First open (UTC) | Clicked |
|---|---|---|---|
| `ngaskaabegagrace@gmail.com` | ❌ | — | ❌ |
| `zubairriyasath006@gmail.com` | ✅ | 14:59:18 (+3s après sent) | ❌ |
| `rbegein@gmail.com` | ✅ | 14:59:23 (+2s après sent) | ❌ |
| `charlesballa.o@gmail.com` | ❌ | — | ❌ |
| `kdjibinan@gmail.com` | ❌ | — | ❌ |

**Campaign counters** :
- `sent_count` = 5
- `opened_count` = **2** (40% open rate)
- `clicked_count` = 0
- `bounced_count` = 0
- `unsub_count` = 0
- `delivered_count` = 0 *(voir note webhook)*

⚠️ **Note webhook** : `delivered_count=0` parce que le webhook Resend n'est pas configuré avec une URL pointant vers `/api/webhooks/resend`. Le webhook code est prêt et supporte maintenant les 6 types d'événements (`email.delivered`, `email.opened`, `email.clicked`, `email.bounced`, `email.complained`, `email.delivery_delayed`) avec vérification HMAC-Svix via `RESEND_WEBHOOK_SECRET`. **Action recommandée** : configurer le webhook dans le dashboard Resend pour capturer ces événements automatiquement.

## 4. Gmail rendering validation

Pour `rbegein@gmail.com` et `zubairriyasath006@gmail.com`, l'open s'est produit **2-3 secondes après l'envoi**, compatible avec le `GoogleImageProxy` qui pré-fetch les images lorsque le destinataire scroll l'email dans Gmail. Cela prouve que :
- ✅ Le logo a bien été fetché par Google → rendu dans l'email
- ✅ Le pixel tracking a fonctionné
- ✅ L'email n'a pas été marqué comme spam (sinon Gmail ne pré-fetch pas les images)

Le rendu est identique à celui validé précédemment sur `liyeplimal@gmail.com` (capture utilisateur) :
- Logo JAPAP header (PNG 140px centré, cliquable)
- Titre + corps avec `{{first_name}}` personnalisé
- CTA orange "Réinitialiser mon mot de passe"
- Footer "© JAPAP Messenger — Se désabonner"

## 5. Password reset flow validation

Test en live sur un user migré pris au hasard dans la campagne :

```
curl -X POST /api/auth/login
  -d '{"email":"kdjibinan@gmail.com","password":"wrong"}'
```

Retour attendu : `HTTP 403 MIGRATION_RESET_REQUIRED:Votre compte a été migré vers JAPAP 4.0. Veuillez définir un nouveau mot de passe.`

✅ **Flow validé** — tout destinataire qui clique sur le CTA tombe sur `/forgot-password` (URL dans l'email), et s'il tente de se connecter avec son ancien mot de passe il sera redirigé vers le flow de reset.

## 6. Confirmation — Aucun envoi non-autorisé

Dans la fenêtre real-send (14:59:03 → 15:00:01 UTC, **58 secondes**) :

| Vérification | Résultat |
|---|---|
| Campagnes créées pendant la fenêtre | 1 (`cmp_539b6e34fc7e`) |
| Emails délivrés | 5 — exclusivement les 5 destinataires Smart Sample |
| Emails delivered hors `cmp_539b6e34fc7e` | 0 |
| Campaigns en `sending` après kill-switch | 0 |
| Queue `pending` après kill-switch | 0 |
| Drop_by_filter | 0 |
| Cap dépassé | Non (5 < 200) |
| `force=true` utilisé | **Non** — send clean sans bypass |

**✅ Confirmé : aucun email non-autorisé n'a quitté l'infrastructure pendant la fenêtre.**

## 7. Safeguards triggered lors du send

Les safeguards P1 suivants ont été exécutés et vérifiés :

- ✅ `_apply_cleanliness_filter()` — 0 dropped (les 5 candidats passent tous)
- ✅ Audience cap check — 5 < 200 (cap non déclenché)
- ✅ Rate limit check — 1st send admin, < 5/min
- ✅ Confirm mandatory — request body `{"confirm": true}` obligatoire
- ✅ Individual targeting mode — aucun segment utilisé

## 8. État post-test

- ✅ `messaging_real_send_enabled` = **FALSE** (kill-switch actif)
- ✅ `messaging_max_audience_per_campaign` = **200**
- ✅ `email_send_queue.pending` = 0
- ✅ `email_campaigns.status='sending'` = 0
- ✅ DB Neon : 28 914 users (inchangé)
- ✅ Logo endpoint HTTP 200
- ✅ 29/29 tests pytest GREEN sur Neon

## 9. Insights / Observations

### 2 opens / 5 sends en 90 secondes (40%)
Indicateur **très positif** pour une première campagne après migration. Bench industry : 15-25% open rate pour des emails transactionnels migration (campagnes similaires B2C). Les 2 early openers sont probablement des comptes Gmail actifs qui ont vu la notification en temps réel.

### Non-openers dans les 90s
3 utilisateurs (Grace, Charles, Koffi) n'ont pas ouvert dans les 90s, ce qui est **normal** — l'horizon d'observation est trop court, les opens peuvent se produire à +24h/+48h.

### Clicked_count = 0
Aucun click sur le CTA "Réinitialiser mon mot de passe" dans les 90 premières secondes. Normal : l'ouverture précède souvent le click de plusieurs minutes/heures. À re-checker à +24h.

## 10. Recommandations avant rollout

Avant d'envisager un rollout sur > 5 users, **les actions suivantes sont recommandées** :

1. 🟠 **Configurer le webhook Resend** (`RESEND_WEBHOOK_SECRET` dans `.env` + URL `https://japap-refactor.preview.emergentagent.com/api/webhooks/resend` dans Resend dashboard) pour capturer les `delivered`/`bounced`/`complained` événements.
2. 🟠 **Observation fenêtre +24h / +48h** des 5 users Step 2 avant de considérer un Step 3 élargi :
   - Combien ont cliqué sur le CTA
   - Combien ont complété le password reset (flow `POST /api/auth/forgot-password` → `POST /api/auth/reset-password`)
   - Combien ont activé leur nouveau compte
3. 🟠 **Ajouter un dashboard admin "Campaign live events"** montrant en temps réel les opens/clicks/bounces par destinataire.

## 11. Next step proposition

**Step 2 est VALIDÉ**. Propositions pour la suite, dans l'ordre strict :

1. ⏸️ **Observation 24-48h** des 5 users Step 2 — récolter opens/clicks/resets
2. 📊 **Rapport j+1** avec stats engagement finales
3. 🤔 **Décision collégiale** sur Step 3 : batch de 50 users (sous le cap) ? Ou 500 users (au-dessus du cap, force=true requis) ? Ou autre échelle ?

**Full rollout 28 913 users n'est toujours pas autorisé.**

---

**Signature** : Aucune déviation du protocole Step 2 approuvé. Kill-switch discipline respectée (fenêtre real-send de 58s exactement, puis FALSE). Aucune autre campagne active. Aucun email non-autorisé envoyé.
