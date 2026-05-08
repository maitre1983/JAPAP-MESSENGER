# 🛡️ JAPAP — Runbook de sécurité infrastructure (iter82)

> Ce document décrit les opérations manuelles à effectuer dans les dashboards des fournisseurs tiers (Cloudflare, Neon, OneSignal) après chaque déploiement en production. Ces actions **ne peuvent pas être automatisées depuis le code** — elles nécessitent un accès admin au compte fournisseur.

---

## 1. Cloudflare — WAF & DDoS

### 1.1 Activer le Managed Ruleset OWASP
1. Aller dans **Cloudflare Dashboard → japapmessenger.com → Security → WAF → Managed rules**
2. Activer :
   - ✅ **Cloudflare Managed Ruleset** → action: `Block`, sensibility: `High`
   - ✅ **Cloudflare OWASP Core Ruleset** → Paranoia level: `PL2`, threshold: `60`
   - ✅ **Cloudflare Exposed Credentials Check** → action: `Log` (non-blocking pour ne pas casser les users qui réutilisent un mdp)
3. Exemptions recommandées :
   - Exempter `/api/email-tracking/webhook` du ruleset OWASP (Resend envoie du JSON avec signature → faux-positif)
   - Exempter `/api/og/post/*` (les scrapers FB/WhatsApp déclenchent des règles bot)

### 1.2 DDoS Protection
1. **Security → DDoS** → vérifier que **HTTP DDoS Attack Protection** est à `Default` (auto-scaling, pas de config manuelle nécessaire)
2. **Security → Bots** → activer **Bot Fight Mode** (gratuit) ou **Super Bot Fight Mode** (plan Pro)

### 1.3 Rate Limiting règles
Ajouter dans **Security → WAF → Rate limiting rules** :

| Nom | Match | Threshold | Action |
|---|---|---|---|
| `auth-abuse` | `http.request.uri.path contains "/api/auth/"` | 20 req / 1min / IP | Challenge |
| `otp-abuse` | `http.request.uri.path contains "/api/auth/verify-otp"` | 10 req / 5min / IP | Block 1h |
| `upload-abuse` | `http.request.uri.path starts_with "/api/upload"` | 30 req / 1min / IP | Challenge |
| `admin-protect` | `http.request.uri.path starts_with "/api/admin"` | 60 req / 1min / IP | Challenge |

### 1.4 Firewall Rules custom
- **Bloquer les user-agents suspects** :
  ```
  (http.user_agent contains "sqlmap") or
  (http.user_agent contains "nikto") or
  (http.user_agent contains "masscan") or
  (http.user_agent contains "nmap") or
  (http.user_agent contains "wpscan")
  → Action: Block
  ```
- **Challenge les pays à haut risque** (si applicable selon la base utilisateurs) :
  ```
  (ip.geoip.country in {"CN" "RU" "KP"}) and (http.request.uri.path starts_with "/api/admin")
  → Action: JS Challenge
  ```

### 1.5 Page Shield (anti-Magecart)
Activer **Security → Page Shield** pour détecter les scripts tiers malicieux injectés dans le JS frontend.

---

## 2. Neon PostgreSQL — Restriction réseau

### 2.1 IP Allow-list
1. Aller dans **Neon Console → japap-prod → Settings → IP Allow**
2. Activer l'allow-list
3. Ajouter les CIDR des nodes K8s Emergent. Pour les récupérer :
   ```bash
   # Depuis un pod en prod
   curl -s https://ipinfo.io/ip
   # Ou depuis le réseau K8s
   kubectl get nodes -o wide | awk '{print $6}'
   ```
   Coller la liste séparée par virgules dans Neon.
4. Ajouter également vos IPs admin (bureau, VPN) pour les backups manuels.
5. **⚠️ Tester** : après activation, faire un `curl $DATABASE_URL` depuis une IP non whitelistée → doit timeout.

### 2.2 Rôles DB dédiés
1. Créer 2 rôles au lieu d'un seul compte postgres :
   ```sql
   -- Rôle applicatif (utilisé par le backend en prod)
   CREATE ROLE japap_app WITH LOGIN PASSWORD '<strong-password>';
   GRANT CONNECT ON DATABASE japap TO japap_app;
   GRANT USAGE ON SCHEMA public TO japap_app;
   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO japap_app;
   GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO japap_app;

   -- Rôle admin/ops (pour les migrations / debug)
   CREATE ROLE japap_admin WITH LOGIN PASSWORD '<strong-password>';
   GRANT ALL PRIVILEGES ON DATABASE japap TO japap_admin;
   ```
2. Mettre à jour `DATABASE_URL` pour utiliser `japap_app:password@...` en prod.
3. Le rôle admin n'est utilisé **qu'en interactive** (psql manuel pour les interventions urgentes).

### 2.3 Backups & restore drill
1. Neon fait des snapshots automatiques (Point-in-Time Recovery 7j en plan Pro).
2. **Test de restauration mensuel** (obligatoire) :
   ```bash
   # Depuis le dashboard Neon
   # → Backups → Point-in-time → Choose timestamp 24h ago → "Branch from this point"
   # → Se connecter à la nouvelle branche et vérifier que les données existent
   ```
3. Exporter un dump hebdomadaire vers S3/R2 (off-site) :
   ```bash
   pg_dump "$DATABASE_URL" | gzip > japap_$(date +%Y%m%d).sql.gz
   aws s3 cp japap_*.sql.gz s3://japap-backups/postgres/
   ```

---

## 3. Emergent Runtime — Secrets & env

### 3.1 Variables obligatoires en prod
```
JWT_SECRET=<32+ bytes random>
COOKIE_SECURE=true
COOKIE_SAMESITE=lax
CSRF_PROTECTION=true
CORS_ORIGINS=https://japapmessenger.com,https://app.japap.com
ADMIN_EMAIL=admin@japap.com
ADMIN_PASSWORD=<16+ chars>
EMERGENT_LLM_KEY=<from profile>
RESEND_API_KEY=<prod key>
ONESIGNAL_APP_ID=<...>
ONESIGNAL_REST_API_KEY=<...>
DATABASE_URL=postgresql://japap_app:...@...neon.tech/japap
```

### 3.2 Rotation des secrets (obligatoire, trimestriel)
1. `JWT_SECRET` → rotation force tout le monde à se reconnecter (voulu)
2. `ADMIN_PASSWORD` → rotation + updater `/app/memory/test_credentials.md`
3. `RESEND_API_KEY` → rotation sans impact utilisateur
4. `DATABASE_URL` password → rotation via `ALTER ROLE japap_app WITH PASSWORD ...`

### 3.3 Checklist pré-déploiement prod
- [ ] `COOKIE_SECURE=true` dans `.env` prod
- [ ] `CORS_ORIGINS` restrictif (pas de `*`)
- [ ] `CSRF_PROTECTION=true`
- [ ] Neon IP allow-list activée
- [ ] Cloudflare WAF OWASP activé
- [ ] Rate limiting rules créées
- [ ] Admin password changé vs default
- [ ] Backup test réalisé dans le mois
- [ ] `yarn audit` — 0 high/critical
- [ ] `pip-audit` — 0 high/critical
- [ ] `bandit -r /app/backend` — 0 high
- [ ] `semgrep --config auto` — 0 error

---

## 4. OneSignal — Alertes admin

### 4.1 Canal "security_alerts"
1. Dans OneSignal → **Tags** → créer un tag `role=admin`
2. Le backend pousse les alertes sécurité via ce canal (`send_push_to_user` filtré par tag)
3. Événements qui déclenchent une notif automatique à l'admin :
   - `auth.refresh_replay_detected` (critical)
   - `> 50 failed logins en 10min` (warning)
   - `admin_settings.changed` (info)

### 4.2 Intégration Slack (optionnel, plan team)
- Forwarder les alertes CRITICAL vers un canal `#japap-security` via OneSignal → Slack integration.

---

## 5. Monitoring & SLOs

### 5.1 Métriques à suivre (à builder avec Grafana / Cloudflare Analytics)
| Métrique | SLO cible |
|---|---|
| Taux d'échec login (24h) | < 5% |
| Alertes critical security (/jour) | 0 (objectif zero) |
| Replay detections (/semaine) | 0 |
| Upload rejections (% total uploads) | < 1% |
| CSRF guard rejections (/heure) | < 10 |
| Latence p95 `/api/auth/login` | < 400ms |

### 5.2 Revues hebdomadaires
Chaque lundi :
1. Ouvrir `GET /api/security/events?limit=200` en admin
2. Grep les `severity=critical` sur 7j
3. Ouvrir le rapport `security_reports/summary_*.json` du weekend
4. Si >0 high/critical non résolu → ticket P0 ouvert

### 5.3 Incident response
**RACI** :
- **Detect** : `security_monitor.py` + Cloudflare alerts
- **Contain** : admin via `/api/security/logout-all` + `revoke_all_user_jtis` si compte compromis
- **Eradicate** : déployer hotfix + rotation JWT_SECRET si nécessaire
- **Recover** : restore Neon snapshot si corruption DB
- **Lessons** : post-mortem dans `/app/memory/postmortems/YYYY-MM-DD_<incident>.md`

---

## 6. Responsible Disclosure

### 6.1 `/.well-known/security.txt`
Créer cette route dans `routes/public_assets.py` pour permettre aux chercheurs en sécurité de contacter l'équipe :
```
Contact: mailto:security@japapmessenger.com
Contact: https://japapmessenger.com/security-report
Expires: 2027-12-31T23:59:59Z
Encryption: https://japapmessenger.com/pgp.txt
Acknowledgments: https://japapmessenger.com/security-hall-of-fame
Preferred-Languages: fr, en
Canonical: https://japapmessenger.com/.well-known/security.txt
```

### 6.2 Bug bounty interne
- Récompense : 6 mois Pro + 50 USDT / finding unique
- Échelle :
  - P0 (RCE, auth bypass, IDOR critique) : 500 USDT + 12 mois Pro
  - P1 (XSS, CSRF, fuite PII limitée) : 150 USDT + 6 mois Pro
  - P2 (info disclosure, header missing) : 50 USDT + 3 mois Pro

---

## 7. Journal des actions

| Date | Action | Exécuté par | Statut |
|---|---|---|---|
| 2026-04-23 | Audit iter82 initial + fix P0 (JWT secure, upload magic-byte, path traversal, legacy hash) | Agent E1 | ✅ |
| 2026-04-23 | P1 fix : rotation refresh_token, CSRF middleware, security headers | Agent E1 | ✅ |
| 2026-04-23 | Active sessions + logout-all + security events API | Agent E1 | ✅ |
| 2026-04-23 | CI scan.sh + security_monitor.py + .github/workflows/security.yml | Agent E1 | ✅ |
| _À faire_ | Cloudflare WAF OWASP activation | Admin humain | ⏳ |
| _À faire_ | Neon IP allow-list | Admin humain | ⏳ |
| _À faire_ | security.txt publication | Admin humain | ⏳ |
