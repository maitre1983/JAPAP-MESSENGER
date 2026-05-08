/**
 * iter117 — Payment Health Admin cockpit
 * =====================================
 * Real-time view of:
 *   • verify_payment_status latency per provider (Hubtel / NowPayments)
 *   • IPN error counts (last 24h)
 *   • Top 5 IPN/QR error groups
 *   • Transactions stuck in pending_verification
 *   • Retry queue (due / scheduled / abandoned)
 *
 * Daily digest e-mail goes to OPS_INBOX_EMAIL (liportalmerchand@gmail.com)
 * — same address that already receives deposit/withdrawal notifications.
 */
import { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  Bank, Lightning, Warning, ArrowsClockwise, EnvelopeSimple, Clock,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function PaymentHealthAdminTab() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [hours, setHours] = useState(24);
  const [sendingDigest, setSendingDigest] = useState(false);
  const [retryingId, setRetryingId] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data: r } = await axios.get(
        `${API}/api/wallet/admin/payment-health?hours=${hours}`,
        { withCredentials: true });
      setData(r);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur chargement');
    } finally {
      setLoading(false);
    }
  }, [hours]);

  useEffect(() => { load(); }, [load]);

  const sendDigestNow = async () => {
    setSendingDigest(true);
    try {
      const { data: r } = await axios.post(
        `${API}/api/wallet/admin/payment-health/digest`, {},
        { withCredentials: true });
      toast.success(r.sent ? `Digest envoyé à ${r.sent_to}` : 'Echec envoi (voir logs)');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur envoi digest');
    } finally {
      setSendingDigest(false);
    }
  };

  const forceRetry = async (provider, tx_id) => {
    setRetryingId(`${provider}:${tx_id}`);
    try {
      const { data: r } = await axios.post(
        `${API}/api/wallet/admin/payment-health/retry/${provider}/${tx_id}`,
        {}, { withCredentials: true });
      if (r.ok) toast.success(`✅ ${tx_id} crédité (${r.reason})`);
      else toast.warning(`⚠️ ${tx_id}: ${r.reason}`);
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur retry');
    } finally {
      setRetryingId('');
    }
  };

  if (loading && !data) {
    return (
      <div className="jp-card p-6 text-center text-sm"
           style={{ color: 'var(--jp-text-muted)' }}
           data-testid="payment-health-loading">
        Chargement du cockpit Payment Health…
      </div>
    );
  }
  if (!data) return null;

  return (
    <div className="space-y-4" data-testid="payment-health-tab">
      {/* Header + actions */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="font-['Outfit'] text-xl font-bold flex items-center gap-2">
          <Bank size={22} weight="duotone" />
          Payment Health Cockpit
        </h2>
        <div className="flex items-center gap-2">
          <select value={hours} onChange={(e) => setHours(Number(e.target.value))}
                  data-testid="payment-health-window"
                  className="jp-input text-xs">
            <option value={1}>1h</option>
            <option value={6}>6h</option>
            <option value={24}>24h</option>
            <option value={72}>3 jours</option>
            <option value={168}>7 jours</option>
          </select>
          <button onClick={load} className="jp-btn text-xs flex items-center gap-1"
                  data-testid="payment-health-refresh">
            <ArrowsClockwise size={12} /> Actualiser
          </button>
          <button onClick={sendDigestNow} disabled={sendingDigest}
                  className="jp-btn jp-btn-primary text-xs flex items-center gap-1"
                  data-testid="payment-health-send-digest">
            <EnvelopeSimple size={12} weight="fill" />
            {sendingDigest ? 'Envoi…' : 'Envoyer digest maintenant'}
          </button>
        </div>
      </div>

      <p className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
        Fenêtre {data.window_hours}h glissantes · Digest quotidien automatique
        à 08h UTC vers <code>liportalmerchand@gmail.com</code> (même boîte que
        les notifications dépôt/retrait).
      </p>

      {/* Provider KPI cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {Object.entries(data.providers).map(([prov, p]) => (
          <ProviderCard key={prov} provider={prov} p={p} />
        ))}
      </div>

      {/* Retry queue */}
      <div className="jp-card p-4" data-testid="payment-health-retry-queue">
        <h3 className="font-['Outfit'] text-base font-bold mb-2 flex items-center gap-2">
          <ArrowsClockwise size={16} weight="duotone" />
          Retry queue
        </h3>
        <div className="grid grid-cols-3 gap-2">
          <Stat label="À retenter maintenant" value={data.retry_queue.due_now}
                color={data.retry_queue.due_now > 0 ? '#FFD700' : undefined}
                testid="rq-due" />
          <Stat label="Programmées" value={data.retry_queue.scheduled}
                testid="rq-scheduled" />
          <Stat label="Abandonnées" value={data.retry_queue.abandoned}
                color={data.retry_queue.abandoned > 0 ? '#E01C2E' : undefined}
                testid="rq-abandoned" />
        </div>
        <p className="text-[10px] mt-2" style={{ color: 'var(--jp-text-muted)' }}>
          Backoff exponentiel : 2 → 4 → 8 → 16 → 32 → 64 → 128 → 256 min
          (max 8 tentatives sur ~4h cumulées). Worker tourne toutes les 2 min.
        </p>
      </div>

      {/* Top errors */}
      {data.top_errors?.length > 0 && (
        <div className="jp-card p-4" data-testid="payment-health-errors">
          <h3 className="font-['Outfit'] text-base font-bold mb-2 flex items-center gap-2">
            <Warning size={16} weight="duotone" />
            Top erreurs IPN / QR
          </h3>
          <div className="space-y-2">
            {data.top_errors.map((e) => (
              <div key={e.module + (e.last_seen || '')}
                   className="flex items-start gap-2 p-2 rounded"
                   style={{ background: 'var(--jp-surface-secondary)' }}>
                <span className={`text-[9px] uppercase tracking-widest font-bold px-1.5 py-0.5 rounded`}
                      style={{
                        background: e.severity === 'critical' ? '#E01C2E' : e.severity === 'high' ? '#FFD700' : '#10B981',
                        color: e.severity === 'critical' ? 'white' : '#0F056B',
                      }}>
                  {e.severity}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-bold">{e.module}</div>
                  <div className="text-[11px]"
                       style={{ color: 'var(--jp-text-muted)' }}>
                    {e.message_sample}
                  </div>
                  <div className="text-[10px] mt-0.5"
                       style={{ color: 'var(--jp-text-muted)' }}>
                    {e.occurrences}× · {e.affected_users} users · {e.status}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Pending verification list */}
      <div className="jp-card p-4" data-testid="payment-health-pending">
        <h3 className="font-['Outfit'] text-base font-bold mb-2 flex items-center gap-2">
          <Clock size={16} weight="duotone" />
          Transactions en attente de vérification
          <span className="text-[10px] ml-auto opacity-60">
            {data.pending_verification.length} bloquées
          </span>
        </h3>
        {data.pending_verification.length === 0 ? (
          <div className="text-xs py-4 text-center"
               style={{ color: 'var(--jp-text-muted)' }}>
            ✅ Aucune transaction bloquée
          </div>
        ) : (
          <div className="space-y-2">
            {data.pending_verification.map((t) => {
              const prov = (t.notes || '').toLowerCase().includes('hubtel') ? 'hubtel'
                         : (t.notes || '').toLowerCase().includes('nowpayments') ? 'nowpayments'
                         : null;
              return (
                <div key={t.tx_id}
                     className="flex items-center gap-2 p-2 rounded"
                     style={{ background: 'var(--jp-surface-secondary)' }}
                     data-testid={`pending-tx-${t.tx_id}`}>
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-bold font-mono">{t.tx_id}</div>
                    <div className="text-[11px]"
                         style={{ color: 'var(--jp-text-muted)' }}>
                      {t.amount_usd} USD · {t.notes}
                    </div>
                    <div className="text-[10px]"
                         style={{ color: 'var(--jp-text-muted)' }}>
                      {t.admin_notes}
                    </div>
                  </div>
                  {prov && (
                    <button onClick={() => forceRetry(prov, t.tx_id)}
                            disabled={retryingId === `${prov}:${t.tx_id}`}
                            className="jp-btn text-[10px]"
                            data-testid={`retry-tx-${t.tx_id}`}>
                      {retryingId === `${prov}:${t.tx_id}` ? '…' : 'Retry'}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <p className="text-[10px] text-right"
         style={{ color: 'var(--jp-text-muted)' }}>
        Généré : {new Date(data.generated_at).toLocaleString('fr-FR')}
      </p>
    </div>
  );
}

function ProviderCard({ provider, p }) {
  const okColor = p.verify_ok_rate >= 95 ? '#10B981'
                : p.verify_ok_rate >= 80 ? '#FFD700' : '#E01C2E';
  return (
    <div className="jp-card p-4" data-testid={`provider-${provider}`}>
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-['Outfit'] text-base font-bold uppercase tracking-wide">
          {provider}
        </h3>
        <span className="text-[10px] uppercase tracking-widest font-bold px-1.5 py-0.5 rounded-full"
              style={{ background: `${okColor}22`, color: okColor }}>
          {p.verify_ok_rate}% OK
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Stat label="Verify calls" value={p.verify_calls} testid={`${provider}-calls`} />
        <Stat label="Vérifs OK" value={p.verify_ok}
              color={okColor} testid={`${provider}-ok`} />
        <Stat label="Confirmés payés" value={p.paid_count}
              testid={`${provider}-paid`} />
        <Stat label="Erreurs IPN"
              value={p.ipn_errors}
              color={p.ipn_errors > 0 ? '#E01C2E' : undefined}
              testid={`${provider}-ipn-err`} />
        <Stat label="Latence p50" value={`${p.latency_p50_ms}ms`}
              testid={`${provider}-p50`} />
        <Stat label="Latence p95" value={`${p.latency_p95_ms}ms`}
              color={p.latency_p95_ms > 5000 ? '#E01C2E' : p.latency_p95_ms > 2000 ? '#FFD700' : undefined}
              testid={`${provider}-p95`} />
      </div>
    </div>
  );
}

function Stat({ label, value, color, testid }) {
  return (
    <div className="p-2 rounded"
         style={{ background: 'var(--jp-surface-secondary)' }}
         data-testid={testid}>
      <div className="text-[9px] uppercase tracking-widest font-bold"
           style={{ color: 'var(--jp-text-muted)' }}>{label}</div>
      <div className="font-['Outfit'] text-base font-bold mt-0.5"
           style={{ color: color || 'var(--jp-text)' }}>
        {value ?? '—'}
      </div>
    </div>
  );
}
