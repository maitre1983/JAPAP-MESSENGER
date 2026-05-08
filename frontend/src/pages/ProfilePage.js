import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import { useTranslation } from 'react-i18next';
import LanguageSwitcher from '@/components/LanguageSwitcher';
import axios from 'axios';
import { EnvelopeSimple, Phone, Globe, Calendar, PencilSimple, FloppyDisk, Gift, Crown, X, Translate, WifiHigh as Wifi, ListChecks, PhoneList, Camera, UserCircle, Gear, UsersThree, Headset } from '@phosphor-icons/react';
import { useTasks } from '@/context/TasksContext';
import PushNotificationsToggle from '@/components/PushNotificationsToggle';
import { SUPPORTED_LANGUAGES as LANGUAGES } from '@/constants/languages';
import ImageCropper from '@/components/ImageCropper';
import MediaFilterEditor from '@/components/media/MediaFilterEditor';
import PhotoGallery from '@/components/profile/PhotoGallery';
import { uploadAvatar, uploadCover } from '@/utils/imageUpload';
import TipSettingsCard from '@/components/profile/TipSettingsCard';
import DisplayCurrencySelector from '@/components/profile/DisplayCurrencySelector';
import ScholarshipDigestPref from '@/components/profile/ScholarshipDigestPref';
import KycVerifiedBadge from '@/components/KycVerifiedBadge';
import { toast } from 'sonner';

const API = process.env.REACT_APP_BACKEND_URL;

export default function ProfilePage() {
  const { user, refreshUser } = useAuth();
  const { t, i18n } = useTranslation();
  const { pending: pendingTasks } = useTasks();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    first_name: user?.first_name || '',
    last_name: user?.last_name || '',
    phone_number: user?.phone_number || '',
    about: user?.about || '',
    gender: user?.gender || '',
    birthday: user?.birthday || '',
    country: user?.country || '',
    language: user?.language || 'en',
  });
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [prefLang, setPrefLang] = useState(user?.preferred_lang || '');
  const [savingLang, setSavingLang] = useState(false);
  // Cropper modal: 'avatar' | 'cover' | null
  const [cropperFor, setCropperFor] = useState(null);
  // iter147 — pre-crop filter state. When the user taps "edit avatar/cover"
  // we open the unified MediaFilterEditor first so they can apply a photo
  // filter, then we hand the filtered file to the cropper for framing.
  const [filterEditor, setFilterEditor] = useState({ open: false, target: null });
  const [filteredFile, setFilteredFile] = useState(null);
  // Pending follow requests — surfaced as a dedicated CTA on the profile
  // header so the user never misses approval-gated incoming follows.
  const [pendingFollowRequests, setPendingFollowRequests] = useState(0);
  // Social layer: we re-fetch the profile row (includes counts + is_following)
  // on mount so the numbers are always fresh even when the bootstrap `user`
  // in AuthContext is stale (e.g. after a follow from a friend-profile page).
  const [social, setSocial] = useState({
    followers_count: 0, following_count: 0, posts_count: 0,
    cover_image: null, cover_position_y: 50,
  });

  useEffect(() => {
    if (!user?.user_id) return;
    let ignore = false;
    axios.get(`${API}/api/users/profile/${user.user_id}`, { withCredentials: true })
      .then(({ data }) => {
        if (ignore) return;
        setSocial({
          followers_count: data.followers_count || 0,
          following_count: data.following_count || 0,
          posts_count: data.posts_count || 0,
          cover_image: data.cover_image || null,
          cover_position_y: data.cover_position_y ?? 50,
        });
      })
      .catch(() => { /* keep defaults */ });
    // Separate call for pending follow requests — small endpoint, gated by
    // the UI visibility of the badge only so it's cheap.
    axios.get(`${API}/api/users/me/follow-requests?limit=1`, { withCredentials: true })
      .then(({ data }) => setPendingFollowRequests(data.total || 0))
      .catch(() => {});
    return () => { ignore = true; };
  }, [user?.user_id]);

  const handleAvatarUpload = async (blob) => {
    try {
      await uploadAvatar(blob);
      await refreshUser();
      toast.success('Photo de profil mise à jour');
      setCropperFor(null);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Échec du téléversement');
    }
  };

  const handleCoverUpload = async (blob, { positionY }) => {
    try {
      const updated = await uploadCover(blob, positionY);
      setSocial((s) => ({
        ...s,
        cover_image: updated.cover_image,
        cover_position_y: updated.cover_position_y ?? positionY,
      }));
      await refreshUser();
      toast.success('Photo de couverture mise à jour');
      setCropperFor(null);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Échec du téléversement');
    }
  };

  // Auto-translate list = Interface language list (+ "Disabled" option).
  // Single source of truth, same order, same labels → zero drift with
  // the LanguageSwitcher component.
  const SUPPORTED_LANGS = [
    { code: '', label: t('profile.auto_translate_disabled') },
    ...LANGUAGES.map((l) => ({
      code: l.code,
      label: `${l.flag} ${l.native}`,
    })),
  ];

  const handleSaveLang = async (lang) => {
    setSavingLang(true);
    try {
      await axios.put(`${API}/api/auth/preferences`, { preferred_lang: lang }, { withCredentials: true });
      setPrefLang(lang);
      // iter237y — Apply language immediately so the user sees the UI
      // switch without needing to reload. `lang` empty means auto-translate
      // disabled; we keep the current i18n locale in that case.
      try {
        if (lang) {
          await i18n.changeLanguage(lang);
          try { localStorage.setItem('japap_ui_lang', lang); } catch (_) {}
        }
      } catch (_) { /* fall back to refreshUser-driven sync */ }
      await refreshUser();
      setMessage(lang
        ? `Auto-traduction activée : ${SUPPORTED_LANGS.find(l => l.code === lang)?.label || lang}`
        : 'Auto-traduction désactivée');
    } catch (e) {
      setMessage(e.response?.data?.detail || 'Erreur lors de la sauvegarde');
    } finally {
      setSavingLang(false);
    }
  };

  const handleSave = async (e) => {
    e.preventDefault();
    setSaving(true); setMessage('');
    try {
      await axios.put(`${API}/api/users/profile`, form, { withCredentials: true });
      setMessage('Profil mis a jour avec succes');
      setEditing(false);
      refreshUser();
    } catch (err) { setMessage(err.response?.data?.detail || 'Erreur'); }
    finally { setSaving(false); }
  };

  if (!user) return null;

  return (
    <div className="px-3 py-4 sm:p-6 max-w-3xl mx-auto jp-animate-fadeIn" data-testid="profile-page">
      <div className="jp-card-elevated overflow-hidden">
        {/* ══ Cover photo — dynamic, upload + drag-position ══ */}
        <div className="h-40 sm:h-56 relative overflow-hidden group" data-testid="profile-cover">
          {social.cover_image ? (
            <img src={social.cover_image.startsWith('http') ? social.cover_image : `${API}${social.cover_image}`}
              alt="" className="w-full h-full object-cover"
              style={{ objectPosition: `center ${social.cover_position_y}%` }}
              data-testid="profile-cover-image" />
          ) : (
            <div className="absolute inset-0 flex items-center justify-center"
              style={{ background: 'linear-gradient(135deg, #0B0542 0%, #0F056B 60%, #E01C2E 100%)' }}>
              <div className="text-center text-white/85 font-['Manrope'] text-xs sm:text-sm px-4">
                <Camera size={28} weight="duotone" className="mx-auto mb-1.5 opacity-80" />
                Ajoutez une couverture pour personnaliser votre profil
              </div>
            </div>
          )}
          {/* Subtle overlay on hover for affordance */}
          <div className="absolute inset-0 bg-gradient-to-t from-black/40 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none" />
          {/* Edit cover CTA — visible, labelled */}
          <button onClick={() => setFilterEditor({ open: true, target: 'cover' })} data-testid="edit-cover-button"
            aria-label={t('profile.modifier_la_photo_de_couverture')}
            className="absolute bottom-3 right-3 flex items-center gap-1.5 px-3.5 py-2 rounded-full backdrop-blur-md transition-all hover:scale-105 active:scale-95 shadow-lg font-['Manrope'] font-semibold text-xs"
            style={{ background: 'rgba(0,0,0,0.7)', color: '#fff', border: '1px solid rgba(255,255,255,0.2)' }}>
            <Camera size={14} weight="fill" />
            <span className="hidden sm:inline">
              {social.cover_image ? 'Modifier la couverture' : 'Ajouter une couverture'}
            </span>
            <span className="sm:hidden">
              {social.cover_image ? 'Modifier' : 'Ajouter'}
            </span>
          </button>
          <div className="absolute -bottom-14 left-6 sm:left-8">
            <div className="relative">
              <div className="jp-avatar jp-avatar-xxl"
                style={{ background: 'var(--jp-surface)', border: '4px solid var(--jp-surface)', color: 'var(--jp-primary)' }}>
                {user.avatar
                  ? <img src={user.avatar.startsWith('http') ? user.avatar : `${API}${user.avatar}`} alt="" />
                  : (
                    <div className="w-full h-full flex flex-col items-center justify-center gap-0.5">
                      <Camera size={22} weight="duotone" className="opacity-60" />
                      <span className="text-[9px] font-['Manrope'] opacity-70 font-semibold">Ajouter</span>
                    </div>
                  )}
              </div>
              <button onClick={() => setFilterEditor({ open: true, target: 'avatar' })} data-testid="edit-avatar-button"
                aria-label={user.avatar ? 'Modifier la photo de profil' : 'Ajouter une photo de profil'}
                className="absolute -bottom-0.5 -right-0.5 p-2.5 rounded-full shadow-lg transition-all hover:scale-110 active:scale-95"
                style={{ background: 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)', color: '#fff', border: '3px solid var(--jp-surface)' }}>
                <Camera size={14} weight="fill" />
              </button>
            </div>
          </div>
        </div>

        <div className="pt-18 px-6 sm:px-8 pb-8" style={{ paddingTop: '72px' }}>
          <div className="flex justify-between items-start mb-6">
            <div>
              <h2 className="font-['Outfit'] text-2xl font-bold" style={{ color: 'var(--jp-text)' }} data-testid="profile-name">
                {user.first_name} {user.last_name}
              </h2>
              <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>@{user.username}</p>
              <div className="flex items-center gap-2 mt-2 flex-wrap">
                <KycVerifiedBadge verified={user.kyc_verified} />
                {user.is_verified && !user.kyc_verified && <span className="jp-badge jp-badge-primary">{t('profile.verified')}</span>}
                {user.is_pro && <span className="jp-badge jp-badge-secondary">{t('profile.pro_title')}</span>}
                <span className="jp-badge jp-badge-neutral">{user.role}</span>
              </div>
            </div>
            <button data-testid="edit-profile-button" onClick={() => setEditing(!editing)}
              className={`jp-btn ${editing ? 'jp-btn-ghost' : 'jp-btn-primary'}`}>
              {editing ? <><X size={16} /> {t('profile.cancel')}</> : <><PencilSimple size={16} /> {t('profile.edit')}</>}
            </button>
          </div>

          {/* ══ Social stats strip — posts / followers / following ══ */}
          <div className="grid grid-cols-3 gap-2 mb-5" data-testid="profile-stats">
            <Link to="#" aria-disabled="true" data-testid="stat-posts"
              className="px-2 py-3 rounded-xl text-center transition-colors"
              style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
              <p className="font-['Outfit'] text-xl font-extrabold" style={{ color: 'var(--jp-text)' }}>
                {social.posts_count}
              </p>
              <p className="text-[11px] font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
                {t('profile.publications')}
              </p>
            </Link>
            <Link to={`/users/${user.user_id}/followers`} data-testid="stat-followers"
              className="px-2 py-3 rounded-xl text-center transition-colors hover:border-transparent"
              style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
              <p className="font-['Outfit'] text-xl font-extrabold" style={{ color: 'var(--jp-text)' }}>
                {social.followers_count}
              </p>
              <p className="text-[11px] font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
                {t('profile.followers')}
              </p>
            </Link>
            <Link to={`/users/${user.user_id}/following`} data-testid="stat-following"
              className="px-2 py-3 rounded-xl text-center transition-colors hover:border-transparent"
              style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
              <p className="font-['Outfit'] text-xl font-extrabold" style={{ color: 'var(--jp-text)' }}>
                {social.following_count}
              </p>
              <p className="text-[11px] font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
                {t('profile.following')}
              </p>
            </Link>
          </div>

          {/* ══ Settings CTA + pending follow-requests inbox ══ */}
          <div className="grid grid-cols-2 gap-2 mb-5">
            <Link to="/settings" data-testid="profile-settings-link"
              className="jp-btn jp-btn-ghost justify-center">
              <Gear size={16} /> {t('profile.settings_cta')}
            </Link>
            {pendingFollowRequests > 0 ? (
              <Link to="/settings/requests" data-testid="profile-follow-requests-link"
                className="jp-btn jp-btn-primary justify-center">
                <UsersThree size={16} weight="fill" /> {t('profile.requests_cta')} ({pendingFollowRequests})
              </Link>
            ) : (
              <Link to="/notifications"
                className="jp-btn jp-btn-ghost justify-center">
                <UsersThree size={16} /> {t('profile.notifications_cta')}
              </Link>
            )}
          </div>

          {message && <div className="jp-alert jp-alert-info mb-5" data-testid="profile-message">{message}</div>}

          {editing ? (
            <form onSubmit={handleSave} className="space-y-4 jp-animate-fadeIn" data-testid="profile-edit-form">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="jp-label">{t('profile.first_name_label')}</label>
                  <input value={form.first_name} onChange={e => setForm(f => ({ ...f, first_name: e.target.value }))} className="jp-input text-sm" />
                </div>
                <div>
                  <label className="jp-label">{t('profile.last_name_label')}</label>
                  <input value={form.last_name} onChange={e => setForm(f => ({ ...f, last_name: e.target.value }))} className="jp-input text-sm" />
                </div>
              </div>
              <div>
                <label className="jp-label">{t('profile.phone_label')}</label>
                <input value={form.phone_number} onChange={e => setForm(f => ({ ...f, phone_number: e.target.value }))} className="jp-input text-sm" placeholder={t('profile.phone_placeholder')} />
              </div>
              <div>
                <label className="jp-label">{t('profile.about_label')}</label>
                <textarea value={form.about} onChange={e => setForm(f => ({ ...f, about: e.target.value }))} rows={3}
                  className="jp-input text-sm resize-none" style={{ height: 'auto' }} placeholder={t('profile.about_placeholder')} />
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="jp-label">{t('profile.gender_label')}</label>
                  <select value={form.gender} onChange={e => setForm(f => ({ ...f, gender: e.target.value }))} className="jp-input text-sm">
                    <option value="">{t('profile.gender_unspecified')}</option>
                    <option value="male">{t('profile.gender_male')}</option>
                    <option value="female">{t('profile.gender_female')}</option>
                    <option value="other">{t('profile.gender_other')}</option>
                  </select>
                </div>
                <div>
                  <label className="jp-label">{t('profile.country_label')}</label>
                  <input value={form.country} onChange={e => setForm(f => ({ ...f, country: e.target.value }))} className="jp-input text-sm" placeholder={t('profile.country_placeholder')} />
                </div>
              </div>
              <button type="submit" disabled={saving} data-testid="save-profile-button" className="jp-btn jp-btn-primary w-full sm:w-auto">
                {saving ? <><span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> {t('profile.saving')}</> : <><FloppyDisk size={16} /> {t('profile.save')}</>}
              </button>
            </form>
          ) : (
            <div className="space-y-4 jp-animate-fadeIn" data-testid="profile-info">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="flex items-center gap-3 p-4 rounded-xl" style={{ background: 'var(--jp-surface-secondary)' }}>
                  <EnvelopeSimple size={20} style={{ color: 'var(--jp-primary)' }} />
                  <div>
                    <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>{t('profile.email_label')}</p>
                    <p className="text-sm font-medium font-['Manrope']" style={{ color: 'var(--jp-text)' }}>{user.email}</p>
                  </div>
                </div>
                {user.phone_number && (
                  <div className="flex items-center gap-3 p-4 rounded-xl" style={{ background: 'var(--jp-surface-secondary)' }}>
                    <Phone size={20} style={{ color: 'var(--jp-primary)' }} />
                    <div>
                      <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>{t('profile.phone_label')}</p>
                      <p className="text-sm font-medium font-['Manrope']" style={{ color: 'var(--jp-text)' }}>{user.phone_number}</p>
                    </div>
                  </div>
                )}
                {user.country && (
                  <div className="flex items-center gap-3 p-4 rounded-xl" style={{ background: 'var(--jp-surface-secondary)' }}>
                    <Globe size={20} style={{ color: 'var(--jp-secondary)' }} />
                    <div>
                      <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>{t('profile.country_label')}</p>
                      <p className="text-sm font-medium font-['Manrope']" style={{ color: 'var(--jp-text)' }}>{user.country}</p>
                    </div>
                  </div>
                )}
                <div className="flex items-center gap-3 p-4 rounded-xl" style={{ background: 'var(--jp-surface-secondary)' }}>
                  <Calendar size={20} style={{ color: 'var(--jp-secondary)' }} />
                  <div>
                    <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>{t('profile.member_since')}</p>
                    <p className="text-sm font-medium font-['Manrope']" style={{ color: 'var(--jp-text)' }}>{user.created_at ? new Date(user.created_at).toLocaleDateString((i18n.language || 'fr').split(/[@_]/)[0], { year: 'numeric', month: 'long', day: 'numeric' }) : '-'}</p>
                  </div>
                </div>
              </div>
              {user.about && (
                <div className="p-4 rounded-xl" style={{ background: 'var(--jp-surface-secondary)' }}>
                  <p className="text-xs font-['Manrope'] mb-1" style={{ color: 'var(--jp-text-muted)' }}>{t('profile.about_label')}</p>
                  <p className="text-sm font-['Manrope']" style={{ color: 'var(--jp-text)' }}>{user.about}</p>
                </div>
              )}

              {/* iter162 — Display currency selector. Stored in users.display_currency;
                  the wallet canonical balance remains USD regardless of this choice. */}
              <DisplayCurrencySelector />

              {/* iter167 — Weekly scholarship digest preference (African JAPAP users). */}
              <ScholarshipDigestPref />

              {/* Quick links */}
              <div className="grid grid-cols-2 gap-3 pt-2">
                <Link to="/tasks" data-testid="link-tasks"
                  className="flex items-center gap-3 p-4 rounded-xl transition-all hover:scale-[1.02] col-span-2 relative overflow-hidden"
                  style={{ background: 'linear-gradient(135deg, #0F056B 0%, #E01C2E 100%)' }}>
                  <ListChecks size={26} color="#fff" weight="fill" />
                  <div className="flex-1">
                    <p className="text-sm font-semibold font-['Manrope'] text-white">{t('profile.tasks_title')}</p>
                    <p className="text-[11px] font-['Manrope'] text-white/85">
                      {pendingTasks > 0
                        ? t(pendingTasks > 1 ? 'profile.tasks_pending_many' : 'profile.tasks_pending_one', { count: pendingTasks })
                        : t('profile.tasks_all_done')}
                    </p>
                  </div>
                  {pendingTasks > 0 && (
                    <span className="inline-flex items-center justify-center min-w-[26px] h-[26px] px-2 rounded-full text-xs font-extrabold"
                          data-testid="link-tasks-badge"
                          style={{ background: 'white', color: '#E01C2E' }}>
                      {pendingTasks > 99 ? '99+' : pendingTasks}
                    </span>
                  )}
                </Link>
                <Link to="/calls" data-testid="link-calls"
                  className="flex items-center gap-3 p-4 rounded-xl transition-all hover:scale-[1.02]"
                  style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
                  <PhoneList size={24} style={{ color: '#F7931A' }} weight="fill" />
                  <div>
                    <p className="text-sm font-semibold font-['Manrope']" style={{ color: 'var(--jp-text)' }}>{t('profile.calls_title')}</p>
                    <p className="text-[11px] font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>{t('profile.calls_desc')}</p>
                  </div>
                </Link>
                <Link to="/referral" data-testid="link-referral"
                  className="flex items-center gap-3 p-4 rounded-xl transition-all hover:scale-[1.02]"
                  style={{ background: 'linear-gradient(135deg, #0F056B 0%, #E01C2E 100%)' }}>
                  <Gift size={24} color="#fff" weight="fill" />
                  <div>
                    <p className="text-sm font-semibold font-['Manrope'] text-white">{t('profile.referral_title')}</p>
                    <p className="text-[11px] font-['Manrope'] text-white/80">{t('profile.referral_desc_new')}</p>
                  </div>
                </Link>
                <Link to="/pro" data-testid="link-pro"
                  className="flex items-center gap-3 p-4 rounded-xl transition-all hover:scale-[1.02]"
                  style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
                  <Crown size={24} style={{ color: '#F59E0B' }} weight="fill" />
                  <div>
                    <p className="text-sm font-semibold font-['Manrope']" style={{ color: 'var(--jp-text)' }}>{t('profile.pro_title')}</p>
                    <p className="text-[11px] font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>{t('profile.pro_desc_new')}</p>
                  </div>
                </Link>
                <Link to="/connect" data-testid="link-connect"
                  className="flex items-center gap-3 p-4 rounded-xl transition-all hover:scale-[1.02] col-span-2"
                  style={{ background: 'linear-gradient(135deg, #10B981, #0891B2)' }}>
                  <Wifi size={24} color="#fff" weight="fill" />
                  <div>
                    <p className="text-sm font-semibold font-['Manrope'] text-white">{t('profile.connect_title')}</p>
                    <p className="text-[11px] font-['Manrope'] text-white/80">{t('profile.connect_desc')}</p>
                  </div>
                </Link>
                <Link to="/support" data-testid="link-support"
                  className="flex items-center gap-3 p-4 rounded-xl transition-all hover:scale-[1.02] col-span-2"
                  style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
                  <Headset size={24} weight="duotone" style={{ color: '#FFD700' }} />
                  <div>
                    <p className="text-sm font-semibold font-['Manrope']" style={{ color: 'var(--jp-text)' }}>Support JAPAP</p>
                    <p className="text-[11px] font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>Assistant IA 24/7 · Contacter un agent</p>
                  </div>
                </Link>
              </div>

              {/* ══ iter141nineF — Pay-as-you-Tip settings ══ */}
              <TipSettingsCard userId={user?.user_id} />

              {/* ══ UI LANGUAGE PREFERENCES ══ */}
              <div className="mt-3 p-4 rounded-xl" data-testid="ui-lang-card"
                style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
                <div className="flex items-center gap-2 mb-2">
                  <Globe size={18} weight="bold" style={{ color: 'var(--jp-primary)' }} />
                  <h3 className="font-['Outfit'] font-bold text-sm" style={{ color: 'var(--jp-text)' }}>
                    {t('profile.language')}
                  </h3>
                </div>
                <p className="text-xs font-['Manrope'] mb-3" style={{ color: 'var(--jp-text-muted)' }}>
                  {t('profile.ui_language_desc')}
                </p>
                <LanguageSwitcher />
              </div>

              {/* ══ AUTO-TRANSLATION PREFERENCES ══ */}
              <div className="mt-3 p-4 rounded-xl" data-testid="preferred-lang-card"
                style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
                <div className="flex items-center gap-2 mb-2">
                  <Translate size={18} weight="bold" style={{ color: 'var(--jp-primary)' }} />
                  <h3 className="font-['Outfit'] font-bold text-sm" style={{ color: 'var(--jp-text)' }}>
                    {t('profile.auto_translate_title')}
                  </h3>
                </div>
                <p className="text-xs font-['Manrope'] mb-3" style={{ color: 'var(--jp-text-muted)' }}>
                  {t('profile.auto_translate_desc')}
                </p>
                <select value={prefLang} onChange={(e) => handleSaveLang(e.target.value)}
                  disabled={savingLang}
                  data-testid="preferred-lang-select"
                  className="w-full px-3 py-2.5 rounded-lg text-sm font-['Manrope'] transition-all disabled:opacity-60"
                  style={{
                    background: 'var(--jp-surface)',
                    border: '1px solid var(--jp-border)',
                    color: 'var(--jp-text)',
                  }}>
                  {SUPPORTED_LANGS.map(l => (
                    <option key={l.code || 'none'} value={l.code}>{l.label}</option>
                  ))}
                </select>
                {prefLang && (
                  <p className="text-[11px] font-['Manrope'] mt-2 flex items-center gap-1" style={{ color: 'var(--jp-primary)' }}
                    data-testid="preferred-lang-active">
                    {t('profile.auto_translate_active', { lang: SUPPORTED_LANGS.find(l => l.code === prefLang)?.label })}
                  </p>
                )}
              </div>

              {/* ── Web Push notifications (iter70) ── */}
              <PushNotificationsToggle />
            </div>
          )}
        </div>
      </div>

      {/* ══ Image cropper — shared modal for avatar & cover ══ */}
      <PhotoGallery userId={user?.user_id} />
      <ImageCropper
        open={cropperFor === 'avatar'}
        shape="round"
        title={t('profile.photo_de_profil')}
        initialFile={cropperFor === 'avatar' ? filteredFile : null}
        onCancel={() => { setCropperFor(null); setFilteredFile(null); }}
        onCrop={async (blob) => { await handleAvatarUpload(blob); setFilteredFile(null); }}
      />
      <ImageCropper
        open={cropperFor === 'cover'}
        shape="rect3x1"
        title={t('profile.photo_de_couverture')}
        initialPositionY={social.cover_position_y}
        initialFile={cropperFor === 'cover' ? filteredFile : null}
        onCancel={() => { setCropperFor(null); setFilteredFile(null); }}
        onCrop={async (blob, opts) => { await handleCoverUpload(blob, opts); setFilteredFile(null); }}
      />
      {/* iter147 — pre-crop filter step (avatar + cover) */}
      <MediaFilterEditor
        open={filterEditor.open}
        mode="upload"
        accept="image/*"
        multiple={false}
        onClose={() => setFilterEditor({ open: false, target: null })}
        onApply={(filteredFiles) => {
          const f = filteredFiles[0];
          if (!f) return;
          setFilteredFile(f);
          setCropperFor(filterEditor.target);
          setFilterEditor({ open: false, target: null });
        }}
      />
    </div>
  );
}
