/**
 * iter235 — Admin Wave (Afrique de l'Ouest) — Settings + dépôts/retraits PENDING.
 * Strictement additif. À insérer dans /admin → Paiements → Paramètres de paiement.
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { CheckCircle, XCircle } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;
const COUNTRY_OPTIONS = [
  { code: 'BF', label: 'Burkina Faso' },
  { code: 'CI', label: "Côte d'Ivoire" },
  { code: 'ML', label: 'Mali' },
  { code: 'NE', label: 'Niger' },
  { code: 'SN', label: 'Sénégal' },
  { code: 'GM', label: 'Gambie' },
  { code: 'UG', label: 'Ouganda' },
];

export default function AdminWaveSection() {
  const [settings, setSettings] = useState(null);
  const [stats, setStats] = useState(null);
  const [deposits, setDeposits] = useState([]);
  const [withdrawals, setWithdrawals] = useState([]);
  const [savingSettings, setSavingSettings] = useState(false);
  const [filter, setFilter] = useState('PENDING');

  const loadAll = useCallback(async () => {
    try {
      const [s, st, d, w] = await Promise.all([
        axios.get(`${API}/api/admin/wave/settings`, { withCredentials: true }),
        axios.get(`${API}/api/admin/wave/stats`, { withCredentials: true }),
        axios.get(`${API}/api/admin/wave/deposits?status=${filter}&limit=50`, { withCredentials: true }),
        axios.get(`${API}/api/admin/wave/withdrawals?status=${filter}&limit=50`, { withCredentials: true }),
      ]);
      setSettings(s.data); setStats(st.data);
      setDeposits(d.data.deposits || []); setWithdrawals(w.data.withdrawals || []);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur de chargement Wave.');
    }
  }, [filter]);
  useEffect(() => { loadAll(); }, [loadAll]);

  const toggleCountry = (code) => {
    const list = new Set(settings.allowed_countries || []);
    list.has(code) ? list.delete(code) : list.add(code);
    setSettings(s => ({ ...s, allowed_countries: Array.from(list) }));
  };

  const saveSettings = async () => {
    setSavingSettings(true);
    try {
      await axios.patch(`${API}/api/admin/wave/settings`, settings, { withCredentials: true });
      toast.success('Paramètres Wave enregistrés.');
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Échec enregistrement.');
    } finally { setSavingSettings(false); }
  };

  const verifyDep = async (id) => {
    if (!window.confirm('Confirmer la vérification (le wallet sera crédité) ?')) return;
    try { await axios.patch(`${API}/api/admin/wave/deposits/${id}/verify`, {}, { withCredentials: true });
      toast.success('Dépôt Wave vérifié et crédité.'); loadAll(); }
    catch (e) { toast.error(e?.response?.data?.detail || 'Échec.'); }
  };
  const rejectDep = async (id) => {
    const motif = window.prompt('Motif du rejet :');
    if (!motif || motif.length < 2) return;
    try { await axios.patch(`${API}/api/admin/wave/deposits/${id}/reject`, { motif }, { withCredentials: true });
      toast.success('Dépôt rejeté.'); loadAll(); }
    catch (e) { toast.error(e?.response?.data?.detail || 'Échec.'); }
  };
  const sentW = async (id) => {
    if (!window.confirm('Confirmer envoi du retrait ?')) return;
    try { await axios.patch(`${API}/api/admin/wave/withdrawals/${id}/sent`, {}, { withCredentials: true });
      toast.success('Retrait marqué SENT.'); loadAll(); }
    catch (e) { toast.error(e?.response?.data?.detail || 'Échec.'); }
  };
  const rejectW = async (id) => {
    const motif = window.prompt('Motif du rejet (recrédit auto) :');
    if (!motif || motif.length < 2) return;
    try { await axios.patch(`${API}/api/admin/wave/withdrawals/${id}/reject`, { motif }, { withCredentials: true });
      toast.success('Retrait rejeté + recrédité.'); loadAll(); }
    catch (e) { toast.error(e?.response?.data?.detail || 'Échec.'); }
  };

  if (!settings) return <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>;

  return (
    <div className="jp-card-elevated p-5 mb-4" data-testid="admin-wave-section">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full flex items-center justify-center" style={{ background: '#1DC8FA', color: 'white', fontWeight: 800, fontSize: 12 }}>W</div>
          <h3 className="font-['Outfit'] text-base font-bold">Wave — Afrique de l'Ouest</h3>
        </div>
        {stats && stats.pending > 0 && (
          <span className="px-2 py-1 rounded text-xs font-bold" style={{ background: '#FEF3C7', color: '#92400E' }} data-testid="wave-pending-badge">
            {stats.pending} en attente
          </span>
        )}
      </div>

      {stats && (
        <div className="grid grid-cols-3 gap-2 mb-4">
          <Stat label="Aujourd'hui" value={`${stats.day_usd.toFixed(2)} USD`} />
          <Stat label="Mois" value={`${stats.month_usd.toFixed(2)} USD`} />
          <Stat label="Année" value={`${stats.year_usd.toFixed(2)} USD`} />
        </div>
      )}

      <details className="mb-4" data-testid="wave-settings-block">
        <summary className="cursor-pointer text-sm font-bold mb-2">⚙️ Paramètres Wave</summary>
        <div className="grid grid-cols-2 gap-3 mt-3">
          <Field label="Taux dépôt (USD → XOF)" value={settings.deposit_rate} type="number"
            testId="wave-deposit-rate" onChange={v => setSettings(s => ({ ...s, deposit_rate: v }))} />
          <Field label="Taux retrait (USD → XOF)" value={settings.withdraw_rate} type="number"
            testId="wave-withdraw-rate" onChange={v => setSettings(s => ({ ...s, withdraw_rate: v }))} />
          <Field label="Min. retrait (USD)" value={settings.withdraw_min} type="number"
            testId="wave-withdraw-min" onChange={v => setSettings(s => ({ ...s, withdraw_min: v }))} />
          <Field label="Nom récepteur" value={settings.receiver_name}
            testId="wave-receiver-name" onChange={v => setSettings(s => ({ ...s, receiver_name: v }))} />
          <Field label="Numéro récepteur" value={settings.receiver_num}
            testId="wave-receiver-num" onChange={v => setSettings(s => ({ ...s, receiver_num: v }))} />
          <div className="flex items-end">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={!!settings.enabled} data-testid="wave-enabled-toggle"
                onChange={e => setSettings(s => ({ ...s, enabled: e.target.checked }))} />
              Méthode activée
            </label>
          </div>
        </div>
        <div className="mt-3">
          <label className="jp-label text-xs">Pays autorisés</label>
          <div className="flex flex-wrap gap-2" data-testid="wave-allowed-countries">
            {COUNTRY_OPTIONS.map(c => (
              <label key={c.code} className="flex items-center gap-1 text-xs px-2 py-1 rounded"
                style={{ background: settings.allowed_countries?.includes(c.code) ? 'var(--jp-primary-subtle)' : 'var(--jp-surface-secondary)' }}>
                <input type="checkbox" checked={settings.allowed_countries?.includes(c.code) || false}
                  data-testid={`wave-country-${c.code}`} onChange={() => toggleCountry(c.code)} />
                {c.code} · {c.label}
              </label>
            ))}
          </div>
        </div>
        <button onClick={saveSettings} disabled={savingSettings} data-testid="wave-save-settings"
          className="jp-btn jp-btn-primary jp-btn-sm mt-3">
          {savingSettings ? 'Enregistrement…' : 'Enregistrer'}
        </button>
      </details>

      <div className="flex gap-2 mb-3" data-testid="wave-filter-tabs">
        {['PENDING', 'VERIFIED', 'SENT', 'REJECTED'].map(s => (
          <button key={s} onClick={() => setFilter(s)} data-testid={`wave-filter-${s}`}
            className="px-2 py-1 rounded text-xs font-bold" style={{
              background: filter === s ? 'var(--jp-primary)' : 'var(--jp-surface-secondary)',
              color: filter === s ? 'white' : 'var(--jp-text)' }}>
            {s}
          </button>
        ))}
      </div>

      <div className="mb-4">
        <h4 className="text-sm font-bold mb-2">Dépôts ({deposits.length})</h4>
        {deposits.length === 0 ? (
          <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>Aucun dépôt {filter}.</p>
        ) : (
          <div className="space-y-2" data-testid="wave-deposits-list">
            {deposits.map(d => (
              <Row key={d.id} data-testid={`wave-deposit-${d.id}`}>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-bold truncate">{d.email || d.username || d.user_id}</div>
                  <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
                    {d.montant_usd} USD · {Number(d.montant_xof).toLocaleString('fr-FR')} XOF · ref: {d.reference} · {d.numero_exp}
                  </div>
                </div>
                {d.statut === 'PENDING' && (
                  <div className="flex gap-1">
                    <button onClick={() => verifyDep(d.id)} className="jp-btn jp-btn-xs" style={{ background: '#10B981', color: 'white' }}
                      data-testid={`wave-verify-${d.id}`}><CheckCircle size={14} /></button>
                    <button onClick={() => rejectDep(d.id)} className="jp-btn jp-btn-xs" style={{ background: '#EF4444', color: 'white' }}
                      data-testid={`wave-reject-${d.id}`}><XCircle size={14} /></button>
                  </div>
                )}
                {d.statut !== 'PENDING' && <Tag status={d.statut} />}
              </Row>
            ))}
          </div>
        )}
      </div>

      <div>
        <h4 className="text-sm font-bold mb-2">Retraits ({withdrawals.length})</h4>
        {withdrawals.length === 0 ? (
          <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>Aucun retrait {filter}.</p>
        ) : (
          <div className="space-y-2" data-testid="wave-withdrawals-list">
            {withdrawals.map(w => (
              <Row key={w.id} data-testid={`wave-withdrawal-${w.id}`}>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-bold truncate">{w.email || w.username || w.user_id}</div>
                  <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
                    {w.montant_usd} USD · {Number(w.montant_xof).toLocaleString('fr-FR')} XOF · {w.numero_wave} · {w.nom_titulaire}
                  </div>
                </div>
                {w.statut === 'PENDING' && (
                  <div className="flex gap-1">
                    <button onClick={() => sentW(w.id)} className="jp-btn jp-btn-xs" style={{ background: '#10B981', color: 'white' }}
                      data-testid={`wave-w-sent-${w.id}`}><CheckCircle size={14} /></button>
                    <button onClick={() => rejectW(w.id)} className="jp-btn jp-btn-xs" style={{ background: '#EF4444', color: 'white' }}
                      data-testid={`wave-w-reject-${w.id}`}><XCircle size={14} /></button>
                  </div>
                )}
                {w.statut !== 'PENDING' && <Tag status={w.statut} />}
              </Row>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="rounded-xl p-3 text-center" style={{ background: 'var(--jp-surface-secondary)' }}>
      <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>{label}</div>
      <div className="text-sm font-bold mt-1">{value}</div>
    </div>
  );
}
function Field({ label, value, onChange, type = 'text', testId }) {
  return (
    <div>
      <label className="jp-label text-xs">{label}</label>
      <input className="jp-input text-sm" type={type} value={value ?? ''} data-testid={testId}
        onChange={e => onChange(type === 'number' ? Number(e.target.value) : e.target.value)} />
    </div>
  );
}
function Row({ children, ...props }) {
  return (
    <div className="flex items-center gap-2 p-2 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }} {...props}>
      {children}
    </div>
  );
}
function Tag({ status }) {
  const map = {
    VERIFIED: { bg: '#D1FAE5', col: '#065F46' },
    SENT:     { bg: '#D1FAE5', col: '#065F46' },
    REJECTED: { bg: '#FEE2E2', col: '#991B1B' },
    PENDING:  { bg: '#FEF3C7', col: '#92400E' },
  };
  const s = map[status] || map.PENDING;
  return <span className="px-2 py-0.5 rounded text-[10px] font-bold" style={{ background: s.bg, color: s.col }}>{status}</span>;
}
