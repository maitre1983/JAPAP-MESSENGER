"""
iter237ac — On-chain verification of manual USDT deposits.

When a user pastes their tx_hash via PATCH /api/wallet/deposit/{tx_id}/hash,
we call the corresponding blockchain explorer API to verify:

  1. Transaction exists and is confirmed (≥ 1 confirmation).
  2. Recipient address matches our JAPAP receiving address.
  3. The transferred USDT amount is ≥ the amount the user committed to.

Returns a structured result so the caller (wallet.py) can either credit
the wallet immediately on success, or leave the deposit in `pending`
status (existing manual review path) on any failure.

Networks supported:
  • TRC20 (Tron)  — Tronscan API (no API key required for tx-info)
  • BEP20 (BSC)   — BscScan API (BSCSCAN_API_KEY required)

USDT contract addresses (canonical, mainnet):
  • TRC20 : TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t
  • BEP20 : 0x55d398326f99059fF775485246999027B3197955

All HTTP calls are bounded by a 10-second timeout to never block the user.
"""
from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# USDT contract canonical addresses on each chain. Used to validate that
# the on-chain transfer is in fact USDT (not some other token).
USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_BEP20_CONTRACT = "0x55d398326f99059fF775485246999027B3197955".lower()

# BEP20 ERC-20 transfer event topic (keccak256("Transfer(address,address,uint256)")).
BEP20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

ONCHAIN_TIMEOUT_SECONDS = 10.0

# iter237ac — BscScan v1 was deprecated in 2026 and v2 (Etherscan unified)
# requires a paid plan for BSC. We use public BSC RPC nodes directly via
# JSON-RPC instead — they're fully open, fast, and authoritative. We keep
# the BSCSCAN_API_KEY as a future fallback if the user upgrades to v2.
BSC_RPC_NODES = [
    "https://bsc-dataseed.binance.org/",
    "https://bsc-dataseed1.defibit.io/",
    "https://bsc-dataseed1.ninicoin.io/",
]


def _get_japap_address(network: str) -> Optional[str]:
    """Returns the configured JAPAP receiving address for a network (case-preserving for TRON)."""
    if network == "trc20":
        return os.environ.get("JAPAP_TRC20_ADDRESS")
    if network == "bep20":
        return (os.environ.get("JAPAP_BEP20_ADDRESS") or "").lower() or None
    return None


def detect_network_from_notes(notes: str) -> Optional[str]:
    """Map the deposit's `notes` field (e.g. '[USDT (TRC20) manuel] ...') to
    a normalized network identifier. Returns None if the notes don't carry
    a recognizable network tag (caller should then skip auto-verify)."""
    if not notes:
        return None
    upper = notes.upper()
    if "TRC20" in upper:
        return "trc20"
    if "BEP20" in upper or "BSC" in upper:
        return "bep20"
    return None


# ─────────────────────────── TRC20 (Tron) ───────────────────────────────
async def _verify_trc20(tx_hash: str, expected_amount_usd: Decimal,
                        japap_address: str) -> dict:
    """Fetches the transaction from Tronscan and validates it. Network
    timeout / error → status='error' (the caller keeps the deposit pending)."""
    url = f"https://apilist.tronscanapi.com/api/transaction-info?hash={tx_hash}"
    try:
        async with httpx.AsyncClient(timeout=ONCHAIN_TIMEOUT_SECONDS) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
        if r.status_code != 200:
            return {"verified": False, "status": "error",
                    "reason": f"Tronscan HTTP {r.status_code}"}
        data = r.json() or {}
    except httpx.TimeoutException:
        return {"verified": False, "status": "error", "reason": "Tronscan timeout"}
    except Exception as e:  # noqa: BLE001
        return {"verified": False, "status": "error", "reason": f"Tronscan error: {e}"}

    # Tronscan returns {} (or no hash field) when the tx does not exist.
    if not data or not data.get("hash"):
        return {"verified": False, "status": "not_found", "reason": "Hash introuvable on-chain."}

    # Look at the TRC20 token transfer info on this tx. Tronscan exposes
    # `trc20TransferInfo` (legacy) or `tokenTransferInfo` (newer schema).
    transfers = data.get("trc20TransferInfo") or []
    if not transfers and isinstance(data.get("tokenTransferInfo"), dict):
        transfers = [data["tokenTransferInfo"]]
    if not transfers:
        return {"verified": False, "status": "no_transfer",
                "reason": "Aucun transfert TRC20 détecté dans cette transaction."}

    # Find a transfer whose contract is USDT and whose recipient matches us.
    for transfer in transfers:
        contract = transfer.get("contract_address") or transfer.get("contractAddress") or ""
        to_addr = transfer.get("to_address") or transfer.get("toAddress") or ""
        if contract != USDT_TRC20_CONTRACT:
            continue
        if to_addr != japap_address:
            continue
        # Amount in raw units (USDT TRC20 has 6 decimals).
        try:
            raw = Decimal(str(transfer.get("amount_str") or transfer.get("amount") or "0"))
            decimals = int(transfer.get("decimals", 6))
        except Exception:  # noqa: BLE001
            return {"verified": False, "status": "parse_error",
                    "reason": "Montant USDT illisible."}
        amount_usdt = (raw / (Decimal(10) ** decimals)) if raw else Decimal(0)
        if amount_usdt < expected_amount_usd:
            return {"verified": False, "status": "amount_too_low",
                    "reason": f"Montant reçu {amount_usdt} USDT < attendu {expected_amount_usd} USDT.",
                    "received_amount": str(amount_usdt)}
        # Confirmation status — Tronscan exposes `confirmed` (bool) and
        # `confirmations` (int). Some API versions only have `confirmed`.
        confirmed = bool(data.get("confirmed", True))
        confirmations = int(data.get("confirmations") or 0)
        if not confirmed and confirmations < 1:
            return {"verified": False, "status": "unconfirmed",
                    "reason": "Transaction non confirmée."}
        return {
            "verified": True, "status": "confirmed", "network": "trc20",
            "received_amount": str(amount_usdt),
            "confirmations": confirmations,
            "from_address": transfer.get("from_address") or transfer.get("fromAddress") or "",
        }

    # No matching USDT-to-japap transfer in this tx.
    return {"verified": False, "status": "wrong_recipient",
            "reason": "Aucun transfert USDT vers l'adresse JAPAP dans cette transaction."}


# ─────────────────────────── BEP20 (BSC) ────────────────────────────────
async def _verify_bep20(tx_hash: str, expected_amount_usd: Decimal,
                        japap_address: str) -> dict:
    """Fetches the transaction receipt directly from public BSC RPC nodes
    (no API key required, no rate limit issues for our volume) and
    validates the USDT Transfer event. Falls through to the next node on
    failure so a single node outage doesn't break the flow."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getTransactionReceipt",
        "params": [tx_hash],
    }
    receipt = None
    last_error: Optional[str] = None
    for node in BSC_RPC_NODES:
        try:
            async with httpx.AsyncClient(timeout=ONCHAIN_TIMEOUT_SECONDS) as client:
                r = await client.post(node, json=payload)
            if r.status_code != 200:
                last_error = f"BSC RPC HTTP {r.status_code}"
                continue
            data = r.json() or {}
            if data.get("error"):
                last_error = f"BSC RPC error: {data['error']}"
                continue
            receipt = data.get("result")
            break  # success — even if receipt is None (tx not mined)
        except httpx.TimeoutException:
            last_error = "BSC RPC timeout"
            continue
        except Exception as e:  # noqa: BLE001
            last_error = f"BSC RPC error: {e}"
            continue

    if receipt is None and last_error:
        return {"verified": False, "status": "error", "reason": last_error}
    if not receipt or isinstance(receipt, str):
        return {"verified": False, "status": "not_found",
                "reason": "Hash introuvable on-chain."}

    # Tx must be successful (status == "0x1").
    if str(receipt.get("status", "")).lower() != "0x1":
        return {"verified": False, "status": "tx_failed",
                "reason": "Transaction échouée on-chain."}

    # Walk the logs for a USDT Transfer event TO our address.
    logs = receipt.get("logs") or []
    for log in logs:
        log_addr = (log.get("address") or "").lower()
        topics = log.get("topics") or []
        if log_addr != USDT_BEP20_CONTRACT:
            continue
        if not topics or topics[0].lower() != BEP20_TRANSFER_TOPIC:
            continue
        if len(topics) < 3:
            continue
        # topics[2] = recipient (32-byte left-padded address).
        recipient_topic = topics[2].lower()
        recipient_addr = "0x" + recipient_topic[-40:]
        if recipient_addr != japap_address:
            continue
        # data = uint256 amount in hex. USDT BEP20 has 18 decimals on BSC.
        try:
            raw_hex = (log.get("data") or "0x0").lower()
            amount_raw = Decimal(int(raw_hex, 16))
        except Exception:  # noqa: BLE001
            return {"verified": False, "status": "parse_error",
                    "reason": "Montant USDT illisible."}
        amount_usdt = amount_raw / (Decimal(10) ** 18)
        if amount_usdt < expected_amount_usd:
            return {"verified": False, "status": "amount_too_low",
                    "reason": f"Montant reçu {amount_usdt} USDT < attendu {expected_amount_usd} USDT.",
                    "received_amount": str(amount_usdt)}
        # If the receipt is present, the tx is mined → at least 1 confirmation.
        from_addr_topic = topics[1].lower()
        return {
            "verified": True, "status": "confirmed", "network": "bep20",
            "received_amount": str(amount_usdt),
            "confirmations": 1,
            "from_address": "0x" + from_addr_topic[-40:],
        }

    return {"verified": False, "status": "wrong_recipient",
            "reason": "Aucun transfert USDT vers l'adresse JAPAP dans cette transaction."}


# ─────────────────────────── Public entrypoint ─────────────────────────
async def verify_usdt_deposit(network: str, tx_hash: str,
                              expected_amount_usd: Decimal) -> dict:
    """Verify a USDT deposit on-chain. Returns a dict with at minimum:
        verified: bool
        status:   'confirmed' | 'not_found' | 'wrong_recipient' |
                  'amount_too_low' | 'unconfirmed' | 'error' | …
        reason:   human-friendly explanation (ALWAYS present when not verified)
    Optional fields when confirmed: received_amount, confirmations,
    from_address, network.

    A network/parse error never raises — the deposit simply stays pending
    (admin reviews manually as before). Always best-effort."""
    network = (network or "").lower()
    japap_addr = _get_japap_address(network)
    if not japap_addr:
        return {"verified": False, "status": "config_missing",
                "reason": f"Adresse JAPAP {network.upper()} non configurée."}
    try:
        if network == "trc20":
            return await _verify_trc20(tx_hash, expected_amount_usd, japap_addr)
        if network == "bep20":
            return await _verify_bep20(tx_hash, expected_amount_usd, japap_addr.lower())
    except Exception as e:  # noqa: BLE001
        # Truly unexpected fallback — keep deposit pending.
        logger.warning("[usdt-onchain] unexpected error: %s", e)
        return {"verified": False, "status": "error", "reason": str(e)}
    return {"verified": False, "status": "unknown_network",
            "reason": f"Réseau {network} non supporté."}


__all__ = ["verify_usdt_deposit", "detect_network_from_notes"]
