// iter240l — Onglet "Jury" dans l'Admin Crowdfunding
// Réutilise les endpoints existants : GET /admin/jury, POST /admin/jury/grant,
// POST /admin/jury/{user_id}/revoke, GET /jury/certificate/{user_id}.png.
// 100% additif — N'ajoute AUCUN endpoint backend.
import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import { Crown, Trash, Download, ArrowsClockwise, UserPlus, FileText } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;
const fmtDate = (s) => { try { return s ? new Date(s).toLocaleDateString() : '—'; } catch { return '—'; } };

export default function CrowdfundingAdminJuryTab() {
  const { t } = useTranslation();
  const [members, setMembers] = useState([]);
  const [includeRevoked, setIncludeRevoked] = useState(false);
  const [loading, setLoading] = useState(false);
  const [grantUserId, setGrantUserId] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(
        `${API}/api/crowdfunding/admin/jury?include_revoked=${includeRevoked}`,
        { withCredentials: true });
      setMembers(Array.isArray(data) ? data : (data?.members || []));
    } catch (e) {
      toast.error(t('crowdfunding.admin_jury_load_failed', { defaultValue: 'Échec du chargement' }));
    } finally { setLoading(false); }
  }, [includeRevoked, t]);
  useEffect(() => { load(); }, [load]);

  const grant = async () => {
    const uid = grantUserId.trim();
    if (!uid) return;
    setBusy(true);
    try {
      await axios.post(`${API}/api/crowdfunding/admin/jury/grant`, { user_id: uid }, { withCredentials: true });
      toast.success(t('crowdfunding.admin_jury_granted', { defaultValue: 'Juré nommé' }));
      setGrantUserId(''); load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || t('crowdfunding.admin_jury_grant_failed', { defaultValue: 'Échec de la nomination' }));
    } finally { setBusy(false); }
  };

  const revoke = async (uid) => {
    const reason = window.prompt(t('crowdfunding.admin_jury_revoke_reason_prompt', { defaultValue: 'Motif de révocation (min 5 caractères)' }));
    if (!reason || reason.length < 5) return;
    setBusy(true);
    try {
      await axios.post(`${API}/api/crowdfunding/admin/jury/${uid}/revoke`, { reason }, { withCredentials: true });
      toast.success(t('crowdfunding.admin_jury_revoked', { defaultValue: 'Juré révoqué' }));
      load();
    } catch (e) {
      toast.error(e?.response?.data?.detail || t('crowdfunding.admin_jury_revoke_failed', { defaultValue: 'Échec de la révocation' }));
    } finally { setBusy(false); }
  };

  const regenCert = async (uid) => {
    setBusy(true);
    try {
      const { data } = await axios.post(
        `${API}/api/crowdfunding/admin/jury/${uid}/regenerate-certificate?lang=fr`,
        null, { withCredentials: true });
      if (data?.ok) {
        toast.success(t('crowdfunding.admin_jury_cert_regen_ok', { defaultValue: 'Certificat régénéré et publié sur R2' }));
        load();
      } else {
        toast.warning(data?.reason || t('crowdfunding.admin_jury_cert_regen_partial', { defaultValue: 'SVG régénéré mais R2 non configuré' }));
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || t('crowdfunding.admin_jury_cert_regen_failed', { defaultValue: 'Échec de la régénération' }));
    } finally { setBusy(false); }
  };

  return (
    <div className="space-y-4" data-testid="cf-admin-jury-tab">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h3 className="text-lg font-semibold flex items-center gap-2">
          <Crown size={18} /> {t('crowdfunding.admin_jury_title', { defaultValue: 'Jurés' })}
          <span className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>({members.length})</span>
        </h3>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={includeRevoked} onChange={e => setIncludeRevoked(e.target.checked)} data-testid="cf-admin-jury-include-revoked" />
          {t('crowdfunding.admin_jury_include_revoked', { defaultValue: 'Inclure les révoqués' })}
        </label>
        <button className="jp-btn jp-btn-ghost jp-btn-sm" onClick={load} data-testid="cf-admin-jury-reload">
          <ArrowsClockwise size={14} /> {t('common.reload', { defaultValue: 'Actualiser' })}
        </button>
      </div>

      {/* Grant form */}
      <div className="jp-card" style={{ padding: 12 }}>
        <h4 className="font-medium text-sm mb-2">{t('crowdfunding.admin_jury_appoint', { defaultValue: 'Nommer manuellement un juré' })}</h4>
        <div className="flex gap-2">
          <input className="jp-input text-sm flex-1" placeholder="user_id"
            value={grantUserId} onChange={e => setGrantUserId(e.target.value)}
            data-testid="cf-admin-jury-grant-uid" />
          <button className="jp-btn jp-btn-primary jp-btn-sm" disabled={busy || !grantUserId.trim()}
            onClick={grant} data-testid="cf-admin-jury-grant-submit"
            style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <UserPlus size={14} /> {t('crowdfunding.admin_jury_appoint_btn', { defaultValue: 'Nommer' })}
          </button>
        </div>
      </div>

      {/* List */}
      {loading && <div className="text-sm text-center py-6" style={{ color: 'var(--jp-text-muted)' }}>{t('common.loading', { defaultValue: 'Chargement…' })}</div>}
      {!loading && members.length === 0 && (
        <div className="text-sm text-center py-6" style={{ color: 'var(--jp-text-muted)' }}>
          {t('crowdfunding.admin_jury_empty', { defaultValue: 'Aucun juré pour le moment.' })}
        </div>
      )}
      <ul className="space-y-2">
        {members.map(m => {
          const revoked = !!m.revoked_at;
          const certHref = `${API}/api/crowdfunding/jury/certificate/${m.user_id}.png`;
          return (
            <li key={m.jury_id || m.user_id} className="jp-card p-3" data-testid={`cf-admin-jury-${m.user_id}`}
              style={{ opacity: revoked ? 0.55 : 1 }}>
              <div className="flex items-center gap-3 flex-wrap">
                {m.avatar ? (
                  <img src={m.avatar} alt="" className="w-10 h-10 rounded-full object-cover" />
                ) : (
                  <div className="w-10 h-10 rounded-full bg-amber-100 flex items-center justify-center text-amber-700 font-bold">
                    {(m.first_name?.[0] || m.username?.[0] || '?').toUpperCase()}
                  </div>
                )}
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-sm flex items-center gap-2 flex-wrap">
                    {m.first_name} {m.last_name}
                    <span className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>@{m.username}</span>
                    {revoked && <span className="jp-badge jp-badge-error" style={{ fontSize: 10 }}>
                      {t('crowdfunding.admin_jury_status_revoked', { defaultValue: 'Révoqué' })}
                    </span>}
                  </div>
                  <div className="text-[11px] flex items-center gap-3 flex-wrap" style={{ color: 'var(--jp-text-muted)' }}>
                    <span>{t('crowdfunding.admin_jury_cycle', { defaultValue: 'Cycle #{{n}}', n: m.awarded_cycle_number })}</span>
                    <span>{t('crowdfunding.admin_jury_wins', { defaultValue: 'Victoires : {{n}}', n: m.total_wins_at_grant })}</span>
                    <span>{t('crowdfunding.admin_jury_granted', { defaultValue: 'Octroyé le {{d}}', d: fmtDate(m.granted_at) })}</span>
                    {m.expires_at_cycle_number != null && (
                      <span>{t('crowdfunding.admin_jury_expires', { defaultValue: 'Expire au cycle #{{n}}', n: m.expires_at_cycle_number })}</span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                  <a href={`${API}/api/crowdfunding/jury/certificate/${m.user_id}.svg?lang=fr`} target="_blank" rel="noreferrer"
                    className="jp-btn jp-btn-ghost jp-btn-sm" data-testid={`cf-admin-jury-cert-svg-${m.user_id}`}
                    style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <FileText size={14} /> {t('crowdfunding.admin_jury_certificate_svg', { defaultValue: 'Certificat SVG' })}
                  </a>
                  <a href={certHref} target="_blank" rel="noreferrer"
                    className="jp-btn jp-btn-ghost jp-btn-sm" data-testid={`cf-admin-jury-cert-${m.user_id}`}
                    style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                    <Download size={14} /> PNG
                  </a>
                  {!revoked && (
                    <button className="jp-btn jp-btn-ghost jp-btn-sm" onClick={() => regenCert(m.user_id)} disabled={busy}
                      style={{ display: 'flex', alignItems: 'center', gap: 4 }}
                      data-testid={`cf-admin-jury-regen-${m.user_id}`}>
                      <ArrowsClockwise size={14} /> {t('crowdfunding.admin_jury_regen_cert', { defaultValue: 'Régénérer' })}
                    </button>
                  )}
                  {!revoked && (
                    <button className="jp-btn jp-btn-ghost jp-btn-sm" onClick={() => revoke(m.user_id)} disabled={busy}
                      style={{ color: 'var(--jp-error)', display: 'flex', alignItems: 'center', gap: 4 }}
                      data-testid={`cf-admin-jury-revoke-${m.user_id}`}>
                      <Trash size={14} /> {t('crowdfunding.admin_jury_revoke_btn', { defaultValue: 'Révoquer' })}
                    </button>
                  )}
                </div>
              </div>
              {m.revoke_reason && (
                <div className="text-[11px] mt-2" style={{ color: 'var(--jp-text-muted)' }}>
                  {t('crowdfunding.admin_jury_revoke_reason', { defaultValue: 'Motif : {{r}}', r: m.revoke_reason })}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
