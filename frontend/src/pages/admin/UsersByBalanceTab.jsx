import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { MagnifyingGlass, Download, ArrowDown, Funnel } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * UsersByBalanceTab — vue admin "Utilisateurs par solde" (iter154).
 * Triés par solde wallet décroissant, avec pagination, recherche
 * (email/username/phone), filtres (pays, statut, legacy/new) et export CSV.
 */
export default function UsersByBalanceTab() {
  const { t } = useTranslation();
  const [users, setUsers] = useState([]);
  const [total, setTotal] = useState(0);
  const [totalBalance, setTotalBalance] = useState('0');
  const [page, setPage] = useState(1);
  const [limit] = useState(50);
  const [loading, setLoading] = useState(false);

  const [search, setSearch] = useState('');
  const [country, setCountry] = useState('');
  const [status, setStatus] = useState('');
  const [legacy, setLegacy] = useState('');
  const [minBalance, setMinBalance] = useState('');

  const buildQuery = useCallback(() => {
    const q = new URLSearchParams();
    q.set('page', String(page));
    q.set('limit', String(limit));
    if (search.trim()) q.set('search', search.trim());
    if (country.trim()) q.set('country', country.trim().toUpperCase());
    if (status) q.set('status', status);
    if (legacy) q.set('legacy', legacy);
    if (minBalance) q.set('min_balance', String(parseFloat(minBalance) || 0));
    return q.toString();
  }, [page, limit, search, country, status, legacy, minBalance]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/api/admin/users-by-balance?${buildQuery()}`,
        { withCredentials: true });
      setUsers(r.data?.users || []);
      setTotal(r.data?.total || 0);
      setTotalBalance(r.data?.total_balance || '0');
    } catch (e) {
      toast.error('Impossible de charger les utilisateurs.');
    } finally {
      setLoading(false);
    }
  }, [buildQuery]);

  useEffect(() => { load(); }, [load]);

  const exportCsv = async () => {
    try {
      const url = `${API}/api/admin/users-by-balance?${buildQuery()}&export=csv`;
      const r = await axios.get(url, { withCredentials: true, responseType: 'blob' });
      const blob = new Blob([r.data], { type: 'text/csv' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      const ts = new Date().toISOString().slice(0, 10);
      a.download = `japap_users_by_balance_${ts}.csv`;
      a.click();
      URL.revokeObjectURL(a.href);
      toast.success('Export CSV téléchargé.');
    } catch {
      toast.error('Échec de l\'export CSV.');
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / limit));

  return (
    <div data-testid="users-by-balance-tab" className="space-y-4">
      {/* Filters bar */}
      <div className="rounded-2xl p-4 border space-y-3"
        style={{ borderColor: 'var(--jp-border)', background: 'var(--jp-card)' }}>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative flex-1 min-w-[220px]">
            <MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2"
              size={16} style={{ color: 'var(--jp-text-muted)' }} />
            <input
              data-testid="ubb-search"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1); }}
              placeholder={t('users_by_balance.rechercher_email_username_telephone')}
              className="jp-input text-sm w-full"
              style={{ paddingLeft: 36 }} />
          </div>
          <input
            data-testid="ubb-country"
            value={country}
            onChange={(e) => { setCountry(e.target.value); setPage(1); }}
            placeholder={t('users_by_balance.pays_ex_cm')}
            className="jp-input text-sm"
            style={{ width: 110 }} />
          <select
            data-testid="ubb-status"
            value={status}
            onChange={(e) => { setStatus(e.target.value); setPage(1); }}
            className="jp-input text-sm"
            style={{ width: 130 }}>
            <option value="">{t('users_by_balance.tous_statuts')}</option>
            <option value="active">{t('users_by_balance.actifs')}</option>
            <option value="suspended">{t('users_by_balance.suspendus')}</option>
          </select>
          <select
            data-testid="ubb-legacy"
            value={legacy}
            onChange={(e) => { setLegacy(e.target.value); setPage(1); }}
            className="jp-input text-sm"
            style={{ width: 140 }}>
            <option value="">{t('users_by_balance.tous_comptes')}</option>
            <option value="legacy">{t('users_by_balance.legacy_1_0')}</option>
            <option value="new">{t('users_by_balance.nouveaux_4_0')}</option>
          </select>
          <input
            data-testid="ubb-minbalance"
            type="number"
            min="0"
            value={minBalance}
            onChange={(e) => { setMinBalance(e.target.value); setPage(1); }}
            placeholder={t('users_by_balance.solde_min')}
            className="jp-input text-sm"
            style={{ width: 110 }} />
          <button
            data-testid="ubb-export-csv"
            onClick={exportCsv}
            className="px-3 py-2 rounded-full text-xs font-bold"
            style={{ background: 'var(--jp-primary)', color: '#fff' }}>
            <Download size={14} className="inline mr-1" weight="bold" />
            Export CSV
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-3 text-xs font-['Manrope']"
          style={{ color: 'var(--jp-text-muted)' }}>
          <Funnel size={14} className="inline" />
          <span><strong>{total.toLocaleString('fr-FR')}</strong> utilisateurs</span>
          <span>·</span>
          <span>{t('users_by_balance.solde_cumule')}<strong>{parseFloat(totalBalance).toLocaleString('fr-FR', { maximumFractionDigits: 2 })}</strong></span>
          <span>·</span>
          <span><ArrowDown size={11} className="inline" /> Tri par solde décroissant</span>
        </div>
      </div>

      {/* Table */}
      <div className="rounded-2xl border overflow-x-auto"
        style={{ borderColor: 'var(--jp-border)', background: 'var(--jp-card)' }}>
        <table className="jp-table w-full">
          <thead>
            <tr>
              <th>{t('users_by_balance.utilisateur')}</th>
              <th>{t('users_by_balance.email')}</th>
              <th>{t('users_by_balance.telephone')}</th>
              <th>Pays</th>
              <th style={{ textAlign: 'right' }}>{t('users_by_balance.solde')}</th>
              <th>{t('users_by_balance.statut')}</th>
              <th>{t('users_by_balance.inscrit')}</th>
              <th>{t('users_by_balance.derniere_conn')}</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={8} className="text-center py-8 text-sm"
                style={{ color: 'var(--jp-text-muted)' }}>Chargement…</td></tr>
            )}
            {!loading && users.length === 0 && (
              <tr><td colSpan={8} className="text-center py-8 text-sm"
                style={{ color: 'var(--jp-text-muted)' }}>{t('users_by_balance.aucun_resultat')}</td></tr>
            )}
            {users.map((u, i) => (
              <tr key={u.user_id} data-testid={`ubb-row-${u.user_id}`}>
                <td>
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-xs px-2 py-0.5 rounded-full"
                      style={{ background: i < 3 ? '#facc15' : 'rgba(15,5,107,0.08)', color: i < 3 ? '#7c5300' : 'var(--jp-primary)' }}>
                      #{((page - 1) * limit) + i + 1}
                    </span>
                    <div>
                      <div className="font-semibold text-sm">{u.first_name || u.username} {u.last_name || ''}</div>
                      <div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>@{u.username}</div>
                    </div>
                  </div>
                </td>
                <td className="text-sm">{u.email}</td>
                <td className="text-sm">{u.phone_number || '—'}</td>
                <td className="text-sm">{u.country || '—'}</td>
                <td style={{ textAlign: 'right' }}>
                  <span className="font-['Outfit'] font-bold text-sm">
                    {parseFloat(u.balance).toLocaleString('fr-FR', { maximumFractionDigits: 2 })}
                  </span>
                  <span className="text-[11px] ml-1" style={{ color: 'var(--jp-text-muted)' }}>
                    {u.currency || ''}
                  </span>
                </td>
                <td>
                  {u.is_active
                    ? <span className="jp-badge jp-badge-success">Actif</span>
                    : <span className="jp-badge jp-badge-error">Suspendu</span>}
                  {u.is_legacy && <span className="jp-badge jp-badge-neutral ml-1">Legacy</span>}
                </td>
                <td className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
                  {u.created_at ? new Date(u.created_at).toLocaleDateString('fr-FR') : '—'}
                </td>
                <td className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
                  {u.last_seen ? new Date(u.last_seen).toLocaleString('fr-FR', {
                    dateStyle: 'short', timeStyle: 'short',
                  }) : 'Jamais'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {total > limit && (
        <div className="flex justify-center items-center gap-3 py-2 text-sm font-['Manrope']">
          <button disabled={page <= 1} onClick={() => setPage(p => Math.max(1, p - 1))}
            data-testid="ubb-prev"
            className="jp-btn jp-btn-ghost jp-btn-sm">
            ← Précédent
          </button>
          <span style={{ color: 'var(--jp-text-muted)' }}>
            Page {page} / {totalPages}
          </span>
          <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}
            data-testid="ubb-next"
            className="jp-btn jp-btn-ghost jp-btn-sm">
            Suivant →
          </button>
        </div>
      )}
    </div>
  );
}
