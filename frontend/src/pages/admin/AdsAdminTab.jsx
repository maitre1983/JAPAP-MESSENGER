/**
 * AdsAdminTab — Modération des campagnes publicitaires JAPAP
 * ==========================================================
 * L'admin voit les campagnes pending/approved/running/ended/rejected,
 * peut approuver ou refuser avec un motif. Stats globales en tête.
 */
import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Megaphone, Check, X, Eye, TrendUp, Clock } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
const API = process.env.REACT_APP_BACKEND_URL;
const STATUS_FILTERS = ['pending', 'approved', 'running', 'ended', 'rejected'];

const getStatus_style = (t) => ({
  pending:  { bg: '#FEF3C7', fg: '#B45309', label: 'En attente' },
  approved: { bg: '#D1FAE5', fg: '#065F46', label: t('ads_admin.approuve') },
  running:  { bg: '#DBEAFE', fg: '#1E40AF', label: 'En cours' },
  ended:    { bg: '#E5E7EB', fg: '#374151', label: t('ads_admin.termine') },
  rejected: { bg: '#FEE2E2', fg: '#991B1B', label: t('ads_admin.refuse') },
});

export default function AdsAdminTab() {
  const { t } = useTranslation();
  const STATUS_STYLE = getStatus_style(t);
  const [status, setStatus] = useState('pending');
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [reasonFor, setReasonFor] = useState(null); // campaign_id for reject modal
  const [reason, setReason] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(
        `${API}/api/ads/admin/campaigns?status=${status}&limit=50`,
        { withCredentials: true }
      );
      setItems(data.items || []); setTotal(data.total || 0);
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
    finally { setLoading(false); }
  }, [status]);

  useEffect(() => { load(); }, [load]);

  const approve = async (id) => {
    try {
      await axios.post(`${API}/api/ads/admin/campaigns/${id}/approve`,
        { approve: true, reason: t('ads_admin.campagne_approuvee') }, { withCredentials: true });
      toast.success('Campagne approuvée'); load();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  const reject = async (id) => {
    if (!reason.trim()) { toast.error('Motif requis'); return; }
    try {
      await axios.post(`${API}/api/ads/admin/campaigns/${id}/approve`,
        { approve: false, reason: reason.trim() }, { withCredentials: true });
      toast.success('Campagne refusée'); setReasonFor(null); setReason(''); load();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  return (
    <div className="space-y-5 jp-animate-fadeIn" data-testid="ads-admin-tab">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-['Outfit'] text-lg font-bold inline-flex items-center gap-2" style={{ color: 'var(--jp-text)' }}>
            <Megaphone size={18} weight="fill" /> Modération Publicités
          </h2>
          <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
            Approuvez ou refusez les campagnes. Les budgets sont déjà débités (escrow) ; les refus créditent une note à rembourser via Settings.
          </p>
        </div>
        <div className="text-right">
          <div className="text-[10px] uppercase tracking-widest font-bold" style={{ color: 'var(--jp-text-muted)' }}>Total</div>
          <div className="font-['Outfit'] text-2xl font-black" data-testid="ads-total">{total}</div>
        </div>
      </div>

      {/* Status filter */}
      <div className="flex gap-2 flex-wrap" data-testid="ads-status-filter">
        {STATUS_FILTERS.map(s => (
          <button key={s} onClick={() => setStatus(s)} data-testid={`ads-filter-${s}`}
            className={`px-3 py-1.5 rounded-full text-xs font-bold inline-flex items-center gap-1.5 ${status === s ? 'jp-btn-primary' : 'jp-btn-ghost'}`}>
            <span className="w-2 h-2 rounded-full" style={{ background: STATUS_STYLE[s].fg }} />
            {STATUS_STYLE[s].label}
          </button>
        ))}
      </div>

      {/* Campaigns list */}
      <div className="space-y-3" data-testid="ads-admin-list">
        {loading && <div className="jp-card p-6 text-center text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>}
        {!loading && items.length === 0 && (
          <div className="jp-card p-8 text-center text-sm" style={{ color: 'var(--jp-text-muted)' }}>
            Aucune campagne en statut « {STATUS_STYLE[status].label} ».
          </div>
        )}
        {items.map(c => (
          <div key={c.campaign_id} className="jp-card-elevated p-4" data-testid={`ads-row-${c.campaign_id}`}>
            <div className="flex flex-col sm:flex-row sm:items-center gap-3">
              {c.image_url ? (
                <img src={c.image_url} alt={c.title || ''} className="w-20 h-20 rounded-xl object-cover" />
              ) : (
                <div className="w-20 h-20 rounded-xl flex items-center justify-center" style={{ background: 'var(--jp-surface-2)' }}>
                  <Megaphone size={28} style={{ color: 'var(--jp-text-muted)' }} />
                </div>
              )}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <strong className="font-['Outfit'] truncate">{c.title || '(sans titre)'}</strong>
                  <span className="jp-badge" style={{ background: STATUS_STYLE[c.status]?.bg, color: STATUS_STYLE[c.status]?.fg }}>
                    {STATUS_STYLE[c.status]?.label || c.status}
                  </span>
                  <span className="jp-badge jp-badge-neutral text-[10px]">{c.target_type}</span>
                  {c.country_code && <span className="jp-badge jp-badge-neutral text-[10px]">🌍 {c.country_code}</span>}
                </div>
                <div className="text-xs mt-1" style={{ color: 'var(--jp-text-muted)' }}>
                  par <strong>@{c.username}</strong> ({c.email}) ·
                  target_id: <code className="text-[10px]">{c.target_id || '—'}</code>
                </div>
                <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-xs font-['Manrope']">
                  <span><strong>{t('ads_admin.budget')}</strong> ${c.budget_usd}</span>
                  <span style={{ color: '#EC4899' }}><strong>{t('ads_admin.depense')}</strong> ${c.spent_usd}</span>
                  <span><strong>{t('ads_admin.cpm')}</strong> ${c.cpm_usd}</span>
                  <span className="inline-flex items-center gap-1"><Eye size={10} /> {c.impressions}</span>
                  <span className="inline-flex items-center gap-1"><TrendUp size={10} /> {c.clicks}</span>
                </div>
                {c.cta_url && (
                  <a href={c.cta_url} target="_blank" rel="noreferrer" className="text-xs underline mt-1 inline-block"
                    style={{ color: 'var(--jp-primary)' }}>
                    {c.cta_url}
                  </a>
                )}
              </div>
              {c.status === 'pending' && (
                <div className="flex gap-2 shrink-0">
                  <button onClick={() => approve(c.campaign_id)} className="jp-btn jp-btn-sm"
                    style={{ background: '#10B981', color: 'white' }} data-testid={`approve-${c.campaign_id}`}>
                    <Check size={14} weight="bold" /> Approuver
                  </button>
                  <button onClick={() => { setReasonFor(c.campaign_id); setReason(''); }}
                    className="jp-btn jp-btn-sm" style={{ background: '#EF4444', color: 'white' }}
                    data-testid={`reject-${c.campaign_id}`}>
                    <X size={14} weight="bold" /> Refuser
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Reject modal */}
      {reasonFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: 'rgba(0,0,0,0.6)' }} data-testid="reject-modal">
          <div className="jp-card-elevated max-w-md w-full p-6">
            <h3 className="font-['Outfit'] text-lg font-bold mb-2">Refuser cette campagne</h3>
            <p className="text-xs mb-3" style={{ color: 'var(--jp-text-muted)' }}>
              Le motif sera notifié à l'annonceur. Soyez précis (contenu non conforme, image inappropriée, etc.).
            </p>
            <textarea value={reason} onChange={e => setReason(e.target.value)} className="jp-input text-sm resize-none"
              style={{ height: '100px' }} placeholder="Motif du refus" data-testid="reject-reason" />
            <div className="flex gap-2 mt-3">
              <button onClick={() => { setReasonFor(null); setReason(''); }} className="jp-btn jp-btn-ghost flex-1">
                Annuler
              </button>
              <button onClick={() => reject(reasonFor)} className="jp-btn flex-1"
                style={{ background: '#EF4444', color: 'white' }} data-testid="confirm-reject">
                <X size={14} weight="bold" /> Confirmer le refus
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
