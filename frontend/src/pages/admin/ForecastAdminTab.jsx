/**
 * iter241a — Admin · Prédictions (Forecast Markets).
 *
 * 3 sous-onglets : Paramètres · Marchés · Métriques.
 * IA (suggest / detect_result / abuse_check) = iter241b — stubs cliquables masqués.
 *
 * 100% additif. Uses existing jp-card / jp-btn helpers + i18next, zero hardcode.
 */
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL;
const CATEGORIES = ['politics', 'economy', 'crypto', 'culture', 'world', 'sport'];
const STATUSES = ['draft', 'active', 'closed', 'resolved', 'cancelled'];

export default function ForecastAdminTab() {
  const { t } = useTranslation();
  const [view, setView] = useState('markets'); // markets | settings | metrics

  return (
    <div className="p-4 max-w-6xl mx-auto" data-testid="forecast-admin-tab">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <h2 className="text-xl font-bold flex items-center gap-2">
          <span>🔮</span> {t('admin.forecast', { defaultValue: 'Prédictions' })}
        </h2>
        <div className="flex gap-1 p-0.5 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }}>
          {['markets', 'settings', 'metrics'].map(v => (
            <button key={v} onClick={() => setView(v)}
              className={`px-3 py-1.5 rounded-md text-xs font-bold ${view === v ? 'jp-btn-primary' : ''}`}
              data-testid={`forecast-admin-tab-${v}`}>
              {t(`admin.forecast_tab_${v}`, { defaultValue: v })}
            </button>
          ))}
        </div>
      </div>

      {view === 'markets'  && <MarketsManager t={t} />}
      {view === 'settings' && <SettingsPanel t={t} />}
      {view === 'metrics'  && <MetricsPanel t={t} />}
    </div>
  );
}

function MarketsManager({ t }) {
  const [markets, setMarkets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState('');
  const [showCreate, setShowCreate] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/api/admin/forecast/markets`,
        { params: statusFilter ? { status: statusFilter } : {}, withCredentials: true });
      setMarkets(data.markets || []);
    } catch (e) { toast.error(e?.response?.data?.detail || 'Load failed'); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [statusFilter]);

  const action = async (market_id, act) => {
    if (act === 'cancel' && !window.confirm(t('admin.forecast.confirm_cancel',
        { defaultValue: 'Annuler ce marché ? Toutes les mises seront remboursées.' }))) return;
    try {
      await axios.post(`${API}/api/admin/forecast/markets/${market_id}/${act}`, {}, { withCredentials: true });
      toast.success(`✓ ${act}`);
      load();
    } catch (e) { toast.error(e?.response?.data?.detail || act + ' failed'); }
  };

  return (
    <>
      <div className="flex justify-between mb-3 flex-wrap gap-2">
        <select className="jp-input text-sm" value={statusFilter}
                onChange={e => setStatusFilter(e.target.value)} data-testid="forecast-admin-status-filter">
          <option value="">{t('admin.forecast.all_statuses', { defaultValue: 'Tous statuts' })}</option>
          {STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <button className="jp-btn-primary" onClick={() => setShowCreate(true)} data-testid="forecast-admin-create-btn">
          + {t('admin.forecast.create')}
        </button>
      </div>

      {showCreate && <CreateMarketForm t={t} onClose={() => setShowCreate(false)} onCreated={load} />}

      {loading ? <div className="text-center py-8">…</div> : (
        <div className="space-y-2">
          {markets.length === 0 && <div className="jp-card p-6 text-center" style={{ color: 'var(--jp-text-muted)' }}>—</div>}
          {markets.map(m => <AdminMarketRow key={m.market_id} t={t} market={m} onAction={action} onReload={load} />)}
        </div>
      )}
    </>
  );
}

function AdminMarketRow({ t, market, onAction, onReload }) {
  const [resolveOpen, setResolveOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);

  // iter241a-fix — clear ON/OFF semantics:
  //   ON  = status='active' AND NOT is_paused (visible & accepting bets)
  //   OFF = anything else (hidden from end-users)
  const isOnline = market.status === 'active' && !market.is_paused;
  const statusColors = {
    draft: '#64748b', active: '#15803d', closed: '#b45309',
    resolved: '#7c3aed', cancelled: '#dc2626',
  };

  const remove = async () => {
    if (!window.confirm(t('admin.forecast.confirm_delete',
        { defaultValue: 'Supprimer définitivement ce marché ? Cette action est irréversible.' }))) return;
    try {
      await axios.delete(`${API}/api/admin/forecast/markets/${market.market_id}`, { withCredentials: true });
      toast.success(t('admin.forecast.deleted_ok', { defaultValue: 'Marché supprimé.' }));
      onReload();
    } catch (e) { toast.error(e?.response?.data?.detail || 'Delete failed'); }
  };

  const canEdit   = market.status === 'draft';
  const canDelete = ['draft', 'cancelled'].includes(market.status);

  return (
    <div className="jp-card p-3" data-testid={`forecast-admin-market-${market.market_id}`}>
      <div className="flex justify-between items-start gap-3 mb-2 flex-wrap">
        <div className="flex-1 min-w-0">
          <div className="text-[10px] uppercase font-bold flex items-center gap-2" style={{ color: 'var(--jp-text-muted)' }}>
            <span>{market.category} · {market.market_type}</span>
            {/* iter241a-fix — ON/OFF visual indicator for any admin to spot live markets at a glance. */}
            <span className="px-1.5 py-0.5 rounded-full text-[9px] font-bold"
                  style={{ background: isOnline ? '#15803d' : '#64748b', color: 'white' }}
                  data-testid={`fc-admin-online-${market.market_id}`}>
              {isOnline ? '● ' + t('admin.forecast.online', { defaultValue: 'EN LIGNE' })
                        : '○ ' + t('admin.forecast.offline', { defaultValue: 'HORS LIGNE' })}
            </span>
          </div>
          <div className="font-bold text-sm" style={{ color: 'var(--jp-text)' }}>{market.title}</div>
          <div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
            {market.bets_count} {t('forecast.bets', { defaultValue: 'paris' })} · {Number(market.total_staked || 0).toFixed(2)} USD
            {' · '} {t('forecast.closes_in')} {market.closes_at ? new Date(market.closes_at).toLocaleString() : '—'}
          </div>
        </div>
        <span className="text-[10px] font-bold px-2 py-0.5 rounded-full whitespace-nowrap"
              style={{ color: statusColors[market.status] || '#64748b', background: 'var(--jp-surface-secondary)' }}>
          {t(`admin.forecast.status_${market.status}`, { defaultValue: market.status })}
          {market.is_paused && ' (' + t('admin.forecast.paused_short', { defaultValue: 'pause' }) + ')'}
        </span>
      </div>

      <div className="flex flex-wrap gap-2 text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>
        {(market.options || []).map(o => (
          <span key={o.option_id} className="px-2 py-0.5 rounded" style={{ background: 'var(--jp-surface-secondary)' }}>
            {o.label} ×{Number(o.multiplier).toFixed(2)}{o.is_winner ? ' 🏆' : ''}
          </span>
        ))}
      </div>

      <div className="flex flex-wrap gap-1 mt-2">
        {/* iter241a-fix — explicit Modifier action surfaces only on drafts. */}
        {canEdit && (
          <button className="jp-btn-secondary jp-btn-sm" onClick={() => setEditOpen(true)}
                  data-testid={`fc-admin-edit-${market.market_id}`}>
            ✏️ {t('admin.forecast.edit', { defaultValue: 'Modifier' })}
          </button>
        )}
        {/* ON/OFF actions — clearer wording than activate/pause/resume. */}
        {market.status === 'draft' && (
          <button className="jp-btn-primary jp-btn-sm" onClick={() => onAction(market.market_id, 'activate')}
                  data-testid={`fc-admin-activate-${market.market_id}`}>
            ▶ {t('admin.forecast.put_online', { defaultValue: 'Mettre en ligne' })}
          </button>
        )}
        {market.status === 'active' && !market.is_paused && (
          <button className="jp-btn-secondary jp-btn-sm" onClick={() => onAction(market.market_id, 'pause')}
                  data-testid={`fc-admin-offline-${market.market_id}`}>
            ⏸ {t('admin.forecast.put_offline', { defaultValue: 'Mettre hors ligne' })}
          </button>
        )}
        {market.status === 'active' && market.is_paused && (
          <button className="jp-btn-primary jp-btn-sm" onClick={() => onAction(market.market_id, 'resume')}
                  data-testid={`fc-admin-online-resume-${market.market_id}`}>
            ▶ {t('admin.forecast.put_online_again', { defaultValue: 'Remettre en ligne' })}
          </button>
        )}
        {market.status === 'active' && (
          <button className="jp-btn-secondary jp-btn-sm" onClick={() => onAction(market.market_id, 'close')}>
            🔒 {t('admin.forecast.close', { defaultValue: 'Fermer' })}
          </button>
        )}
        {(market.status === 'active' || market.status === 'closed') && (
          <button className="jp-btn-primary jp-btn-sm" onClick={() => setResolveOpen(true)}
                  data-testid={`fc-admin-resolve-${market.market_id}`}>
            🏆 {t('admin.forecast.resolve', { defaultValue: 'Résoudre' })}
          </button>
        )}
        {['draft','active','closed'].includes(market.status) && (
          <button className="jp-btn-ghost jp-btn-sm"
                  onClick={() => onAction(market.market_id, 'cancel')}
                  data-testid={`fc-admin-cancel-${market.market_id}`}
                  style={{ color: 'var(--jp-warning, #b45309)' }}>
            ✕ {t('admin.forecast.cancel_market', { defaultValue: 'Annuler' })}
          </button>
        )}
        {canDelete && (
          <button className="jp-btn-ghost jp-btn-sm" onClick={remove}
                  data-testid={`fc-admin-delete-${market.market_id}`}
                  style={{ color: 'var(--jp-error, #dc2626)' }}>
            🗑️ {t('admin.forecast.delete', { defaultValue: 'Supprimer' })}
          </button>
        )}
      </div>

      {resolveOpen && (
        <ResolveModal t={t} market={market} onClose={() => setResolveOpen(false)}
                      onResolved={() => { setResolveOpen(false); onReload(); }} />
      )}
      {editOpen && (
        <EditMarketForm t={t} market={market}
                        onClose={() => setEditOpen(false)}
                        onUpdated={() => { setEditOpen(false); onReload(); }} />
      )}
    </div>
  );
}

function EditMarketForm({ t, market, onClose, onUpdated }) {
  const [title, setTitle] = useState(market.title || '');
  const [description, setDescription] = useState(market.description || '');
  const [category, setCategory] = useState(market.category || 'world');
  const [closesAt, setClosesAt] = useState(market.closes_at ? market.closes_at.slice(0, 16) : '');
  const [sourceLabel, setSourceLabel] = useState(market.source_label || '');
  const [sourceUrl, setSourceUrl] = useState(market.source_url || '');
  const [options, setOptions] = useState(
    (market.options || []).map(o => ({ label: o.label, multiplier: o.multiplier })),
  );
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      await axios.put(`${API}/api/admin/forecast/markets/${market.market_id}`, {
        title, description, category,
        closes_at: closesAt ? new Date(closesAt).toISOString() : undefined,
        source_label: sourceLabel, source_url: sourceUrl,
        options: options.map(o => ({ label: o.label, multiplier: Number(o.multiplier) })),
      }, { withCredentials: true });
      toast.success(t('admin.forecast.updated_ok', { defaultValue: 'Marché mis à jour.' }));
      onUpdated();
    } catch (e) { toast.error(e?.response?.data?.detail || 'Update failed'); }
    finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.6)' }}>
      <div className="jp-card-elevated p-5 max-w-xl w-full max-h-[90vh] overflow-y-auto" data-testid="fc-admin-edit-form">
        <h3 className="font-bold text-lg mb-3">
          ✏️ {t('admin.forecast.edit', { defaultValue: 'Modifier' })}
        </h3>
        <input className="jp-input w-full mb-2" placeholder={t('admin.forecast.title_field', { defaultValue: 'Titre' })}
               value={title} onChange={e => setTitle(e.target.value)} data-testid="fc-admin-edit-title" />
        <textarea className="jp-input w-full mb-2" rows={2} placeholder={t('admin.forecast.desc_field', { defaultValue: 'Description' })}
                  value={description} onChange={e => setDescription(e.target.value)} />
        <div className="grid grid-cols-2 gap-2 mb-2">
          <select className="jp-input" value={category} onChange={e => setCategory(e.target.value)}>
            {CATEGORIES.map(c => <option key={c} value={c}>{t(`forecast.cat_${c}`)}</option>)}
          </select>
          <input className="jp-input" type="datetime-local" value={closesAt} onChange={e => setClosesAt(e.target.value)} />
        </div>
        <input className="jp-input w-full mb-2" placeholder={t('admin.forecast.source_label', { defaultValue: 'Source (label)' })}
               value={sourceLabel} onChange={e => setSourceLabel(e.target.value)} />
        <input className="jp-input w-full mb-3" placeholder={t('admin.forecast.source_url', { defaultValue: 'Source URL' })}
               value={sourceUrl} onChange={e => setSourceUrl(e.target.value)} />

        <div className="text-xs font-bold mb-1">{t('admin.forecast.options', { defaultValue: 'Options' })}</div>
        {options.map((o, i) => (
          <div key={i} className="flex gap-2 mb-2">
            <input className="jp-input flex-1" placeholder="Label" value={o.label}
                   onChange={e => setOptions(opts => opts.map((x, j) => j === i ? { ...x, label: e.target.value } : x))} />
            <input className="jp-input w-24" type="number" step="0.01" min="1.01" max="100"
                   value={o.multiplier}
                   onChange={e => setOptions(opts => opts.map((x, j) => j === i ? { ...x, multiplier: e.target.value } : x))} />
            {options.length > 2 && (
              <button className="jp-btn-ghost jp-btn-sm" onClick={() => setOptions(opts => opts.filter((_, j) => j !== i))}>×</button>
            )}
          </div>
        ))}
        <button className="jp-btn-ghost jp-btn-sm mb-3" onClick={() => setOptions(opts => [...opts, { label: '', multiplier: 2.0 }])}>
          + {t('admin.forecast.add_option', { defaultValue: 'Ajouter option' })}
        </button>

        <div className="flex gap-2 mt-2">
          <button onClick={onClose} className="jp-btn-ghost flex-1">{t('forecast.cancel', { defaultValue: 'Annuler' })}</button>
          <button onClick={submit} disabled={busy} className="jp-btn-primary flex-1" data-testid="fc-admin-edit-submit">
            {busy ? '…' : t('admin.forecast.save', { defaultValue: 'Enregistrer' })}
          </button>
        </div>
      </div>
    </div>
  );
}

function ResolveModal({ t, market, onClose, onResolved }) {
  const [winning, setWinning] = useState(market.options?.[0]?.option_id || '');
  const [notes, setNotes] = useState('');
  const [source, setSource] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!winning) return;
    if (!window.confirm(t('admin.forecast.confirm_resolve',
        { defaultValue: 'Résoudre ce marché ? Les gagnants seront payés immédiatement.' }))) return;
    setBusy(true);
    try {
      await axios.post(`${API}/api/admin/forecast/markets/${market.market_id}/resolve`,
        { winning_option_id: winning, result_notes: notes, source_reference: source },
        { withCredentials: true });
      toast.success(t('admin.forecast.resolved_ok', { defaultValue: 'Marché résolu, paiements envoyés.' }));
      onResolved();
    } catch (e) { toast.error(e?.response?.data?.detail || 'Resolve failed'); }
    finally { setBusy(false); }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.6)' }}>
      <div className="jp-card-elevated p-5 max-w-md w-full">
        <h3 className="font-bold text-lg mb-3">
          🏆 {t('admin.forecast.resolve', { defaultValue: 'Résoudre' })} — {market.title}
        </h3>
        <label className="block text-xs font-bold mb-1">{t('admin.forecast.winning_option', { defaultValue: 'Option gagnante' })}</label>
        <select className="jp-input w-full mb-3" value={winning} onChange={e => setWinning(e.target.value)}
                data-testid="fc-resolve-option">
          {(market.options || []).map(o => <option key={o.option_id} value={o.option_id}>{o.label}</option>)}
        </select>
        <textarea className="jp-input w-full mb-2" rows={2} placeholder={t('admin.forecast.notes_placeholder', { defaultValue: 'Notes…' })}
                  value={notes} onChange={e => setNotes(e.target.value)} />
        <input className="jp-input w-full mb-3" placeholder={t('admin.forecast.source_placeholder', { defaultValue: 'URL source (preuve)' })}
               value={source} onChange={e => setSource(e.target.value)} />
        <div className="flex gap-2">
          <button onClick={onClose} className="jp-btn-ghost flex-1">{t('forecast.cancel', { defaultValue: 'Annuler' })}</button>
          <button onClick={submit} disabled={busy} className="jp-btn-primary flex-1" data-testid="fc-resolve-confirm">
            {busy ? '…' : t('admin.forecast.confirm_resolve_btn', { defaultValue: 'Confirmer' })}
          </button>
        </div>
      </div>
    </div>
  );
}

function CreateMarketForm({ t, onClose, onCreated }) {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('world');
  const [closesAt, setClosesAt] = useState('');
  const [sourceLabel, setSourceLabel] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [options, setOptions] = useState([
    { label: 'Oui', multiplier: 2.0 },
    { label: 'Non', multiplier: 2.0 },
  ]);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!title.trim() || !closesAt) {
      toast.error(t('admin.forecast.missing_fields', { defaultValue: 'Titre et date requis.' })); return;
    }
    setBusy(true);
    try {
      await axios.post(`${API}/api/admin/forecast/markets`, {
        title, description, category, market_type: 'binary',
        closes_at: new Date(closesAt).toISOString(),
        source_label: sourceLabel, source_url: sourceUrl,
        options: options.map(o => ({ label: o.label, multiplier: Number(o.multiplier) })),
      }, { withCredentials: true });
      toast.success(t('admin.forecast.market_created', { defaultValue: 'Marché créé en brouillon.' }));
      onCreated(); onClose();
    } catch (e) { toast.error(e?.response?.data?.detail || 'Create failed'); }
    finally { setBusy(false); }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.6)' }}>
      <div className="jp-card-elevated p-5 max-w-xl w-full max-h-[90vh] overflow-y-auto" data-testid="fc-admin-create-form">
        <h3 className="font-bold text-lg mb-3">+ {t('admin.forecast.create')}</h3>
        <input className="jp-input w-full mb-2" placeholder={t('admin.forecast.title_field', { defaultValue: 'Titre' })}
               value={title} onChange={e => setTitle(e.target.value)} data-testid="fc-admin-title" />
        <textarea className="jp-input w-full mb-2" rows={2} placeholder={t('admin.forecast.desc_field', { defaultValue: 'Description' })}
                  value={description} onChange={e => setDescription(e.target.value)} />
        <div className="grid grid-cols-2 gap-2 mb-2">
          <select className="jp-input" value={category} onChange={e => setCategory(e.target.value)} data-testid="fc-admin-category">
            {CATEGORIES.map(c => <option key={c} value={c}>{t(`forecast.cat_${c}`)}</option>)}
          </select>
          <input className="jp-input" type="datetime-local" value={closesAt} onChange={e => setClosesAt(e.target.value)}
                 data-testid="fc-admin-closes-at" />
        </div>
        <input className="jp-input w-full mb-2" placeholder={t('admin.forecast.source_label', { defaultValue: 'Source (label)' })}
               value={sourceLabel} onChange={e => setSourceLabel(e.target.value)} />
        <input className="jp-input w-full mb-3" placeholder={t('admin.forecast.source_url', { defaultValue: 'Source URL' })}
               value={sourceUrl} onChange={e => setSourceUrl(e.target.value)} />

        <div className="text-xs font-bold mb-1">{t('admin.forecast.options', { defaultValue: 'Options' })}</div>
        {options.map((o, i) => (
          <div key={i} className="flex gap-2 mb-2">
            <input className="jp-input flex-1" placeholder="Label" value={o.label}
                   onChange={e => setOptions(opts => opts.map((x, j) => j === i ? { ...x, label: e.target.value } : x))} />
            <input className="jp-input w-24" type="number" step="0.01" min="1.01" max="100"
                   value={o.multiplier}
                   onChange={e => setOptions(opts => opts.map((x, j) => j === i ? { ...x, multiplier: e.target.value } : x))} />
            {options.length > 2 && (
              <button className="jp-btn-ghost jp-btn-sm" onClick={() => setOptions(opts => opts.filter((_, j) => j !== i))}>×</button>
            )}
          </div>
        ))}
        <button className="jp-btn-ghost jp-btn-sm mb-3" onClick={() => setOptions(opts => [...opts, { label: '', multiplier: 2.0 }])}>
          + {t('admin.forecast.add_option', { defaultValue: 'Ajouter option' })}
        </button>

        <div className="flex gap-2 mt-2">
          <button onClick={onClose} className="jp-btn-ghost flex-1">{t('forecast.cancel', { defaultValue: 'Annuler' })}</button>
          <button onClick={submit} disabled={busy} className="jp-btn-primary flex-1" data-testid="fc-admin-create-submit">
            {busy ? '…' : t('admin.forecast.create')}
          </button>
        </div>
      </div>
    </div>
  );
}

function SettingsPanel({ t }) {
  // iter241a-fix — Two distinct states: `saved` reflects what's in DB,
  // `draft` is what the admin is editing. Save only fires on explicit
  // click — capture 3 showed there was no confirmation, save was silent
  // onBlur and admins didn't see anything happen.
  const [saved, setSaved] = useState(null);
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    axios.get(`${API}/api/admin/forecast/settings`, { withCredentials: true })
      .then(r => { setSaved(r.data); setDraft(r.data); })
      .catch(() => {});
  }, []);

  if (!draft || !saved) return <div className="text-center py-8">…</div>;

  const dirty = JSON.stringify(saved) !== JSON.stringify(draft);

  const save = async () => {
    setBusy(true);
    try {
      const patch = {};
      Object.keys(draft).forEach(k => {
        if (saved[k] !== draft[k]) patch[k] = draft[k];
      });
      if (Object.keys(patch).length === 0) { setBusy(false); return; }
      const { data } = await axios.put(`${API}/api/admin/forecast/settings`, patch,
        { withCredentials: true });
      setSaved(data); setDraft(data);
      toast.success(t('admin.forecast.settings_saved', { defaultValue: '✓ Paramètres enregistrés.' }));
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Save failed');
    } finally { setBusy(false); }
  };

  const reset = () => setDraft(saved);

  const Toggle = ({ k, label }) => (
    <label className="flex items-center justify-between py-2 cursor-pointer" data-testid={`fc-admin-toggle-${k}`}>
      <span className="text-sm">{label}</span>
      <input type="checkbox" checked={!!draft[k]}
             onChange={e => setDraft({ ...draft, [k]: e.target.checked })} />
    </label>
  );
  const Num = ({ k, label }) => (
    <label className="flex items-center justify-between py-2">
      <span className="text-sm">{label}</span>
      <input className="jp-input w-32 text-right" type="number" step="0.01" value={draft[k] ?? ''}
             onChange={e => setDraft({ ...draft, [k]: e.target.value === '' ? '' : Number(e.target.value) })} />
    </label>
  );

  return (
    <div className="jp-card p-4 max-w-2xl" data-testid="forecast-admin-settings">
      <h3 className="font-bold mb-2">{t('admin.forecast_tab_settings', { defaultValue: 'Paramètres' })}</h3>
      <Toggle k="module_enabled"     label={t('admin.forecast.module_enabled', { defaultValue: 'Module activé' })} />
      <hr className="my-2" style={{ borderColor: 'var(--jp-border)' }} />
      <div className="text-xs font-bold mb-1" style={{ color: 'var(--jp-text-muted)' }}>
        {t('admin.forecast.tokens', { defaultValue: 'Tokens acceptés' })}
      </div>
      <Toggle k="token_usd_enabled"  label="USD (canonical)" />
      <Toggle k="token_mir_enabled"  label="MIR (iter241c)" />
      <Toggle k="token_limo_enabled" label="LIMO (iter241c)" />
      <Toggle k="token_usdt_enabled" label="USDT (iter241c)" />
      <hr className="my-2" style={{ borderColor: 'var(--jp-border)' }} />
      <div className="text-xs font-bold mb-1" style={{ color: 'var(--jp-text-muted)' }}>
        {t('admin.forecast.defaults', { defaultValue: 'Valeurs par défaut (USD)' })}
      </div>
      <Num k="default_min_bet"              label={t('admin.forecast.min_bet', { defaultValue: 'Mise min' })} />
      <Num k="default_max_bet"              label={t('admin.forecast.max_bet', { defaultValue: 'Mise max' })} />
      <Num k="default_max_bet_per_user"     label={t('admin.forecast.max_per_user', { defaultValue: 'Plafond / user' })} />
      <Num k="default_max_exposure"         label={t('admin.forecast.max_exposure', { defaultValue: 'Exposition max' })} />
      <Num k="default_platform_fee_percent" label={t('admin.forecast.fee_percent', { defaultValue: 'Frais (%)' })} />

      {/* iter241a-fix — explicit Save / Reset buttons (sticky bottom row). */}
      <div className="flex gap-2 mt-4 pt-3 border-t" style={{ borderColor: 'var(--jp-border)' }}>
        <button onClick={reset} disabled={!dirty || busy} className="jp-btn-ghost"
                data-testid="fc-admin-settings-reset">
          {t('admin.forecast.reset', { defaultValue: 'Annuler les modifications' })}
        </button>
        <button onClick={save} disabled={!dirty || busy}
                className="jp-btn-primary flex-1 disabled:opacity-50"
                data-testid="fc-admin-settings-save">
          {busy ? '…' : (dirty
            ? '💾 ' + t('admin.forecast.save_settings', { defaultValue: 'Enregistrer les paramètres' })
            : t('admin.forecast.up_to_date', { defaultValue: 'À jour' }))}
        </button>
      </div>
      {dirty && (
        <div className="text-[11px] mt-2" style={{ color: 'var(--jp-warning, #b45309)' }}
             data-testid="fc-admin-settings-dirty">
          ⚠ {t('admin.forecast.unsaved_changes', { defaultValue: 'Modifications non enregistrées.' })}
        </div>
      )}
    </div>
  );
}

function MetricsPanel({ t }) {
  const [m, setM] = useState(null);
  useEffect(() => {
    axios.get(`${API}/api/admin/forecast/metrics`, { withCredentials: true })
      .then(r => setM(r.data)).catch(() => {});
  }, []);
  if (!m) return <div className="text-center py-8">…</div>;
  const cards = [
    { k: 'markets_total',    label: t('admin.forecast.markets_total',    { defaultValue: 'Marchés total' }),    value: m.markets_total },
    { k: 'markets_active',   label: t('admin.forecast.markets_active',   { defaultValue: 'Actifs' }),           value: m.markets_active },
    { k: 'markets_resolved', label: t('admin.forecast.markets_resolved', { defaultValue: 'Résolus' }),          value: m.markets_resolved },
    { k: 'bets_total',       label: t('admin.forecast.bets_total',       { defaultValue: 'Paris total' }),      value: m.bets_total },
    { k: 'unique_users',     label: t('admin.forecast.unique_users',     { defaultValue: 'Joueurs uniques' }),  value: m.unique_users },
    { k: 'total_staked',     label: t('admin.forecast.total_staked',     { defaultValue: 'Misé (USD)' }),       value: Number(m.total_staked || 0).toFixed(2) },
    { k: 'total_fees',       label: t('admin.forecast.total_fees',       { defaultValue: 'Frais (USD)' }),      value: Number(m.total_fees || 0).toFixed(2) },
    { k: 'total_payouts',    label: t('admin.forecast.total_payouts',    { defaultValue: 'Payouts (USD)' }),    value: Number(m.total_payouts || 0).toFixed(2) },
  ];
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3" data-testid="forecast-admin-metrics">
      {cards.map(c => (
        <div key={c.k} className="jp-card p-3" data-testid={`fc-metric-${c.k}`}>
          <div className="text-[10px] uppercase font-bold" style={{ color: 'var(--jp-text-muted)' }}>{c.label}</div>
          <div className="text-2xl font-bold mt-1" style={{ color: 'var(--jp-text)' }}>{c.value ?? '—'}</div>
        </div>
      ))}
    </div>
  );
}
