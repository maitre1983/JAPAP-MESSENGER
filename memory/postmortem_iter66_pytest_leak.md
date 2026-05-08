# Post-Mortem — Fuite d'emails test vers utilisateurs réels migrés
**Date incident** : 22 avril 2026, 03:59–04:05 UTC
**Détecté** : 22 avril 2026, 04:02 UTC (pendant la revue de régression Iter 66)
**Contenu** : Coupé à 04:10 UTC
**Sévérité** : Haute (envoi non autorisé de contenu test à des adresses réelles)
**Statut** : Contenu et corrigé

---

## 1. Résumé

Pendant l'exécution de la suite pytest `test_iteration64_messaging_center.py` dans la session précédente (avant fork), le fixture `campaign` a créé une campagne qui référençait le segment **`seg_non_pro`**. Ce segment, après la migration de 1 158 utilisateurs legacy, s'était silencieusement étendu à **1 167 utilisateurs réels (dont 1 158 emails de production)**.

Le worker messaging a ensuite drainé la queue et **envoyé 70 emails test via Resend** vers des adresses réelles avant que l'incident soit détecté. 2 263 autres emails étaient encore en file d'attente — supprimés manuellement avant envoi.

## 2. Cause racine

La suite pytest Iter 64 a été conçue avant la migration, à une époque où le segment `seg_non_pro` correspondait uniquement à une poignée d'utilisateurs sandbox (admin, bob, alice, carol, etc.).

Après l'exécution du script de migration Iter 66-Migration (1 158 comptes créés), la requête SQL compilée par `services/segment_compiler.py` pour `seg_non_pro` (`WHERE u.is_pro = NOT TRUE`) a silencieusement pris en compte les nouveaux comptes, sans aucun garde-fou côté test ni côté worker.

**Failles cumulées** :
1. 🔴 Tests automatisés autorisés à utiliser des segments production (`seg_non_pro`, `seg_all_users`, etc.)
2. 🔴 Aucun kill-switch opérationnel côté worker pour interrompre les envois massifs en cours
3. 🔴 Aucun cap maximum sur la taille d'audience par campagne (un pytest pouvait cibler 1 000+ users sans alerte)
4. 🟠 Aucune distinction sémantique entre utilisateurs sandbox et utilisateurs production (basée uniquement sur is_pro/is_active)

## 3. Campagnes affectées

| campaign_id | name | subject | body | delivered | notes |
|---|---|---|---|---|---|
| `cmp_052fd80b94c0` | renamed | `Y` | `<p>Ok</p>` | **70** | 🔴 fuite majeure — users migrés réels |
| `cmp_9ae52ddd0b39` | pytest_* | — | — | 8 | principalement sandbox |
| `cmp_00723a2f5d81` | pytest_* | — | — | 7 | pré-migration — impact minimal |
| `cmp_89611ffbab25` | pytest_* | — | — | 7 | pré-migration |
| `cmp_230c097ed5a5` | pytest_* | — | — | 7 | pré-migration |
| `cmp_5a69df9e4c64` | pytest_* | — | — | 2 | sandbox |
| `cmp_aa2b9ad52a3d` | pytest_* | — | — | 2 | sandbox |
| `cmp_ace520593fd9` | pytest_* | — | — | 2 | sandbox |
| `cmp_192c896a846e` | pytest_* | — | — | 1 | sandbox |
| `cmp_9f2099a000ef` | pytest_* | — | — | 1 | sandbox |

**Total** : ~107 emails envoyés, dont **~70 vers de vrais utilisateurs migrés** (ex : `addydian98@gmail.com`, `aissatasao2022@gmail.com`, `albanozau@yahoo.com`, `liyeplimal@gmail.com`, etc.).

Les autres 37 emails sont majoritairement partis à des sandbox users (`@japap.com`) datant d'avant la migration.

## 4. Chronologie de la détection et du confinement

- **04:02 UTC** : Lancement de `pytest tests/test_iteration64_messaging_center.py` pour régression. Le test `test_campaign_send_enqueues_and_worker_drains` échoue après 30 s avec `Worker did not drain within 30s`.
- **04:03 UTC** : Inspection de la DB `email_send_queue` → découverte de 2 263 entrées `pending` sur 2 campagnes test (~1 100 users chacune).
- **04:05 UTC** : Identification des campagnes par nom (`pytest_*`, `renamed`, `indiv_*`, `var_test_*`).
- **04:07 UTC** : `DELETE FROM email_send_queue WHERE campaign_id = ANY(...) AND status='pending'` → 2 263 lignes supprimées.
- **04:08 UTC** : `UPDATE email_campaigns SET status='paused'` sur les 4 campagnes encore en `sending`.
- **04:10 UTC** : Confirmation que la queue est vide (`pending = 0`).
- **04:16 UTC** : Kill-switch `messaging_real_send_enabled=false` activé en DB.

## 5. Safeguards permanents ajoutés (ce commit)

1. **Kill-switch admin** : nouveau setting `messaging_real_send_enabled` (défaut `TRUE`). Quand `FALSE`, le worker log `[SAFE-MODE] Skipping real delivery` et ne contacte pas Resend. Contrôlable en DB ou via `/api/admin/settings`. *(fichier : `services/messaging_worker.py::_send_one`)*

2. **Segment sandbox dédié** : nouveau segment système `seg_pytest_safe` avec règle `pytest_safe_only = TRUE` qui compile en SQL `LOWER(u.email) LIKE '%@japap.com'`. Les 1 158 users migrés sont explicitement exclus (ils ont des emails `@gmail.com`, `@yahoo.com`, etc.). *(fichiers : `services/segment_compiler.py` — ajout du synthétique `pytest_safe_only` + seed SYSTEM_SEGMENTS)*

3. **Migration du fixture pytest** : `tests/test_iteration64_messaging_center.py::campaign` et `test_campaign_update_draft_only` utilisent maintenant `seg_pytest_safe` au lieu de `seg_non_pro`. *(2 edits)*

4. **Test de non-régression** : `test_segments_auto_seed` vérifie désormais la présence de `seg_pytest_safe` dans les segments système — toute suppression accidentelle fera échouer les tests.

## 6. Statut actuel (post-fix)

- ✅ Queue messaging : `pending = 0`
- ✅ Safe mode : `messaging_real_send_enabled = FALSE` (vérifié en DB + via log `[SAFE-MODE]` sur les sends récents)
- ✅ Tests pytest Iter 64 + Iter 66 : **29/29 GREEN** en mode safe
- ✅ Tous les sends depuis `2026-04-22 04:16:20 UTC` passent par `[SAFE-MODE]` (confirmé côté `backend.err.log` : 11 envois, tous skipped)
- ✅ Un seul email non-@japap.com loggé depuis le fix (`external_test@example.com`) → test iter66 explicite, **non envoyé réellement** grâce au kill-switch

## 7. Actions recommandées (backlog)

### 🔴 P0 — avant le batch Migration
- [ ] Revue manuelle des 70 adresses réelles touchées : si besoin, email correctif depuis admin expliquant l'erreur + rappel du lien /forgot-password
- [ ] Avant réactivation du real-send : vérifier manuellement le segment de la campagne Migration (`cmp_sys_migration_draft`) pour s'assurer qu'il cible uniquement les users attendus

### 🟠 P1 — prochaines itérations
- [ ] Ajouter un cap dur d'audience par campagne (`messaging_max_audience_per_campaign`, défaut `200`, éditable admin). Les envois > cap doivent exiger confirmation explicite supplémentaire.
- [ ] Ajouter une colonne `users.account_tier` (`sandbox|production|migration_pending`) et permettre aux tests automatisés de cibler uniquement `sandbox`
- [ ] Retirer le segment `seg_non_pro` du seed SYSTEM_SEGMENTS (ou au moins le marquer `is_dangerous=TRUE`) — actuellement plus personne ne devrait l'utiliser

### 🟡 P2 — hygiène
- [ ] Ajouter un rate limit HTTP sur `POST /api/admin/messaging/campaigns/{id}/send` (actuellement aucune limite : un admin peut déclencher autant de sends qu'il veut)
- [ ] Nettoyer les ~15 campagnes pytest orphelines restantes en DB (status `paused` ou `draft` avec name commençant par `pytest_`/`indiv_`/`var_test`/`renamed`)
- [ ] Ajouter un dashboard admin affichant les 10 derniers sends avec leur provider_message_id Resend (actuellement `provider_message_id` n'est pas persisté — à fixer dans `_send_one`)

## 8. Leçons apprises

1. **Production data change ≠ no-op pour les tests** : la migration de 1 158 users a changé silencieusement la sémantique de segments existants. Toute migration massive doit déclencher une revue des assertions de test.
2. **Kill-switch avant kill-switch** : le worker tournait sans aucun mécanisme d'arrêt d'urgence au-delà du supervisor restart. Maintenant couvert par `messaging_real_send_enabled`.
3. **Segments production vs sandbox** : il faut une séparation explicite. C'est désormais en place via `seg_pytest_safe`.
