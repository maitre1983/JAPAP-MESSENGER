import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import * as WS from '../../utils/walletSecurity';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * iter156 — Live deposit status card (Hubtel / generic).
 *
 * Polls GET /api/wallet/deposit/{tx_id}/status every 4 seconds. As soon
 * as the backend reports `is_paid=true` or `tx_status=completed`, we
 * trigger the `onDone` callback so the parent refreshes the balance +
 * closes the modal. Shows a clear "Paiement en cours..." message while
 * waiting — never mentions admin validation.
 */
export default function HubtelDepositStatus({ txId, onDone }) {
  const [status, setStatus] = useState('pending');
  const [elapsed, setElapsed] = useState(0);
  const [lastProviderStatus, setLastProviderStatus] = useState('');
  const doneRef = useRef(false);

  useEffect(() => {
    if (!WS.isSafeTxId(txId)) {
      setStatus('error');
      return;
    }
    let cancelled = false;
    let interval;
    const poll = async () => {
      try {
        const { data } = await axios.get(
          `${API}/api/wallet/deposit/${txId}/status`,
          { withCredentials: true },
        );
        if (cancelled) return;
        setStatus(data.tx_status || 'pending');
        setLastProviderStatus(WS.safeProviderStatus(data.payment_status));
        if (data.is_paid && !doneRef.current) {
          doneRef.current = true;
          clearInterval(interval);
          setTimeout(() => onDone && onDone(), 500);
        }
      } catch (_e) { /* silent — retry on next tick */ }
    };
    // First probe immediately, then every 4s.
    poll();
    interval = setInterval(() => {
      setElapsed(s => {
        const next = s + 4;
        // iter221 — auto-stop polling after 300s (5 min) to prevent runaway loops.
        if (next >= 300) clearInterval(interval);
        return next;
      });
      poll();
    }, 4000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [txId, onDone]);

  const isCompleted = status === 'completed' || doneRef.current;
  return (
    <div
      data-testid="hubtel-deposit-status"
      className="rounded-xl p-4"
      style={{
        background: isCompleted ? '#DCFCE7' : '#EFF6FF',
        color: isCompleted ? '#065F46' : '#1E40AF',
        border: `1px solid ${isCompleted ? '#86EFAC' : '#BFDBFE'}`,
      }}
    >
      <div className="flex items-center gap-3">
        {isCompleted ? (
          <span style={{ fontSize: 20 }}>✅</span>
        ) : (
          <span
            className="inline-block w-4 h-4 rounded-full border-2 border-t-transparent animate-spin"
            style={{ borderColor: 'currentColor', borderTopColor: 'transparent' }}
          />
        )}
        <div className="flex-1">
          <div className="text-sm font-bold">
            {isCompleted ? 'Dépôt réussi ✅' : 'Paiement en cours…'}
          </div>
          <div className="text-xs opacity-80">
            {isCompleted ? (
              "Ton solde est crédité."
            ) : (
              <>
                Attente de confirmation du provider
                {lastProviderStatus ? ` — ${lastProviderStatus}` : ''}
                {elapsed > 0 ? ` (${elapsed}s)` : ''}
              </>
            )}
          </div>
        </div>
      </div>
      <div className="text-[10px] mt-2 opacity-60">
        TX : <code>{WS.maskId(txId)}</code>
      </div>
    </div>
  );
}
