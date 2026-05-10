/**
 * iter238c — Admin Wallet Diagnostics page (STRICTLY ADDITIVE).
 *
 * Route: /admin/wallet/diagnostics?user_id=XXX
 *
 * Back-office only — protected by `<ProtectedRoute adminOnly>` PLUS a
 * server-side `require_admin` on the API endpoint. Any non-admin who
 * crafts the URL gets bounced by both layers.
 *
 * Replaces the old "🔍 Debug admin" banner on /wallet (removed in
 * iter238c) by giving support staff a dedicated, never-leaked tool to
 * troubleshoot MoMo eligibility for any user.
 */
import { useEffect, useState, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import {
  ArrowLeft, MagnifyingGlass, CheckCircle, XCircle, Wallet,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

function Flag({ ok }) {
  return ok
    ? <CheckCircle size={16} weight="fill" color="#16a34a" />
    : <XCircle size={16} weight="fill" color="#dc2626" />;
}

function Row({ label, value, mono = false }) {
  return (
    <div className="flex items-center justify-between py-2 text-sm border-b last:border-0"
         style={{ borderColor: 'var(--jp-border)' }}>
      <span style={{ color: 'var(--jp-text-secondary)' }}>{label}</span>
      <span className={mono ? 'font-mono text-xs' : ''}
            style={{ color: 'var(--jp-text)' }}>
        {value === null || value === undefined || value === ''
          ? <em style={{ color: 'var(--jp-text-muted)' }}>∅</em>
          : value}
      </span>
    </div>
  );
}

export default function AdminWalletDiagnosticsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [userIdInput, setUserIdInput] = useState(params.get('user_id') || '');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchDiag = useCallback(async (uid) => {
    if (!uid) return;
    setLoading(true); setError(null);
    try {
      const { data } = await axios.get(
        `${API}/api/admin/wallet/diagnostics?user_id=${encodeURIComponent(uid)}`,
        { withCredentials: true },
      );
      setData(data);
    } catch (e) {
      const detail = e.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail
                : detail?.message || 'Failed to load diagnostics';
      setError(msg);
      setData(null);
      toast.error(msg);
    } finally { setLoading(false); }
  }, []);

  // Auto-load when URL has ?user_id=...
  useEffect(() => {
    const uid = params.get('user_id');
    if (uid) fetchDiag(uid);
  }, [params, fetchDiag]);

  const submit = (e) => {
    e.preventDefault();
    const uid = userIdInput.trim();
    if (!uid) return;
    setParams({ user_id: uid });
  };

  return (
    <div className="min-h-screen" style={{ background: 'var(--jp-background)' }}
         data-testid="admin-wallet-diagnostics-page">
      <div className="max-w-2xl mx-auto p-4">
        <button type="button"
          onClick={() => navigate('/admin')}
          className="flex items-center gap-2 text-sm mb-4"
          style={{ color: 'var(--jp-text-secondary)' }}
          data-testid="diag-back-btn">
          <ArrowLeft size={16} /> Retour Admin
        </button>

        <h1 className="font-['Outfit'] text-2xl font-bold mb-1"
            style={{ color: 'var(--jp-text)' }}>
          🔍 Wallet Diagnostics
        </h1>
        <p className="text-sm mb-6" style={{ color: 'var(--jp-text-secondary)' }}>
          Pays détecté + flags d'éligibilité Mobile Money (Orange Money / Wave)
          pour n'importe quel utilisateur. <strong>Back-office uniquement.</strong>
        </p>

        <form onSubmit={submit} className="flex gap-2 mb-4">
          <input type="text" value={userIdInput}
            onChange={e => setUserIdInput(e.target.value)}
            placeholder="user_id (ex: user_a1b203440a53)"
            className="jp-input text-sm flex-1 font-mono"
            data-testid="diag-user-id-input" />
          <button type="submit" disabled={loading || !userIdInput.trim()}
            className="jp-btn jp-btn-primary"
            data-testid="diag-search-btn">
            <MagnifyingGlass size={14} /> {loading ? 'Recherche…' : 'Rechercher'}
          </button>
        </form>

        {error && (
          <div className="jp-card p-3 mb-4 text-sm"
               style={{ background: 'rgba(220,38,38,0.06)', color: '#991b1b' }}
               data-testid="diag-error">
            {error}
          </div>
        )}

        {data && (
          <>
            <div className="jp-card-elevated p-4 mb-3" data-testid="diag-user-card">
              <h3 className="font-['Manrope'] text-sm font-bold mb-2"
                  style={{ color: 'var(--jp-text)' }}>
                Utilisateur
              </h3>
              <Row label="user_id" value={data.user.user_id} mono />
              <Row label="username" value={data.user.username} />
              <Row label="email" value={data.user.email} />
              <Row label="role" value={data.user.role} />
              <Row label="language" value={data.user.language} />
            </div>

            <div className="jp-card-elevated p-4 mb-3" data-testid="diag-country-card">
              <h3 className="font-['Manrope'] text-sm font-bold mb-2"
                  style={{ color: 'var(--jp-text)' }}>
                Pays détecté
              </h3>
              <Row label="resolved (cc)"   value={data.country.resolved}     mono />
              <Row label="country_code"    value={data.country.country_code} mono />
              <Row label="country (raw)"   value={data.country.country_raw}  mono />
              <Row label="phone_number"    value={data.phone}                mono />
            </div>

            <div className="jp-card-elevated p-4 mb-3" data-testid="diag-wallet-card">
              <h3 className="font-['Manrope'] text-sm font-bold mb-2 flex items-center gap-2"
                  style={{ color: 'var(--jp-text)' }}>
                <Wallet size={14} weight="duotone" /> Wallet
              </h3>
              <Row label="balance (USD)"
                value={data.wallet.balance_usd === null
                       ? null
                       : data.wallet.balance_usd.toFixed(2)} />
            </div>

            <div className="jp-card-elevated p-4" data-testid="diag-eligibility-card">
              <h3 className="font-['Manrope'] text-sm font-bold mb-3"
                  style={{ color: 'var(--jp-text)' }}>
                Éligibilité Mobile Money
              </h3>
              <div className="flex items-center justify-between py-2 text-sm border-b"
                   style={{ borderColor: 'var(--jp-border)' }}>
                <span>Orange Money — dépôt</span>
                <span data-testid="diag-elig-om-deposit"
                      data-eligible={String(data.eligibility.orange_money_deposit)}>
                  <Flag ok={data.eligibility.orange_money_deposit} />
                </span>
              </div>
              <div className="flex items-center justify-between py-2 text-sm border-b"
                   style={{ borderColor: 'var(--jp-border)' }}>
                <span>Orange Money — retrait</span>
                <span data-testid="diag-elig-om-withdraw"
                      data-eligible={String(data.eligibility.orange_money_withdraw)}>
                  <Flag ok={data.eligibility.orange_money_withdraw} />
                </span>
              </div>
              <div className="flex items-center justify-between py-2 text-sm">
                <span>Wave (BF · CI · ML · NE · SN · GM · UG)</span>
                <span data-testid="diag-elig-wave"
                      data-eligible={String(data.eligibility.wave)}>
                  <Flag ok={data.eligibility.wave} />
                </span>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
