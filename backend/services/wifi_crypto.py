"""
Fernet-based symmetric encryption for WiFi passwords at rest.

The key is read from `WIFI_ENCRYPTION_KEY` (set in backend/.env). If the key
is missing, any call raises `WifiCryptoError` so we fail fast rather than
silently storing plaintext.

Fernet guarantees:
    - AES-128-CBC + HMAC-SHA256 authenticated encryption
    - timestamp + versioning embedded in every ciphertext
    - no way to recover plaintext without the key (⚠ back it up externally)

Used by:
    - routes/connect.py   → PUT /hotspots/{id}/wifi   (encrypt on write)
                          → POST /access/redeem       (decrypt on reveal)
"""
import os
import logging

logger = logging.getLogger(__name__)


class WifiCryptoError(Exception):
    """Raised when the WIFI_ENCRYPTION_KEY is missing or malformed."""


def _get_fernet():
    key = os.environ.get("WIFI_ENCRYPTION_KEY", "").strip()
    if not key:
        raise WifiCryptoError(
            "WIFI_ENCRYPTION_KEY absente de l'environnement. "
            "Générez-la avec `python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'` puis ajoutez-la à backend/.env."
        )
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:
        raise WifiCryptoError(f"cryptography non installé : {e}")
    try:
        return Fernet(key.encode())
    except Exception as e:
        raise WifiCryptoError(f"Clé Fernet invalide : {e}")


def encrypt_password(plaintext: str) -> str:
    """Encrypt a WiFi password → base64 ciphertext (stored in wifi_hotspots)."""
    if not plaintext:
        return ""
    f = _get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_password(ciphertext: str) -> str:
    """Decrypt a stored ciphertext back to plaintext."""
    if not ciphertext:
        return ""
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except Exception as e:
        logger.warning(f"wifi password decrypt failed: {e}")
        raise WifiCryptoError(
            "Impossible de déchiffrer le mot de passe (clé WIFI_ENCRYPTION_KEY a-t-elle changé ?)."
        )


def health_check() -> dict:
    """Quick round-trip self-test for admin diagnostics."""
    try:
        sample = "japap_wifi_health_check_2026"
        enc = encrypt_password(sample)
        dec = decrypt_password(enc)
        return {"ok": dec == sample, "key_set": True}
    except WifiCryptoError as e:
        return {"ok": False, "key_set": False, "reason": str(e)}
