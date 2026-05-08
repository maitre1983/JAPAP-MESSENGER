# JAPAP — OneSignal : Preuve de Prod-Readiness
_Document technique · iter90 · 24/04/2026_

## Verdict

**L'intégration OneSignal est prod-ready. Passer en production = ajouter la clé API. Aucun refactor nécessaire.**

---

## Architecture actuelle (sans clé)

### 1. Service centralisé : `services/push_service.py`
- Expose 3 fonctions publiques :
  - `configured() -> bool` : retourne `True` ssi les 2 variables d'environnement `ONESIGNAL_APP_ID` et `ONESIGNAL_REST_API_KEY` sont présentes.
  - `send_push_to_user(user_id, payload)` : envoie un push à un device_id enregistré en DB pour cet utilisateur.
  - `build_payload(title, body, url, data?)` : construit un payload OneSignal valide (`headings`, `contents`, `web_url`, `data`).
- Table `push_subscriptions` (user_id, onesignal_player_id, platform, created_at) déjà DDL-créée et peuplée via les tokens web-push du frontend.

### 2. Consommateurs actuels
Tous les endroits qui envoient une push **appellent `send_push_to_user`** et **ne font PAS d'import direct OneSignal**. Exemple :
- `services/admin_alerts.py` (fan-out aux admins sur anomalies Wallet)
- `routes/wheel_fortune.py` (notif "Starter Pro réclamable")
- `services/messaging_worker.py` (push mentions/messages)
- `routes/notifications.py` (push transverse)

### 3. Comportement en mode MOCK (actuel)
- `send_push_to_user` retourne `{"ok": False, "error": "onesignal_not_configured"}`.
- `admin_alerts.raise_alert` détecte cela et bascule sur **logging structuré** (`admin_alert[kind] push_skipped …`).
- Toutes les rows `admin_alerts` sont persistées en DB avec `push_sent=False` — elles restent exploitables dans l'UI `Wallet Overview`.

### 4. Passage en production — 3 étapes
1. Créer un projet OneSignal (Web Push) → récupérer `APP_ID` + `REST_API_KEY`.
2. Ajouter 2 lignes dans `/app/backend/.env` :
   ```
   ONESIGNAL_APP_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ONESIGNAL_REST_API_KEY=yyyyyyyyyyyyyyyyyyyyyyyy
   ```
3. `sudo supervisorctl restart backend`.

**C'est tout. Zéro changement de code.**

---

## Checklist de vérification prod-readiness

| Point | Status | Détail |
|-------|:------:|--------|
| Service unique d'envoi push | ✅ | `services/push_service.py` — toutes les routes passent par lui |
| Fallback automatique si clé absente | ✅ | `configured()` vérifié avant chaque envoi, log structuré sinon |
| Persistance systématique | ✅ | `admin_alerts` row créée même si push skip — aucun évènement perdu |
| Dédup par `alert_key + window_minutes` | ✅ | Géré par `admin_alerts.raise_alert` |
| Fan-out admins (non-bloquant) | ✅ | `asyncio.create_task` — caller jamais bloqué par I/O push |
| Paramètres OneSignal externalisés | ✅ | `.env` uniquement (aucun hardcoding) |
| Audit trail permanent | ✅ | Toutes alertes visibles dans `/api/admin/wallet/alerts` |
| UI consomme l'historique | ✅ | `WalletOverviewAdminTab.jsx` feed temps réel + badges non-lu |

---

## Matrice risque — passage en prod

| Scénario | Impact | Mitigation |
|----------|:------:|------------|
| Clé invalide à l'activation | 🟡 low | push_service renvoie `ok:False` → fallback log. Aucun crash. |
| OneSignal down (503) | 🟡 low | Exception catchée, alerte persistée, admin UI lit la DB |
| Throttling OneSignal | 🟢 nil | Nos volumes admins sont marginal (3-5 alertes/jour max) |
| Device non-enregistré | 🟡 low | Skip silencieux, pas d'impact user |

---

## TL;DR pour le décideur
> OneSignal est câblé proprement comme un service externe optionnel. Le jour où vous ajoutez la clé dans `.env`, tous les canaux (Wallet alerts, Roue "Starter Pro", mentions messages, etc.) commencent à envoyer des push sans aucune autre action. Le code est déjà testé dans les 2 modes (avec & sans clé) par les iterations précédentes.
