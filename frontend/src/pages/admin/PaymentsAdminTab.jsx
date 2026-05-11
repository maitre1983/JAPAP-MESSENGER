/**
 * PaymentsAdminTab — Gestion des Dépôts / Retraits / Paramètres de paiement
 * =========================================================================
 * Aligné avec la logique EAA :
 *   - Dépôts : automatiques (webhook Hubtel/NowPayments) + modération admin
 *   - Retraits : manuel (validation admin) OU auto (SDK) activables séparément
 *   - Méthodes : USDT TRC20 / BEP20 (dépôts + retraits) + Hubtel (dépôts)
 */
import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  Bank, ArrowDown, ArrowUp, Check, X, MagnifyingGlass, Clock, CheckCircle,
  Warning, Gear, CaretLeft, CaretRight,
} from '@phosphor-icons/react';
// iter235 — Mobile Money admin sections (Orange Money + Wave). Strictement additif.
import AdminOrangeMoneySection from '@/components/admin/AdminOrangeMoneySection';
import AdminWaveSection from '@/components/admin/AdminWaveSection';
// iter237i — Catalogue méthodes (toggles ON/OFF) + Analytics par méthode.
import PaymentMethodsCatalogAdmin from '@/components/admin/PaymentMethodsCatalogAdmin';
// iter238 — Paystack admin settings card (strictly additive).
import PaystackSettingsCard from '@/components/admin/PaystackSettingsCard';
// iter239b — Hubtel MoMo credentials admin card (strictly additive).
import HubtelSettingsCard from '@/components/admin/HubtelSettingsCard';
const API = process.env.REACT_APP_BACKEND_URL;

const getStatus_style = (t) => ({
  pending:    { bg: '#FEF3C7', fg: '#B45309', label: 'En attente' },
  processing: { bg: '#DBEAFE', fg: '#1E40AF', label: 'En cours' },
  completed:  { bg: '#D1FAE5', fg: '#065F46', label: t('payments_admin.complete') },
  approved:   { bg: '#D1FAE5', fg: '#065F46', label: t('payments_admin.approuve') },
  rejected:   { bg: '#FEE2E2', fg: '#991B1B', label: t('payments_admin.refuse') },
  // iter157 — auto-expire after TTL hours without provider confirmation.
  expired:    { bg: '#F3F4F6', fg: '#6B7280', label: t('payments_admin.expire') },
  failed:     { bg: '#FEE2E2', fg: '#991B1B', label: t('payments_admin.echec') },
});

const getStatus_filters = (t) => ([
  { id: '', label: 'Tous' },
  { id: 'pending', label: 'En attente' },
  { id: 'processing', label: 'En cours' },
  { id: 'completed', label: t('payments_admin.complete') },
  { id: 'expired', label: t('payments_admin.expire') },
  { id: 'rejected', label: t('payments_admin.refuse') },
]);

export default function PaymentsAdminTab() {
  const { t } = useTranslation();
  const [sub, setSub] = useState('deposits'); // deposits | withdrawals | settings

  return (
    <div className="space-y-5 jp-animate-fadeIn" data-testid="payments-admin-tab">
      <div className="flex gap-2 flex-wrap">
        <button className={`jp-btn jp-btn-sm ${sub === 'deposits' ? 'jp-btn-primary' : 'jp-btn-ghost'}`}
          onClick={() => setSub('deposits')} data-testid="payments-sub-deposits">
          <ArrowDown size={14} /> Dépôts
        </button>
        <button className={`jp-btn jp-btn-sm ${sub === 'withdrawals' ? 'jp-btn-primary' : 'jp-btn-ghost'}`}
          onClick={() => setSub('withdrawals')} data-testid="payments-sub-withdrawals">
          <ArrowUp size={14} /> Retraits
        </button>
        <button className={`jp-btn jp-btn-sm ${sub === 'settings' ? 'jp-btn-primary' : 'jp-btn-ghost'}`}
          onClick={() => setSub('settings')} data-testid="payments-sub-settings">
          <Gear size={14} /> Paramètres Paiement
        </button>
      </div>

      {sub === 'deposits' && <TxList kind="deposits" />}
      {sub === 'withdrawals' && <TxList kind="withdrawals" />}
      {sub === 'settings' && <PaymentSettings />}
    </div>
  );
}

function TxList({ kind }) {
  const { t } = useTranslation();
  const STATUS_STYLE = getStatus_style(t);
  const STATUS_FILTERS = getStatus_filters(t);
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [volume, setVolume] = useState('0');
  const [page, setPage] = useState(1);
  const [limit] = useState(20);
  const [status, setStatus] = useState('');
  const [q, setQ] = useState('');
  const [loading, setLoading] = useState(false);
  const [rejectFor, setRejectFor] = useState(null);
  const [reason, setReason] = useState('');
  // iter239c — Hubtel MoMo manual credit modal state (admin force-credit for
  // pending deposits when the Hubtel callback never arrived).
  const [momoCreditFor, setMomoCreditFor] = useState(null);
  const [momoExtTxId, setMomoExtTxId] = useState('');
  const [momoNote, setMomoNote] = useState('');
  const [momoSubmitting, setMomoSubmitting] = useState(false);

  const isDeposit = kind === 'deposits';

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page, limit });
      if (status) params.set('status', status);
      if (q.trim()) params.set('q', q.trim());
      const { data } = await axios.get(`${API}/api/admin/${kind}?${params}`, { withCredentials: true });
      setItems(data.items || []);
      setTotal(data.total || 0);
      setVolume(data.volume_total_usd || '0');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur chargement');
    } finally {
      setLoading(false);
    }
  }, [kind, status, q, page, limit]);

  useEffect(() => { load(); }, [load]);

  const approve = async (txId) => {
    // iter156 — kept only for withdrawals. Deposits are auto-credited via
    // provider webhooks — the button is never rendered for them.
    try {
      await axios.post(`${API}/api/admin/${kind}/${txId}/approve`, { reason: 'ok' }, { withCredentials: true });
      toast.success('Retrait approuvé');
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    }
  };

  // iter156 — "Re-verify with provider" for stuck deposits. Never credits
  // the wallet unilaterally — just asks Hubtel/NowPayments if the payment
  // has arrived. Matches the "admin observes, never decides" policy.
  const reverify = async (txId) => {
    try {
      const res = await axios.post(
        `${API}/api/admin/deposits/${txId}/reverify`, {},
        { withCredentials: true },
      );
      const d = res.data;
      if (d.credited) {
        toast.success(`Dépôt confirmé et crédité par ${d.provider}`);
      } else if (d.already) {
        toast.info('Déjà crédité');
      } else {
        toast.info(`Pas encore confirmé par ${d.provider || 'le provider'} — status: ${d.details?.provider_status || 'inconnu'}`);
      }
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur lors de la vérification provider');
    }
  };

  const submitReject = async () => {
    if (!rejectFor) return;
    try {
      const res = await axios.post(`${API}/api/admin/${kind}/${rejectFor}/reject`,
        { reason: reason || 'Non précisé' }, { withCredentials: true });
      const extra = res.data?.refunded ? ` — ${res.data.refunded} USDT remboursé` : '';
      toast.success((isDeposit ? t('payments_admin.depot_refuse') : t('payments_admin.retrait_refuse')) + extra);
      setRejectFor(null); setReason(''); load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    }
  };

  // iter239c — Open the manual-credit modal for a pending Hubtel MoMo deposit.
  // Pre-fills the external_tx_id field if already present in notes (e.g.
  // stored by the cron status check on a previous tick).
  const openMomoCredit = (tx) => {
    setMomoCreditFor(tx);
    const m = String(tx.notes || '').match(/external_tx_id=([\w-]+)/);
    setMomoExtTxId(m ? m[1] : '');
    setMomoNote('');
  };

  const submitMomoCredit = async () => {
    if (!momoCreditFor) return;
    if (!momoExtTxId.trim()) {
      toast.error('External Transaction ID requis (référence Hubtel/MTN).');
      return;
    }
    if (momoNote.trim().length < 10) {
      toast.error('Note admin requise (min. 10 caractères).');
      return;
    }
    setMomoSubmitting(true);
    try {
      await axios.post(
        `${API}/api/admin/hubtel/momo/credit-manual/${momoCreditFor.tx_id}`,
        { external_tx_id: momoExtTxId.trim(), note: momoNote.trim() },
        { withCredentials: true },
      );
      toast.success(`Dépôt crédité manuellement — ${momoCreditFor.amount} USD`);
      setMomoCreditFor(null);
      setMomoExtTxId('');
      setMomoNote('');
      load();
    } catch (e) {
      const d = e.response?.data?.detail;
      toast.error(d?.message || d || 'Erreur crédit manuel');
    } finally {
      setMomoSubmitting(false);
    }
  };

  const isHubtelMomo = (tx) => (
    tx.provider === 'hubtel_momo'
    || String(tx.notes || '').toLowerCase().includes('hubtel momo')
  );

  const totalPages = Math.max(1, Math.ceil(total / limit));

  return (
    <div className="jp-card-elevated p-5" data-testid={`${kind}-list`}>
      {isDeposit && (
        <div className="mb-4 p-3 rounded-xl border text-xs font-['Manrope'] leading-relaxed"
          style={{
            background: 'rgba(16,185,129,0.06)',
            borderColor: 'rgba(16,185,129,0.35)',
            color: '#065F46',
          }}
          data-testid="deposits-auto-banner">
          <strong>✓ Dépôts 100 % automatiques</strong> — aucune validation manuelle.
          Les dépôts sont crédités dès que Hubtel / NowPayments envoient leur
          webhook (vérif signature + montant + TX ID + idempotency). Si un
          dépôt reste bloqué, clique <strong>↻ Re-vérifier provider</strong> :
          JAPAP interroge à nouveau le provider et crédite uniquement s'il
          confirme le paiement. <em>{t('payments_admin.l_admin_observe_il_ne_decide_pas')}</em>
        </div>
      )}
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div>
          <h3 className="font-['Outfit'] text-lg font-bold flex items-center gap-2">
            {isDeposit ? <ArrowDown size={20} weight="duotone" style={{ color: '#10B981' }} /> : <ArrowUp size={20} weight="duotone" style={{ color: '#EF4444' }} />}
            {isDeposit ? t('payments_admin.depots') : 'Retraits'}
          </h3>
          <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
            {total} entrée{total > 1 ? 's' : ''} — Volume total : <strong>${parseFloat(volume).toLocaleString('fr-FR')}</strong>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <MagnifyingGlass size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: 'var(--jp-text-muted)' }} />
            <input value={q} onChange={e => { setPage(1); setQ(e.target.value); }}
              placeholder={t('payments_admin.rechercher_email_tx_id_adresse')}
              className="jp-input text-xs" style={{ paddingLeft: '30px', minWidth: '220px' }}
              data-testid={`${kind}-search`} />
          </div>
        </div>
      </div>
      <div className="flex gap-1 mb-3 flex-wrap">
        {STATUS_FILTERS.map(f => (
          <button key={f.id} onClick={() => { setPage(1); setStatus(f.id); }}
            data-testid={`${kind}-filter-${f.id || 'all'}`}
            className={`text-xs px-3 py-1 rounded-full transition-colors font-['Manrope']`}
            style={{
              background: status === f.id ? 'var(--jp-primary)' : 'var(--jp-surface-secondary)',
              color: status === f.id ? 'white' : 'var(--jp-text)',
              fontWeight: status === f.id ? 700 : 500,
            }}>
            {f.label}
          </button>
        ))}
      </div>
      <div className="rounded-xl overflow-hidden border" style={{ borderColor: 'var(--jp-border)' }}>
        <table className="jp-table w-full text-sm">
          <thead>
            <tr>
              <th className="text-left">Utilisateur</th>
              <th className="text-left">Méthode</th>
              <th className="text-right">Montant</th>
              {!isDeposit && <th className="text-right">Frais</th>}
              <th className="text-left">Réf. / Adresse</th>
              {!isDeposit && <th className="text-left">Mode</th>}
              <th className="text-left">Statut</th>
              <th className="text-left">Date</th>
              <th className="text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={isDeposit ? 7 : 9} className="text-center py-6 text-xs" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</td></tr>
            )}
            {!loading && items.length === 0 && (
              <tr><td colSpan={isDeposit ? 7 : 9} className="text-center py-6 text-xs" style={{ color: 'var(--jp-text-muted)' }}>Aucune entrée</td></tr>
            )}
            {!loading && items.map(tx => {
              const st = STATUS_STYLE[tx.status] || { bg: '#E5E7EB', fg: '#374151', label: tx.status };
              return (
                <tr key={tx.tx_id} data-testid={`${kind}-row-${tx.tx_id}`}>
                  <td>
                    <div className="text-sm font-medium">{tx.first_name} {tx.last_name}</div>
                    <div className="text-[10px] opacity-70">{tx.email}</div>
                  </td>
                  <td className="text-xs">{tx.method_label || '—'}</td>
                  <td className="text-right font-['Outfit'] font-bold">${parseFloat(tx.amount).toLocaleString('fr-FR', { minimumFractionDigits: 2 })}</td>
                  {!isDeposit && <td className="text-right text-xs opacity-70">{parseFloat(tx.fee).toFixed(2)}</td>}
                  <td className="text-xs font-mono max-w-[180px] truncate" title={tx.reference}>{tx.reference || '—'}</td>
                  {!isDeposit && (
                    <td>
                      <span className="text-[10px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider"
                        style={{
                          background: tx.processing_mode === 'auto' ? '#E0E7FF' : '#FEF3C7',
                          color: tx.processing_mode === 'auto' ? '#3730A3' : '#92400E',
                        }}>
                        {tx.processing_mode || 'manual'}
                      </span>
                    </td>
                  )}
                  <td>
                    <span className="text-xs px-2 py-1 rounded-full font-bold"
                      style={{ background: st.bg, color: st.fg }}>
                      {st.label}
                    </span>
                  </td>
                  <td className="text-[10px] opacity-70">{new Date(tx.created_at).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })}</td>
                  <td className="text-right">
                    {isDeposit && (tx.status === 'pending' || tx.status === 'processing') && (
                      <div className="flex gap-1 justify-end flex-wrap">
                        <button onClick={() => reverify(tx.tx_id)}
                          className="jp-btn jp-btn-sm text-xs"
                          style={{ background: 'rgba(15,5,107,0.1)', color: 'var(--jp-primary)' }}
                          data-testid={`deposit-reverify-${tx.tx_id}`}
                          title={t('payments_admin.re_interroger_le_provider_ne_credit')}>
                          ↻ Re-vérifier provider
                        </button>
                        {/* iter239c — Force-credit for Hubtel MoMo pending TX
                            where the callback never arrived but the admin has
                            confirmed payment on the Hubtel dashboard. */}
                        {isHubtelMomo(tx) && (
                          <button onClick={() => openMomoCredit(tx)}
                            className="jp-btn jp-btn-sm text-xs"
                            style={{ background: '#10B981', color: 'white' }}
                            data-testid={`hubtel-momo-credit-${tx.tx_id}`}
                            title="Créditer manuellement (callback non reçu)">
                            ✅ Créditer manuellement
                          </button>
                        )}
                      </div>
                    )}
                    {!isDeposit && (tx.status === 'pending' || tx.status === 'processing') && (
                      <div className="flex gap-1 justify-end">
                        <button onClick={() => approve(tx.tx_id)}
                          className="jp-btn jp-btn-sm text-xs"
                          style={{ background: '#10B981', color: 'white' }}
                          data-testid={`${kind}-approve-${tx.tx_id}`}>
                          <Check size={12} weight="bold" /> Valider
                        </button>
                        <button onClick={() => { setRejectFor(tx.tx_id); setReason(''); }}
                          className="jp-btn jp-btn-sm text-xs"
                          style={{ background: '#EF4444', color: 'white' }}
                          data-testid={`${kind}-reject-${tx.tx_id}`}>
                          <X size={12} weight="bold" /> Refuser
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between mt-3 text-xs">
        <span style={{ color: 'var(--jp-text-muted)' }}>Page {page} / {totalPages}</span>
        <div className="flex gap-1">
          <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}
            className="jp-btn jp-btn-ghost jp-btn-sm" style={{ opacity: page <= 1 ? 0.3 : 1 }}
            data-testid={`${kind}-prev`}>
            <CaretLeft size={12} /> Préc
          </button>
          <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}
            className="jp-btn jp-btn-ghost jp-btn-sm" style={{ opacity: page >= totalPages ? 0.3 : 1 }}
            data-testid={`${kind}-next`}>
            Suiv <CaretRight size={12} />
          </button>
        </div>
      </div>

      {rejectFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.6)' }}>
          <div className="jp-card-elevated max-w-md w-full p-5 jp-animate-scaleIn" data-testid={`${kind}-reject-modal`}>
            <h4 className="font-['Outfit'] text-lg font-bold mb-2">
              Refuser {isDeposit ? 'le dépôt' : 'le retrait'}
            </h4>
            <p className="text-xs mb-4" style={{ color: 'var(--jp-text-muted)' }}>
              {isDeposit ? t('payments_admin.l_utilisateur_ne_sera_pas_credite') : t('payments_admin.le_montant_sera_automatiquement_rem')}
            </p>
            <textarea rows={3} value={reason} onChange={e => setReason(e.target.value)}
              placeholder={t('payments_admin.motif_du_refus_affiche_a_l_utilisat')}
              className="jp-input text-sm w-full mb-3"
              data-testid={`${kind}-reject-reason`} />
            <div className="flex gap-2 justify-end">
              <button className="jp-btn jp-btn-ghost" onClick={() => setRejectFor(null)}>Annuler</button>
              <button className="jp-btn" style={{ background: '#EF4444', color: 'white' }}
                onClick={submitReject}
                data-testid={`${kind}-reject-submit`}>
                Confirmer le refus
              </button>
            </div>
          </div>
        </div>
      )}

      {/* iter239c — Hubtel MoMo manual credit modal. */}
      {momoCreditFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.6)' }}>
          <div className="jp-card-elevated max-w-md w-full p-5 jp-animate-scaleIn" data-testid="hubtel-momo-credit-modal">
            <h4 className="font-['Outfit'] text-lg font-bold mb-2">
              ✅ Créditer manuellement — Hubtel Mobile Money
            </h4>
            <div className="text-xs mb-3 space-y-1 p-3 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }}>
              <div><strong>Montant :</strong> ${parseFloat(momoCreditFor.amount).toFixed(2)} USD</div>
              <div><strong>Client Reference Japap :</strong> <code className="text-[10px]">{momoCreditFor.reference || '—'}</code></div>
              <div><strong>Utilisateur :</strong> {momoCreditFor.first_name} {momoCreditFor.last_name} ({momoCreditFor.email})</div>
              <div><strong>Créée le :</strong> {new Date(momoCreditFor.created_at).toLocaleString('fr-FR')}</div>
            </div>
            <div className="py-1.5">
              <label className="jp-label text-xs">External Transaction ID (référence Hubtel/MTN)</label>
              <input
                value={momoExtTxId}
                onChange={e => setMomoExtTxId(e.target.value)}
                placeholder="Ex: 81045690055"
                className="jp-input text-sm font-mono w-full"
                data-testid="hubtel-momo-credit-ext-tx-id"
              />
            </div>
            <div className="py-1.5">
              <label className="jp-label text-xs">Note admin (min. 10 caractères, obligatoire)</label>
              <textarea
                rows={3}
                value={momoNote}
                onChange={e => setMomoNote(e.target.value)}
                placeholder="Ex: Paiement confirmé sur dashboard Hubtel à 18h42, callback non reçu"
                className="jp-input text-sm w-full"
                data-testid="hubtel-momo-credit-note"
              />
              <div className="text-[10px] mt-0.5" style={{ color: momoNote.length < 10 ? '#EF4444' : 'var(--jp-text-muted)' }}>
                {momoNote.length} / 500 caractères {momoNote.length < 10 ? '— minimum 10' : '✓'}
              </div>
            </div>
            <div className="mt-3 p-3 rounded-xl text-[11px]" style={{ background: '#FEF3C7', color: '#92400E' }}>
              ⚠️ <strong>Action irréversible.</strong> Le wallet de l'utilisateur sera crédité immédiatement et l'opération sera tracée dans <code>audit_logs</code> avec votre identifiant admin.
            </div>
            <div className="flex gap-2 justify-end mt-3">
              <button className="jp-btn jp-btn-ghost" onClick={() => setMomoCreditFor(null)}
                disabled={momoSubmitting}
                data-testid="hubtel-momo-credit-cancel">
                Annuler
              </button>
              <button className="jp-btn"
                style={{
                  background: '#10B981', color: 'white',
                  opacity: (momoSubmitting || momoNote.trim().length < 10 || !momoExtTxId.trim()) ? 0.5 : 1,
                }}
                disabled={momoSubmitting || momoNote.trim().length < 10 || !momoExtTxId.trim()}
                onClick={submitMomoCredit}
                data-testid="hubtel-momo-credit-submit">
                {momoSubmitting ? 'Crédit en cours…' : '✅ Confirmer le crédit'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function PaymentSettings() {
  const { t } = useTranslation();
  const [s, setS] = useState({});
  const [configured, setConfigured] = useState({});
  const [loading, setLoading] = useState(false);

  const load = async () => {
    try {
      const { data } = await axios.get(`${API}/api/admin/settings`, { withCredentials: true });
      setS(data.settings || {});
      setConfigured(data.secret_configured || {});
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    setLoading(true);
    try {
      // Don't resend masked values (the backend also filters them out as a safety net).
      const coerced = Object.fromEntries(Object.entries(s).map(([k, v]) => {
        if (v === 'true' || v === 'false') return [k, v === 'true'];
        return [k, v];
      }));
      await axios.put(`${API}/api/admin/settings`, { settings: coerced }, { withCredentials: true });
      toast.success('Paramètres enregistrés'); load();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
    finally { setLoading(false); }
  };

  const Toggle = ({ k, label, hint }) => {
    const on = s[k] === 'true' || s[k] === true;
    return (
      <div className="flex items-start justify-between gap-3 py-2.5">
        <div className="flex-1">
          <div className="text-sm font-['Manrope'] font-semibold" style={{ color: 'var(--jp-text)' }}>{label}</div>
          {hint && <div className="text-[11px] mt-0.5" style={{ color: 'var(--jp-text-muted)' }}>{hint}</div>}
        </div>
        <button type="button" onClick={() => setS(prev => ({ ...prev, [k]: on ? 'false' : 'true' }))}
          className="relative w-12 h-6 rounded-full transition-colors shrink-0 mt-0.5"
          data-testid={`pay-toggle-${k}`}
          style={{ background: on ? 'var(--jp-success)' : '#D1D5DB' }}>
          <div className="absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform"
            style={{ transform: on ? 'translateX(26px)' : 'translateX(2px)' }} />
        </button>
      </div>
    );
  };

  const TextField = ({ k, label, placeholder }) => (
    <div className="py-2">
      <label className="jp-label">{label}</label>
      <input value={s[k] || ''} onChange={e => setS(prev => ({ ...prev, [k]: e.target.value }))}
        placeholder={placeholder} className="jp-input text-sm" data-testid={`pay-field-${k}`} />
    </div>
  );

  return (
    <div className="space-y-5" data-testid="payment-settings">
      <div className="jp-card-elevated p-5">
        <h3 className="font-['Outfit'] text-lg font-bold mb-1 flex items-center gap-2">
          <ArrowDown size={18} weight="duotone" style={{ color: '#10B981' }} /> Dépôts
        </h3>
        <p className="text-xs mb-3" style={{ color: 'var(--jp-text-muted)' }}>
          Les dépôts sont 100 % automatiques dès que le webhook provider est reçu (Hubtel, NowPayments). Les dépôts pending non confirmés après {' '}
          <strong>24 heures</strong> passent automatiquement en <code>expired</code> (aucune intervention admin possible).
        </p>
        <div className="divide-y" style={{ borderColor: 'var(--jp-border)' }}>
          <Toggle k="deposits_enabled" label="Dépôts activés (master switch)"
            hint="Si désactivé, aucun utilisateur ne peut initier de dépôt." />
          <Toggle k="deposit_usdt_trc20_enabled" label="USDT TRC20 (TRON)" hint="Dépôt crypto sur réseau TRON — disponible globalement." />
          <Toggle k="deposit_usdt_bep20_enabled" label="USDT BEP20 (BSC)" hint="Dépôt crypto sur réseau Binance Smart Chain." />
          <Toggle k="deposit_hubtel_card_enabled" label="Carte bancaire (Hubtel)" hint="Paiement carte via Hubtel Checkout." />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
          <TextField k="deposit_min_amount_usd" label="Dépôt minimum (USD)" placeholder="1" />
          <TextField k="deposit_disabled_message" label="Message si désactivés"
            placeholder={t('payments_admin.les_depots_sont_temporairement_susp')} />
          <TextField k="deposit_address_usdt_trc20" label="Adresse officielle TRC20 (T…)" />
          <TextField k="deposit_address_usdt_bep20" label="Adresse officielle BEP20 (0x…)" />
        </div>
      </div>

      <div className="jp-card-elevated p-5">
        <h3 className="font-['Outfit'] text-lg font-bold mb-1 flex items-center gap-2">
          <ArrowUp size={18} weight="duotone" style={{ color: '#EF4444' }} /> Retraits
        </h3>
        <p className="text-xs mb-3" style={{ color: 'var(--jp-text-muted)' }}>
          Choisissez <strong>manuel</strong>, <strong>auto</strong>, ou <strong>les deux</strong>. En auto, le retrait passe en "processing" immédiatement (traité par le SDK). En manuel, il reste "pending" jusqu'à la validation admin.
        </p>
        <div className="divide-y" style={{ borderColor: 'var(--jp-border)' }}>
          <Toggle k="withdraw_enabled" label="Retraits activés (master switch)" />
          <Toggle k="manual_withdraw_enabled" label="Retraits manuels activés"
            hint="L'admin doit approuver chaque retrait. Statut initial : pending." />
          <Toggle k="auto_withdraw_enabled" label="Retraits automatiques activés"
            hint="Le retrait est envoyé via SDK (NowPayments à venir). Statut initial : processing." />
          <Toggle k="kyc_required_for_withdraw" label="KYC requis pour retirer" />
          <Toggle k="withdraw_usdt_trc20_enabled" label="Réseau USDT TRC20 (TRON)" />
          <Toggle k="withdraw_usdt_bep20_enabled" label="Réseau USDT BEP20 (BSC)" />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-3">
          <TextField k="withdraw_min_amount_usd" label="Retrait minimum (USD)" placeholder="5" />
          <TextField k="withdraw_disabled_message" label="Message si retraits désactivés" />
          <TextField k="auto_withdraw_unavailable_message" label="Message si auto indisponible" />
        </div>
        <div className="mt-4 p-3 rounded-xl text-xs" style={{ background: 'var(--jp-primary-subtle)', color: 'var(--jp-text)' }}>
          💡 Les frais de retrait par plan Pro se configurent dans l'onglet <strong>{t('payments_admin.parametres')}</strong> ci-contre (section "Retraits Wallet — Frais & Méthodes").
        </div>
      </div>

      {/* ========== API Keys — Hubtel + NowPayments ========== */}
      <div className="jp-card-elevated p-5" data-testid="payment-api-keys">
        <h3 className="font-['Outfit'] text-lg font-bold mb-1 flex items-center gap-2">
          <Gear size={18} weight="duotone" style={{ color: '#5B21B6' }} />
          Clés API des passerelles
        </h3>
        <p className="text-xs mb-4" style={{ color: 'var(--jp-text-muted)' }}>
          Collez vos clés Hubtel et NowPayments. Elles sont stockées de façon sécurisée et masquées à l'affichage (••••••••1234). Tant qu'elles sont vides, les webhooks acceptent tout payload (mode DEV) ; dès qu'elles sont renseignées, la signature HMAC est vérifiée automatiquement.
        </p>

        <div className="mb-5">
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            <span className="text-sm font-['Manrope'] font-bold">Hubtel</span>
            <StatusChip ok={configured.hubtel_client_id && configured.hubtel_client_secret && configured.hubtel_merchant_account} />
            <select className="jp-input text-xs ml-auto" style={{ minWidth: '130px' }}
              value={s.hubtel_environment || 'sandbox'}
              onChange={e => setS(prev => ({ ...prev, hubtel_environment: e.target.value }))}
              data-testid="select-hubtel_environment">
              <option value="sandbox">{t('payments_admin.sandbox_test')}</option>
              <option value="production">{t('payments_admin.production')}</option>
            </select>
            <TestConnectionButton endpoint="/api/wallet/hubtel/test-connection" label="Tester" testid="test-hubtel" />
          </div>
          <SecretField k="hubtel_client_id" label="Client ID" placeholder={t('payments_admin.entrez_votre_client_id_hubtel')}
            value={s.hubtel_client_id} setS={setS} configured={configured.hubtel_client_id} />
          <SecretField k="hubtel_client_secret" label="Client Secret" placeholder={t('payments_admin.entrez_votre_client_secret')}
            value={s.hubtel_client_secret} setS={setS} configured={configured.hubtel_client_secret} type="password" />
          <SecretField k="hubtel_merchant_account" label="Merchant Account Number"
            placeholder={t('payments_admin.ex_0241234567')} value={s.hubtel_merchant_account}
            setS={setS} configured={configured.hubtel_merchant_account} />
          <SecretField k="hubtel_webhook_secret" label="Webhook / IPN Secret"
            placeholder={t('payments_admin.secret_partage_pour_signer_les_call')} value={s.hubtel_webhook_secret}
            setS={setS} configured={configured.hubtel_webhook_secret} type="password"
            hint="Utilisé pour vérifier X-Auth-Signature (HMAC-SHA256) sur POST /api/wallet/hubtel/webhook." />

          {/* iter195 — CEO : URLs de rappel Hubtel (clone EAA). Éditables par l'admin
              sans redéploiement backend. Le service d'initiation injecte ces URLs
              telles quelles dans le payload POST /items/initiate. */}
          <div className="py-2 mt-3 p-3 rounded-xl" style={{ background: '#F0F9FF', border: '1px solid #BAE6FD' }}>
            <div className="text-xs font-bold mb-2" style={{ color: '#0C4A6E' }}>
              🔔 URLs de rappel Hubtel (obligatoires pour le crédit wallet)
            </div>
            <div className="py-1">
              <label className="jp-label">Callback URL (backend webhook — IPN)</label>
              <input
                value={s.hubtel_callback_url_override || ''}
                onChange={e => setS(prev => ({ ...prev, hubtel_callback_url_override: e.target.value }))}
                placeholder={t('payments_admin.https_japapmessenger_com_api_wallet')}
                className="jp-input text-sm font-mono"
                data-testid="hubtel-callback-url"
              />
              <div className="text-[10px] mt-1" style={{ color: '#0C4A6E' }}>
                Hubtel POST ici après paiement. Doit être HTTPS + public. Crédite le wallet.
              </div>
            </div>
            <div className="py-1">
              <label className="jp-label">Return URL (page frontend post-paiement)</label>
              <input
                value={s.hubtel_return_url_override || ''}
                onChange={e => setS(prev => ({ ...prev, hubtel_return_url_override: e.target.value }))}
                placeholder={t('payments_admin.https_japapmessenger_com_wallet_dep')}
                className="jp-input text-sm font-mono"
                data-testid="hubtel-return-url"
              />
              <div className="text-[10px] mt-1" style={{ color: '#0C4A6E' }}>
                Page où Hubtel redirige l'utilisateur après Pay/Cancel. `?tx=…` ajouté automatiquement.
              </div>
            </div>
          </div>
          <div className="text-[10px] mt-1" style={{ color: 'var(--jp-text-muted)' }}>
            Dashboard : <a href="https://unity.hubtel.com" target="_blank" rel="noreferrer" className="underline">unity.hubtel.com</a> → API Keys
          </div>
        </div>

        <div className="h-px my-4" style={{ background: 'var(--jp-border)' }} />

        <div>
          <div className="flex items-center gap-2 mb-2">
            <span className="text-sm font-['Manrope'] font-bold">NowPayments</span>
            <StatusChip ok={configured.nowpayments_api_key && configured.nowpayments_ipn_secret} />
            <select className="jp-input text-xs ml-auto" style={{ minWidth: '130px' }}
              value={s.nowpayments_environment || 'sandbox'}
              onChange={e => setS(prev => ({ ...prev, nowpayments_environment: e.target.value }))}
              data-testid="select-nowpayments_environment">
              <option value="sandbox">{t('payments_admin.sandbox_test')}</option>
              <option value="production">{t('payments_admin.production')}</option>
            </select>
          </div>
          <SecretField k="nowpayments_api_key" label="API Key"
            placeholder={t('payments_admin.entrez_votre_api_key_nowpayments')}
            value={s.nowpayments_api_key} setS={setS}
            configured={configured.nowpayments_api_key} type="password" />
          <SecretField k="nowpayments_ipn_secret" label="IPN Secret"
            placeholder={t('payments_admin.secret_ipn_hmac_sha512')}
            value={s.nowpayments_ipn_secret} setS={setS}
            configured={configured.nowpayments_ipn_secret} type="password"
            hint="Utilisé pour vérifier x-nowpayments-sig sur POST /api/wallet/nowpayments/webhook." />
          <div className="text-[10px] mt-1" style={{ color: 'var(--jp-text-muted)' }}>
            Dashboard : <a href="https://nowpayments.io" target="_blank" rel="noreferrer" className="underline">nowpayments.io</a> → Settings → API Keys / IPN
          </div>
        </div>

        <div className="mt-4 p-3 rounded-xl text-xs" style={{ background: '#FEF3C7', color: '#92400E' }}>
          🔐 <strong>{t('payments_admin.securite')}</strong> : ces clés donnent accès à vos comptes de paiement. Ne les partagez pas. Les valeurs sont masquées dès qu'elles sont enregistrées ; laissez le champ intact pour conserver la valeur actuelle, ou écrivez une nouvelle valeur pour l'écraser.
        </div>
      </div>

      {/* iter235 — Mobile Money sections (additive). */}
      <div data-testid="admin-mobile-money-block">
        <h3 className="font-['Outfit'] text-lg font-bold mb-2 mt-4 flex items-center gap-2">
          💸 Mobile Money
        </h3>
        <AdminOrangeMoneySection />
        <AdminWaveSection />
      </div>

      {/* iter237i — Catalogue & Analytics des méthodes de paiement. */}
      <PaymentMethodsCatalogAdmin />

      {/* iter238 — Paystack admin settings (strictly additive). */}
      <PaystackSettingsCard />

      {/* iter239b — Hubtel MoMo credentials (strictly additive). */}
      <HubtelSettingsCard />

      <button disabled={loading} onClick={save} className="jp-btn jp-btn-primary" data-testid="pay-save-settings">
        {loading ? 'Enregistrement…' : t('payments_admin.enregistrer_les_parametres_de_paiem')}
      </button>
    </div>
  );
}

function StatusChip({ ok }) {
  return (
    <span className="text-[10px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider"
      style={{
        background: ok ? '#D1FAE5' : '#FEE2E2',
        color: ok ? '#065F46' : '#991B1B',
      }}>
      {ok ? '✓ Configuré' : '⚠ Non configuré'}
    </span>
  );
}

function TestConnectionButton({ endpoint, label, testid }) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null); // { ok, status_code, reason }
  const run = async () => {
    setLoading(true); setResult(null);
    try {
      const { data } = await axios.get(`${API}${endpoint}`, { withCredentials: true, timeout: 12000 });
      setResult(data);
      if (data.ok) toast.success('Connexion OK');
      else toast.error(data.reason || `Échec (${data.status_code || '?'})`);
    } catch (e) {
      const detail = e.response?.data?.detail || e.message;
      setResult({ ok: false, reason: detail });
      toast.error(detail);
    } finally { setLoading(false); }
  };
  return (
    <button type="button" onClick={run} disabled={loading}
      data-testid={testid}
      className="jp-btn jp-btn-sm text-xs shrink-0"
      style={{
        background: result?.ok ? '#10B981' : result && !result.ok ? '#EF4444' : 'var(--jp-surface-secondary)',
        color: result ? 'white' : 'var(--jp-text)',
      }}>
      {loading ? '...' : result?.ok ? '✓ OK' : result ? '✗ Erreur' : label}
    </button>
  );
}

function SecretField({ k, label, placeholder, value, setS, configured, type, hint }) {  const [localValue, setLocalValue] = useState(value || '');
  const [editing, setEditing] = useState(false);

  useEffect(() => { setLocalValue(value || ''); }, [value]);

  const displayValue = (configured && !editing) ? (value || '') : localValue;
  const isMasked = configured && !editing && typeof value === 'string' && value.startsWith('••');

  const handleEdit = () => { setEditing(true); setLocalValue(''); setS(prev => ({ ...prev, [k]: '' })); };
  const handleChange = (v) => { setLocalValue(v); setS(prev => ({ ...prev, [k]: v })); };

  return (
    <div className="py-1.5" data-testid={`api-field-${k}`}>
      <label className="jp-label text-xs">{label}</label>
      <div className="flex gap-1.5">
        <input
          type={type === 'password' && !editing ? 'text' : (type || 'text')}
          className="jp-input text-xs font-mono flex-1"
          value={displayValue}
          placeholder={placeholder}
          readOnly={isMasked}
          onChange={e => handleChange(e.target.value)}
          data-testid={`api-input-${k}`} />
        {isMasked && (
          <button type="button" onClick={handleEdit}
            className="jp-btn jp-btn-ghost jp-btn-sm text-xs shrink-0"
            data-testid={`api-edit-${k}`}>
            Modifier
          </button>
        )}
      </div>
      {hint && <div className="text-[10px] mt-0.5" style={{ color: 'var(--jp-text-muted)' }}>{hint}</div>}
    </div>
  );
}
