# JAPAP — Secrets Backup Runbook

**Owner:** Admin JAPAP
**Last updated:** Feb 2026 (Iter 65)
**Criticality:** 🔴 MAX — perte = données utilisateur irrécupérables

Ce document couvre la procédure de sauvegarde et de restauration des deux
secrets critiques du backend JAPAP. Ces secrets sont symétriques : leur
perte rend les données chiffrées correspondantes **définitivement illisibles**.

---

## 1. `WIFI_ENCRYPTION_KEY` — Chiffrement Fernet JAPAP Connect

### Portée
Chiffre tous les mots de passe Wi-Fi des hotspots JAPAP Connect
(`wifi_hotspots.wifi_password_encrypted`).
**Fernet** = AES-128-CBC + HMAC-SHA256 symétrique — **pas de récupération
possible sans la clé**.

### Où elle est stockée
- Fichier : `/app/backend/.env`
- Clé : `WIFI_ENCRYPTION_KEY=<base64-urlsafe-32-bytes>`
- Jamais committée en git (`.env` est dans `.gitignore`).

### Impact si perdue
- Tous les QR déjà actifs restent valides jusqu'à expiration (60 s).
- **Après redémarrage du backend** : tous les hotspots existants retournent
  503 au moment du redeem car `decrypt_password()` lève `WifiCryptoError`.
- Les owners doivent tous re-saisir leur mot de passe Wi-Fi un par un.
- Les utilisateurs déjà connectés via un hotspot gardent leur accès Wi-Fi
  local (le chiffrement concerne la révélation côté serveur, pas la
  connexion physique).

### Procédure de sauvegarde

**Immédiatement après génération initiale** :
```bash
# Sur le serveur
grep WIFI_ENCRYPTION_KEY /app/backend/.env
```

1. Copier la valeur dans **3 coffres distincts** :
   - 1Password (vault `JAPAP-Prod-Secrets` → entrée `WIFI_ENCRYPTION_KEY`)
   - Coffre offline imprimé physique (safe bureau, enveloppe scellée)
   - GPG-chiffré sur un disque externe (loi des 3-2-1)
2. Ne **jamais** l'envoyer par email, Slack, SMS, chat.
3. Documenter la date de rotation dans `/app/memory/secrets_audit.md`
   (même si non existant encore — créer si besoin).

### Procédure de restauration
Si le fichier `.env` du backend est perdu/écrasé :
```bash
# 1. Récupérer la clé depuis 1Password
# 2. L'insérer dans /app/backend/.env (pas de guillemets, une seule ligne)
echo 'WIFI_ENCRYPTION_KEY=<valeur-depuis-1password>' >> /app/backend/.env

# 3. Vérifier qu'il n'y a pas de doublon
grep -c WIFI_ENCRYPTION_KEY /app/backend/.env   # attendu : 1

# 4. Redémarrer le backend
sudo supervisorctl restart backend

# 5. Valider avec un round-trip
cd /app/backend && python3 -c "
from dotenv import load_dotenv; load_dotenv()
from services.wifi_crypto import health_check
print(health_check())  # attendu : {'ok': True, 'key_set': True}"

# 6. Vérifier qu'un hotspot existant peut être redeemed
```

### Rotation (urgence seulement)
La clé ne peut **pas être rotée** sans ré-chiffrement de toutes les lignes
`wifi_password_encrypted`. Procédure exceptionnelle :
1. Générer une nouvelle clé : `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
2. Script Python : lire chaque `wifi_password_encrypted` avec l'ancienne
   clé, ré-encrypter avec la nouvelle, remplacer en BDD.
3. Mettre à jour `.env`, sauvegarder les deux clés pendant 7 jours.
4. Purger l'ancienne clé après vérification complète.

---

## 2. `EMAIL_UNSUB_SECRET` — HMAC tokens de désabonnement

### Portée
Signe les tokens HMAC-SHA256 des liens 1-click dans les emails marketing :
`{BASE_URL}/api/email/unsubscribe?u=<uid>&t=<token>`.

### Où elle est stockée
- Fichier : `/app/backend/.env`
- Clé : `EMAIL_UNSUB_SECRET=<base64-urlsafe-32-bytes>`

### Impact si perdue
- **Tous les liens de désabonnement déjà distribués deviennent invalides
  (HTTP 400)**.
- Les utilisateurs ne peuvent plus se désabonner via email — seulement via
  l'app ou via une commande de complaint Resend (→ webhook auto-unsub).
- **RGPD** : non-conformité potentielle si rotation non-communiquée.
- Pas de perte de données utilisateur en lui-même.

### Procédure de sauvegarde
Identique à `WIFI_ENCRYPTION_KEY` :
1. Copier dans 1Password, vault `JAPAP-Prod-Secrets`, entrée
   `EMAIL_UNSUB_SECRET`.
2. Ne pas partager.
3. Documenter la date d'installation dans `secrets_audit.md`.

### Procédure de restauration
```bash
echo 'EMAIL_UNSUB_SECRET=<valeur-depuis-1password>' >> /app/backend/.env
grep -c EMAIL_UNSUB_SECRET /app/backend/.env   # attendu : 1
sudo supervisorctl restart backend

# Test : régénérer un token et le valider
cd /app/backend && python3 -c "
from dotenv import load_dotenv; load_dotenv()
from services.email_renderer import sign_unsub_token, verify_unsub_token
t = sign_unsub_token('user_test')
print('token ok :', verify_unsub_token('user_test', t))  # attendu : True"
```

### Rotation (recommandée : jamais en prod)
La rotation invalide **tous les liens existants dans les boîtes mail
utilisateur**. À réserver aux cas de compromission avérée.
1. Remplacer la valeur dans `/app/backend/.env`.
2. Redémarrer le backend.
3. Communiquer aux utilisateurs (bandeau in-app) que les anciens liens
   sont invalides et les inviter à utiliser le bouton "Préférences email"
   dans leur profil.

---

## 3. Checklist mensuelle (1er du mois)
- [ ] Vérifier que les 2 secrets sont bien dans 1Password, lisibles par au
      moins 2 membres de l'équipe admin.
- [ ] Vérifier que `/app/backend/.env` contient exactement 1 ligne par
      secret (pas de doublon).
- [ ] Tester round-trip Fernet (`health_check()` backend log).
- [ ] Tester un `verify_unsub_token` sur un uid connu.
- [ ] Mettre à jour `secrets_audit.md` avec la date de la vérification.

## 4. Checklist en cas d'incident
- [ ] Ne **jamais** regénérer une nouvelle clé sans d'abord vérifier qu'il
      ne s'agit pas d'une erreur de lecture du `.env` (ex: fichier tronqué).
- [ ] Escalader auprès du Lead Backend avant toute rotation.
- [ ] Documenter l'incident : date, cause, actions, ré-encrypt logs.

---

## 5. Variables complémentaires à surveiller
Secrets secondaires (impact moindre mais à sauvegarder aussi) :
- `JWT_SECRET` — perte = invalide tous les JWT actifs (users re-login).
- `RESEND_API_KEY` — perte = pas d'emails jusqu'à régénération côté Resend.
- `LIVEKIT_API_KEY` + `LIVEKIT_API_SECRET` — perte = pas d'appels jusqu'à
  régénération côté LiveKit.
- `R2_ACCESS_KEY_ID` + `R2_SECRET_ACCESS_KEY` — perte = pas de recordings
  (fichiers déjà uploadés OK, lecture dépend du bucket policy).

Ces secrets peuvent être **régénérés** chez le provider — aucune donnée
utilisateur perdue. Mais à traiter avec la même rigueur dans 1Password.
