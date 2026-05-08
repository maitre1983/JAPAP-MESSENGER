/**
 * SettingsPage — Facebook-style settings hub for JAPAP.
 *
 * Sections: Compte (nom/username/bio), Sécurité (password + 2FA placeholder),
 * Confidentialité (privacy toggles), Notifications (per-event opt-ins).
 * Each section is a collapsible card — matches mobile navigation pattern.
 */
import { useState, useEffect } from 'react';
import { useNavigate, useSearchParams, Link } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  ArrowLeft, User, Lock, ShieldCheck, Bell, CaretRight, FloppyDisk,
  Eye, EyeSlash, Check, Envelope, UserCircle, UsersThree, LockKey,
  DeviceMobile,
} from '@phosphor-icons/react';
import { useAuth } from '@/context/AuthContext';

const API = process.env.REACT_APP_BACKEND_URL;

// ────────────────────────── Account section ──────────────────────────

function AccountSection({ user, refreshUser }) {
  const [form, setForm] = useState({
    first_name: user?.first_name || '',
    last_name: user?.last_name || '',
    username: user?.username || '',
    about: user?.about || '',
  });
  const [saving, setSaving] = useState(false);

  const save = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      await axios.put(`${API}/api/users/profile`, form, { withCredentials: true });
      await refreshUser();
      toast.success('Profil mis à jour');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    } finally { setSaving(false); }
  };

  return (
    <form onSubmit={save} className="space-y-4" data-testid="settings-account-form">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="jp-label">Prénom</label>
          <input data-testid="settings-first-name"
            value={form.first_name}
            onChange={(e) => setForm((f) => ({ ...f, first_name: e.target.value }))}
            className="jp-input text-sm" />
        </div>
        <div>
          <label className="jp-label">Nom</label>
          <input data-testid="settings-last-name"
            value={form.last_name}
            onChange={(e) => setForm((f) => ({ ...f, last_name: e.target.value }))}
            className="jp-input text-sm" />
        </div>
      </div>
      <div>
        <label className="jp-label">Nom d'utilisateur</label>
        <input data-testid="settings-username" value={form.username}
          onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
          className="jp-input text-sm" placeholder="unique_handle" />
      </div>
      <div>
        <label className="jp-label">Bio</label>
        <textarea data-testid="settings-about" rows={3}
          value={form.about}
          onChange={(e) => setForm((f) => ({ ...f, about: e.target.value }))}
          className="jp-input text-sm resize-none" placeholder="Quelques mots sur vous…" />
      </div>
      <button type="submit" disabled={saving}
        className="jp-btn jp-btn-primary" data-testid="settings-account-save">
        {saving ? <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <FloppyDisk size={16} />}
        Enregistrer
      </button>
    </form>
  );
}

// ────────────────────────── Security section ──────────────────────────

function SecuritySection() {
  const { t } = useTranslation();
  const [pw, setPw] = useState({ current: '', next: '', confirm: '' });
  const [showPw, setShowPw] = useState(false);
  const [saving, setSaving] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (pw.next !== pw.confirm) return toast.error('Les mots de passe ne correspondent pas');
    if (pw.next.length < 8) return toast.error('Le mot de passe doit contenir au moins 8 caractères');
    setSaving(true);
    try {
      await axios.post(`${API}/api/users/me/change-password`,
        { current_password: pw.current, new_password: pw.next },
        { withCredentials: true });
      toast.success('Mot de passe modifié');
      setPw({ current: '', next: '', confirm: '' });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    } finally { setSaving(false); }
  };

  return (
    <form onSubmit={submit} className="space-y-4" data-testid="settings-security-form">
      <div>
        <label className="jp-label">Mot de passe actuel</label>
        <div className="relative">
          <input data-testid="settings-pw-current" type={showPw ? 'text' : 'password'}
            value={pw.current} onChange={(e) => setPw((p) => ({ ...p, current: e.target.value }))}
            className="jp-input text-sm pr-10" />
          <button type="button" onClick={() => setShowPw(!showPw)}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400">
            {showPw ? <EyeSlash size={16} /> : <Eye size={16} />}
          </button>
        </div>
      </div>
      <div>
        <label className="jp-label">Nouveau mot de passe</label>
        <input data-testid="settings-pw-next" type={showPw ? 'text' : 'password'} value={pw.next}
          onChange={(e) => setPw((p) => ({ ...p, next: e.target.value }))}
          className="jp-input text-sm" placeholder="8 caractères min." />
      </div>
      <div>
        <label className="jp-label">Confirmer le nouveau mot de passe</label>
        <input data-testid="settings-pw-confirm" type={showPw ? 'text' : 'password'} value={pw.confirm}
          onChange={(e) => setPw((p) => ({ ...p, confirm: e.target.value }))}
          className="jp-input text-sm" />
      </div>
      <button type="submit" disabled={saving || !pw.current || !pw.next}
        className="jp-btn jp-btn-primary disabled:opacity-50"
        data-testid="settings-pw-submit">
        <LockKey size={16} /> {saving ? t('settings.mise_a_jour') : 'Changer le mot de passe'}
      </button>
      <div className="text-[11px] font-['Manrope'] pt-2 border-t opacity-60"
        style={{ borderColor: 'var(--jp-border)' }}>
        Authentification à deux facteurs (2FA) · <em>à venir</em>
      </div>

      {/* iter82 — Active sessions + logout-all + suspicious activity log */}
      <ActiveSessionsPanel />
    </form>
  );
}

// ──────────────────────── Active sessions panel ─────────────────────────

function ActiveSessionsPanel() {
  const [sessions, setSessions] = useState(null);
  const [events, setEvents] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const [sRes, eRes] = await Promise.all([
        axios.get(`${API}/api/security/sessions`, { withCredentials: true }),
        axios.get(`${API}/api/security/events?limit=20`, { withCredentials: true }),
      ]);
      setSessions(sRes.data.sessions || []);
      setEvents(eRes.data.events || []);
    } catch (e) {
      toast.error('Chargement sessions : ' + (e.response?.data?.detail || e.message));
    }
  };
  // Initial load only — no polling to keep the panel light.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, []);

  const revokeOne = async (sid) => {
    if (!window.confirm('Déconnecter cet appareil ?')) return;
    setBusy(true);
    try {
      await axios.delete(`${API}/api/security/sessions/${sid}`, { withCredentials: true });
      toast.success('Appareil déconnecté');
      load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
    } finally { setBusy(false); }
  };

  const logoutAll = async () => {
    if (!window.confirm('Déconnecter TOUS les appareils ? Vous devrez vous reconnecter.')) return;
    setBusy(true);
    try {
      await axios.post(`${API}/api/security/logout-all`, {}, { withCredentials: true });
      toast.success('Tous les appareils ont été déconnectés');
      setTimeout(() => { window.location.href = '/login'; }, 800);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur');
      setBusy(false);
    }
  };

  return (
    <div className="pt-4 mt-4 border-t space-y-3"
         style={{ borderColor: 'var(--jp-border)' }} data-testid="active-sessions-panel">
      <div className="flex items-center justify-between">
        <div>
          <div className="font-bold text-sm">Appareils actifs</div>
          <div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
            Gérez les connexions en cours à votre compte.
          </div>
        </div>
        <button type="button" onClick={logoutAll} disabled={busy}
          className="jp-btn jp-btn-ghost jp-btn-sm"
          style={{ color: 'var(--jp-error)' }}
          data-testid="logout-all-devices-btn">
          Tout déconnecter
        </button>
      </div>
      {sessions === null ? (
        <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>
      ) : sessions.length === 0 ? (
        <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>Aucune session active.</div>
      ) : (
        <div className="space-y-2">
          {sessions.map((s) => (
            <div key={s.session_id}
                 className="flex items-center justify-between p-3 rounded-lg"
                 style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}
                 data-testid={`session-row-${s.session_id}`}>
              <div className="flex-1 min-w-0">
                <div className="text-xs font-semibold truncate">
                  {s.user_agent?.slice(0, 60) || 'Appareil inconnu'}
                </div>
                <div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>
                  IP {s.ip_address || '?'} · connecté le {new Date(s.created_at).toLocaleString('fr-FR')}
                </div>
              </div>
              <button type="button" onClick={() => revokeOne(s.session_id)}
                      className="jp-btn jp-btn-ghost jp-btn-sm"
                      style={{ color: 'var(--jp-error)' }}
                      data-testid={`revoke-session-${s.session_id}`}>
                Fermer
              </button>
            </div>
          ))}
        </div>
      )}

      <details className="text-xs pt-2" style={{ color: 'var(--jp-text-muted)' }}>
        <summary className="cursor-pointer font-semibold" data-testid="security-events-toggle">
          Activité récente ({events?.length || 0})
        </summary>
        {events && events.length > 0 && (
          <ul className="mt-2 space-y-1 font-mono text-[10px]">
            {events.map((e, i) => (
              <li key={i} className="flex items-start gap-2">
                <span className={`inline-block min-w-[60px] font-bold ${
                  e.severity === 'critical' ? 'text-red-500' :
                  e.severity === 'warning' ? 'text-amber-500' :
                  'opacity-60'
                }`}>
                  {e.severity}
                </span>
                <span className="flex-1">
                  {e.event_type} · {e.ip_address || '?'} ·{' '}
                  {new Date(e.created_at).toLocaleString('fr-FR')}
                </span>
              </li>
            ))}
          </ul>
        )}
      </details>
    </div>
  );
}

// ─────────────────────── Privacy + Notif sections ───────────────────────

function Toggle({ checked, onChange, label, description, testid }) {
  return (
    <label className="flex items-start gap-3 py-2 cursor-pointer" data-testid={testid}>
      <input type="checkbox" checked={!!checked} onChange={(e) => onChange(e.target.checked)}
        className="w-5 h-5 mt-0.5 accent-[var(--jp-primary)]" />
      <span className="flex-1">
        <span className="block text-sm font-['Manrope'] font-semibold" style={{ color: 'var(--jp-text)' }}>{label}</span>
        {description && (
          <span className="block text-[11px] font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
            {description}
          </span>
        )}
      </span>
    </label>
  );
}

function Radio({ name, value, checked, onChange, label, description, testid }) {
  return (
    <label className="flex items-start gap-3 py-2 cursor-pointer" data-testid={testid}>
      <input type="radio" name={name} value={value} checked={checked} onChange={() => onChange(value)}
        className="w-4 h-4 mt-1 accent-[var(--jp-primary)]" />
      <span className="flex-1">
        <span className="block text-sm font-['Manrope'] font-semibold" style={{ color: 'var(--jp-text)' }}>{label}</span>
        {description && (
          <span className="block text-[11px] font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
            {description}
          </span>
        )}
      </span>
    </label>
  );
}

function PrivacySection() {
  const { t } = useTranslation();
  const [privacy, setPrivacy] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    axios.get(`${API}/api/users/me/privacy`, { withCredentials: true })
      .then(({ data }) => setPrivacy(data))
      .catch(() => setPrivacy({}));
  }, []);

  const update = async (patch) => {
    setSaving(true);
    const next = { ...privacy, ...patch };
    setPrivacy(next);  // optimistic
    try {
      const { data } = await axios.put(`${API}/api/users/me/privacy`, patch, { withCredentials: true });
      setPrivacy(data);
      toast.success('Confidentialité mise à jour');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    } finally { setSaving(false); }
  };

  if (!privacy) return <p className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</p>;

  return (
    <div className="space-y-5" data-testid="settings-privacy-form">
      <section>
        <h4 className="font-['Outfit'] font-bold text-sm mb-2"
          style={{ color: 'var(--jp-text)' }}>{t('settings.visibilite_du_compte')}</h4>
        <Radio name="av" value="public" checked={privacy.account_visibility === 'public'}
          onChange={(v) => update({ account_visibility: v })}
          testid="privacy-account-public"
          label="Compte public"
          description="Tout le monde peut voir votre profil et vos publications publiques." />
        <Radio name="av" value="private" checked={privacy.account_visibility === 'private'}
          onChange={(v) => update({ account_visibility: v })}
          testid="privacy-account-private"
          label="Compte privé"
          description="Seuls vos abonnés approuvés voient vos publications." />
      </section>

      <section className="pt-4 border-t" style={{ borderColor: 'var(--jp-border)' }}>
        <h4 className="font-['Outfit'] font-bold text-sm mb-2"
          style={{ color: 'var(--jp-text)' }}>{t('settings.demandes_d_abonnement')}</h4>
        <Radio name="fm" value="auto" checked={privacy.follow_mode === 'auto'}
          onChange={(v) => update({ follow_mode: v })}
          testid="privacy-follow-auto"
          label="Automatique"
          description="Tout le monde peut vous suivre immédiatement." />
        <Radio name="fm" value="approval" checked={privacy.follow_mode === 'approval'}
          onChange={(v) => update({ follow_mode: v })}
          testid="privacy-follow-approval"
          label="Validation manuelle"
          description="Vous approuvez ou refusez chaque nouvelle demande." />
      </section>

      <section className="pt-4 border-t" style={{ borderColor: 'var(--jp-border)' }}>
        <h4 className="font-['Outfit'] font-bold text-sm mb-2"
          style={{ color: 'var(--jp-text)' }}>{t('settings.qui_peut_voir_mes_publications_par')}</h4>
        <Radio name="pv" value="public" checked={privacy.post_visibility_default === 'public'}
          onChange={(v) => update({ post_visibility_default: v })}
          testid="privacy-post-public" label="Public"
          description="Visible par tous les utilisateurs JAPAP." />
        <Radio name="pv" value="friends" checked={privacy.post_visibility_default === 'friends'}
          onChange={(v) => update({ post_visibility_default: v })}
          testid="privacy-post-friends" label="Amis (abonnés)"
          description="Uniquement les personnes qui vous suivent." />
        <Radio name="pv" value="only_me" checked={privacy.post_visibility_default === 'only_me'}
          onChange={(v) => update({ post_visibility_default: v })}
          testid="privacy-post-only-me" label="Moi uniquement"
          description="Journal privé — seulement visible par vous." />
      </section>

      {saving && <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>Enregistrement…</p>}
    </div>
  );
}

function NotificationsSection() {
  const [privacy, setPrivacy] = useState(null);

  useEffect(() => {
    axios.get(`${API}/api/users/me/privacy`, { withCredentials: true })
      .then(({ data }) => setPrivacy(data))
      .catch(() => setPrivacy({}));
  }, []);

  const updatePref = async (key, val) => {
    const next = { ...privacy, [key]: val };
    setPrivacy(next);
    try {
      await axios.put(`${API}/api/users/me/privacy`, { [key]: val }, { withCredentials: true });
    } catch (err) {
      setPrivacy(privacy);  // rollback
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  if (!privacy) return <p className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</p>;

  return (
    <div className="space-y-1" data-testid="settings-notif-form">
      <Toggle checked={privacy.notify_follow} onChange={(v) => updatePref('notify_follow', v)}
        testid="notif-follow" label="Nouveaux abonnés / demandes"
        description="Recevez une notification quand quelqu'un vous suit ou demande à vous suivre." />
      <Toggle checked={privacy.notify_follow_accept} onChange={(v) => updatePref('notify_follow_accept', v)}
        testid="notif-follow-accept" label="Demandes acceptées"
        description="Soyez notifié quand votre demande d'abonnement est acceptée." />
      <Toggle checked={privacy.notify_likes} onChange={(v) => updatePref('notify_likes', v)}
        testid="notif-likes" label="Likes sur mes publications" />
      <Toggle checked={privacy.notify_comments} onChange={(v) => updatePref('notify_comments', v)}
        testid="notif-comments" label="Commentaires sur mes publications" />
      <Toggle checked={privacy.notify_messages} onChange={(v) => updatePref('notify_messages', v)}
        testid="notif-messages" label="Nouveaux messages" />
    </div>
  );
}

// ────────────────────────── Devices section ──────────────────────────
// iter147 — "Mes appareils connectés" — uses GET /api/auth/devices
// (created in iter146) + POST /api/auth/devices/untrust to drop a
// trusted device (forcing re-auth from that fingerprint).

function DevicesSection() {
  const [devices, setDevices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [actingOn, setActingOn] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/api/auth/devices`, { withCredentials: true });
      setDevices(data?.devices || []);
    } catch (err) {
      toast.error('Impossible de charger les appareils');
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const untrust = async (fingerprint) => {
    setActingOn(fingerprint);
    try {
      await axios.post(`${API}/api/auth/devices/untrust`,
        { fingerprint }, { withCredentials: true });
      await load();
      toast.success('Appareil retiré des appareils de confiance');
    } catch (_e) {
      toast.error("Impossible de retirer l'appareil");
    } finally { setActingOn(null); }
  };

  if (loading) {
    return (
      <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}
         data-testid="settings-devices-loading">
        Chargement…
      </p>
    );
  }

  return (
    <div className="space-y-3" data-testid="settings-devices-list">
      <p className="text-xs font-['Manrope'] mb-2" style={{ color: 'var(--jp-text-muted)' }}>
        Les appareils de confiance restent connectés jusqu'à 90 jours sans réauthentification.
        Retire un appareil que tu ne reconnais pas pour invalider sa session.
      </p>
      {devices.length === 0 && (
        <p className="text-sm font-['Manrope'] text-center py-6"
           style={{ color: 'var(--jp-text-muted)' }} data-testid="settings-devices-empty">
          Aucun appareil enregistré pour l'instant.
        </p>
      )}
      {devices.map((d) => (
        <div key={d.fingerprint}
             data-testid={`settings-device-${d.fingerprint.slice(0, 8)}`}
             className="jp-card p-4 flex items-start gap-3"
             style={d.is_current ? { borderLeft: '3px solid var(--jp-primary)' } : {}}>
          <DeviceMobile size={28} weight="duotone"
            style={{ color: d.is_trusted ? '#22c55e' : 'var(--jp-text-muted)', flexShrink: 0 }} />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-['Manrope'] font-bold flex items-center gap-2"
               style={{ color: 'var(--jp-text)' }}>
              {summariseUserAgent(d.last_user_agent)}
              {d.is_current && (
                <span className="jp-badge jp-badge-primary text-[9px]"
                      data-testid={`settings-device-${d.fingerprint.slice(0, 8)}-current`}>
                  Cet appareil
                </span>
              )}
              {d.is_trusted && (
                <span className="text-[9px] px-2 py-0.5 rounded-full"
                      style={{ background: 'rgba(34,197,94,0.15)', color: '#16a34a' }}
                      data-testid={`settings-device-${d.fingerprint.slice(0, 8)}-trusted`}>
                  ✓ De confiance
                </span>
              )}
            </p>
            <p className="text-[11px] font-['Manrope'] truncate"
               style={{ color: 'var(--jp-text-muted)' }}>
              {d.last_ip || '—'} · {d.successful_logins_count} connexion{d.successful_logins_count > 1 ? 's' : ''}
            </p>
            <p className="text-[10px] font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
              Dernière utilisation : {formatRelative(d.last_seen_at)}
            </p>
          </div>
          {d.is_trusted && (
            <button
              type="button"
              onClick={() => untrust(d.fingerprint)}
              disabled={actingOn === d.fingerprint}
              data-testid={`settings-device-${d.fingerprint.slice(0, 8)}-untrust`}
              className="flex-shrink-0 text-xs font-['Manrope'] font-semibold px-3 py-1.5 rounded-full transition-colors disabled:opacity-50"
              style={{ background: 'rgba(224,28,46,0.1)', color: '#E01C2E' }}
            >
              {actingOn === d.fingerprint ? '…' : 'Retirer'}
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

function summariseUserAgent(ua) {
  if (!ua) return 'Appareil inconnu';
  // Cheap UA → "Browser sur OS" extraction. Avoids pulling a 30 KB UA-parser
  // dependency for what is, fundamentally, a list label.
  const u = String(ua);
  let browser = 'Navigateur';
  if (/edg\b/i.test(u)) browser = 'Edge';
  else if (/opr|opera/i.test(u)) browser = 'Opera';
  else if (/chrome/i.test(u)) browser = 'Chrome';
  else if (/safari/i.test(u)) browser = 'Safari';
  else if (/firefox/i.test(u)) browser = 'Firefox';
  let os = '';
  if (/android/i.test(u)) os = 'Android';
  else if (/iphone|ipad|ios/i.test(u)) os = 'iOS';
  else if (/mac os|macintosh/i.test(u)) os = 'macOS';
  else if (/windows/i.test(u)) os = 'Windows';
  else if (/linux/i.test(u)) os = 'Linux';
  return os ? `${browser} sur ${os}` : browser;
}

function formatRelative(iso) {
  if (!iso) return '—';
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return 'à l\'instant';
  if (diff < 3600) return `il y a ${Math.floor(diff / 60)} min`;
  if (diff < 86400) return `il y a ${Math.floor(diff / 3600)} h`;
  return `il y a ${Math.floor(diff / 86400)} j`;
}

// ────────────────────────── Root component ──────────────────────────

const getSections = (t) => ([
  { id: 'account',  icon: User,         label: 'Mon compte',          description: 'Nom, pseudo, bio' },
  { id: 'security', icon: Lock,         label: t('settings.securite'),            description: 'Mot de passe, 2FA' },
  { id: 'devices',  icon: DeviceMobile, label: 'Mes appareils',       description: t('settings.sessions_connectees_appareils_de_co') },
  { id: 'privacy',  icon: ShieldCheck,  label: t('settings.confidentialite'),     description: t('settings.compte_abonnes_publications') },
  { id: 'notif',    icon: Bell,         label: 'Notifications',       description: 'Likes, commentaires, messages' },
]);

export default function SettingsPage() {
  const { t } = useTranslation();
  const SECTIONS = getSections(t);
  const { user, refreshUser } = useAuth();
  const navigate = useNavigate();
  const [sp, setSp] = useSearchParams();
  const active = sp.get('s') || '';  // '' = index
  const [pendingCount, setPendingCount] = useState(0);

  useEffect(() => {
    axios.get(`${API}/api/users/me/follow-requests?limit=1`, { withCredentials: true })
      .then(({ data }) => setPendingCount(data.total || 0))
      .catch(() => {});
  }, []);

  if (!user) return null;

  const renderSection = () => {
    switch (active) {
      case 'account':  return <AccountSection user={user} refreshUser={refreshUser} />;
      case 'security': return <SecuritySection />;
      case 'devices':  return <DevicesSection />;
      case 'privacy':  return <PrivacySection />;
      case 'notif':    return <NotificationsSection />;
      default: return null;
    }
  };

  return (
    <div className="max-w-xl mx-auto px-4 py-4" data-testid="settings-page">
      <button onClick={() => (active ? setSp({}) : navigate(-1))}
        data-testid="settings-back"
        className="inline-flex items-center gap-1.5 text-sm font-['Manrope'] font-semibold mb-3"
        style={{ color: 'var(--jp-text-secondary)' }}>
        <ArrowLeft size={16} /> Retour
      </button>

      <h1 className="font-['Outfit'] text-2xl font-extrabold mb-4"
        style={{ color: 'var(--jp-text)' }} data-testid="settings-title">
        {active ? SECTIONS.find((s) => s.id === active)?.label : t('settings.parametres')}
      </h1>

      {!active ? (
        <ul className="space-y-2" data-testid="settings-menu">
          {/* Shortcut to the follow requests inbox, only shown when non-empty */}
          {pendingCount > 0 && (
            <li>
              <Link to="/settings/requests" data-testid="settings-menu-requests"
                className="jp-card flex items-center gap-3 p-4 transition-colors"
                style={{ borderLeft: '3px solid var(--jp-primary)' }}>
                <UsersThree size={22} weight="fill" style={{ color: 'var(--jp-primary)' }} />
                <div className="flex-1">
                  <p className="text-sm font-['Manrope'] font-bold" style={{ color: 'var(--jp-text)' }}>
                    Demandes d'abonnement
                  </p>
                  <p className="text-[11px] font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
                    {pendingCount} en attente
                  </p>
                </div>
                <span className="jp-badge jp-badge-primary" data-testid="settings-requests-badge">
                  {pendingCount}
                </span>
              </Link>
            </li>
          )}
          {SECTIONS.map((s) => (
            <li key={s.id}>
              <button onClick={() => setSp({ s: s.id })}
                data-testid={`settings-menu-${s.id}`}
                className="w-full jp-card flex items-center gap-3 p-4 transition-colors hover:bg-black/5">
                <s.icon size={22} weight="duotone" style={{ color: 'var(--jp-primary)' }} />
                <div className="flex-1 text-left">
                  <p className="text-sm font-['Manrope'] font-bold" style={{ color: 'var(--jp-text)' }}>
                    {s.label}
                  </p>
                  <p className="text-[11px] font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
                    {s.description}
                  </p>
                </div>
                <CaretRight size={16} style={{ color: 'var(--jp-text-muted)' }} />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="jp-card p-5">{renderSection()}</div>
      )}
    </div>
  );
}
