// iter240j — Page profil public LinkedIn-style. Lookup par username OU
// user_id. Honore profile_visibility côté backend : privé → version masquée
// avec uniquement avatar/nom/headline + CTA "Envoyer un message".
//
// Cette page est ADDITIVE — la page /profile existante (own profile) reste
// inchangée. Cette route est /profile/:username.
import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import axios from 'axios';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import {
  EnvelopeSimple, Globe, MapPin, Briefcase, GraduationCap,
  Lock, Crown, ArrowLeft, LinkedinLogo, TwitterLogo,
  ChatCircleDots, Pencil, Eye, EyeSlash,
} from '@phosphor-icons/react';
import { useAuth } from '@/context/AuthContext';
import ProfileCompletionBar from '@/components/profile/ProfileCompletionBar';

const API = process.env.REACT_APP_BACKEND_URL;

export default function PublicProfilePage() {
  const { username } = useParams();
  const navigate = useNavigate();
  const { t, i18n } = useTranslation();
  const { user: viewer } = useAuth();
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [savingVisibility, setSavingVisibility] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    axios
      .get(`${API}/api/users/profile/${encodeURIComponent(username)}`, { withCredentials: true })
      .then(({ data }) => { if (alive) setProfile(data); })
      .catch((e) => {
        if (alive) {
          toast.error(
            e?.response?.status === 404
              ? t('profile.not_found', { defaultValue: 'Profil introuvable.' })
              : t('profile.load_failed', { defaultValue: 'Impossible de charger ce profil.' }),
          );
        }
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [username, t]);

  const isRtl = i18n.language === 'ar';

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-slate-500" dir={isRtl ? 'rtl' : 'ltr'}>
        {t('profile.loading', { defaultValue: 'Chargement…' })}
      </div>
    );
  }
  if (!profile) {
    return (
      <div className="min-h-screen flex items-center justify-center text-slate-500" dir={isRtl ? 'rtl' : 'ltr'}>
        {t('profile.not_found', { defaultValue: 'Profil introuvable.' })}
      </div>
    );
  }

  const isSelf = !!profile.is_self;
  const isPrivate = !!profile.is_private;
  const displayName = `${profile.first_name || ''} ${profile.last_name || ''}`.trim()
                     || profile.username || profile.user_id;

  const toggleVisibility = async () => {
    setSavingVisibility(true);
    try {
      const next = profile.profile_visibility === 'private' ? 'public' : 'private';
      await axios.post(`${API}/api/users/profile/visibility`, { visibility: next }, { withCredentials: true });
      setProfile((p) => ({ ...p, profile_visibility: next }));
      toast.success(next === 'public'
        ? t('profile.visibility_set_public', { defaultValue: 'Profil maintenant public.' })
        : t('profile.visibility_set_private', { defaultValue: 'Profil maintenant privé.' }));
    } catch {
      toast.error(t('profile.visibility_failed', { defaultValue: 'Échec du changement de visibilité.' }));
    } finally {
      setSavingVisibility(false);
    }
  };

  const dmHref = `/messenger?to=${profile.user_id}`;

  return (
    <div className="min-h-screen bg-slate-50" dir={isRtl ? 'rtl' : 'ltr'} data-testid="public-profile-page">
      {/* Header — back button */}
      <header className="sticky top-0 z-30 bg-white border-b border-slate-100 px-3 py-2 flex items-center gap-2">
        <button
          type="button"
          onClick={() => navigate(-1)}
          data-testid="public-profile-back"
          className="p-2 rounded-full hover:bg-slate-100">
          <ArrowLeft size={20} className={isRtl ? 'rotate-180' : ''} />
        </button>
        <h1 className="text-sm font-bold truncate flex-1">@{profile.username}</h1>
      </header>

      {/* Cover */}
      <div className="h-32 sm:h-44 bg-gradient-to-br from-rose-500 via-fuchsia-600 to-indigo-700 relative">
        {profile.cover_image && (
          <img src={profile.cover_image} alt="" className="w-full h-full object-cover"
               style={{ objectPosition: `center ${profile.cover_position_y ?? 50}%` }} />
        )}
      </div>

      {/* Header card */}
      <section className="px-4 -mt-12 relative" data-testid="public-profile-header">
        <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-4">
          <div className="flex items-start gap-3">
            <div className="relative -mt-12">
              <div className="w-24 h-24 rounded-full ring-4 ring-white overflow-hidden bg-slate-200">
                {profile.avatar
                  ? <img src={profile.avatar} alt="" className="w-full h-full object-cover" />
                  : <div className="w-full h-full flex items-center justify-center text-3xl font-bold text-slate-400">
                      {(displayName[0] || '?').toUpperCase()}
                    </div>}
              </div>
              {profile.profile_visibility === 'private' && (
                <span
                  data-testid="public-profile-lock-badge"
                  title={t('profile.private', { defaultValue: 'Privé' })}
                  className="absolute -bottom-1 -right-1 w-7 h-7 rounded-full bg-slate-700 text-white flex items-center justify-center ring-2 ring-white">
                  <Lock size={12} weight="fill" />
                </span>
              )}
            </div>
            <div className="flex-1 min-w-0 mt-1">
              <div className="flex items-center gap-1.5 flex-wrap">
                <h2 className="text-lg font-bold truncate" data-testid="public-profile-name">{displayName}</h2>
                {profile.kyc_verified && (
                  <span title={t('profile.kyc_verified', { defaultValue: 'Identité vérifiée' })}
                        className="text-blue-600"><Crown size={16} weight="fill" /></span>
                )}
                {profile.is_pro && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-800 font-bold">PRO</span>
                )}
              </div>
              <p className="text-sm text-slate-600 truncate" data-testid="public-profile-username">@{profile.username}</p>
              {profile.headline && (
                <p className="text-sm font-medium mt-1 break-words" data-testid="public-profile-headline">{profile.headline}</p>
              )}
              {profile.location && (
                <p className="text-xs text-slate-500 mt-1 flex items-center gap-1">
                  <MapPin size={12} /> {profile.location}
                </p>
              )}
            </div>
          </div>

          {/* Actions */}
          <div className="flex flex-wrap gap-2 mt-3">
            {isSelf ? (
              <>
                <button
                  type="button"
                  onClick={() => navigate('/profile')}
                  data-testid="public-profile-edit-btn"
                  className="flex-1 min-w-[120px] inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-full bg-rose-600 hover:bg-rose-700 text-white text-sm font-bold">
                  <Pencil size={14} /> {t('profile.edit', { defaultValue: 'Modifier le profil' })}
                </button>
                <button
                  type="button"
                  onClick={toggleVisibility}
                  disabled={savingVisibility}
                  data-testid="public-profile-visibility-toggle"
                  className="inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-full bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-bold">
                  {profile.profile_visibility === 'private'
                    ? <><EyeSlash size={14}/> {t('profile.private', { defaultValue: 'Privé' })}</>
                    : <><Eye size={14}/> {t('profile.public', { defaultValue: 'Public' })}</>}
                </button>
              </>
            ) : (
              <a
                href={dmHref}
                data-testid="public-profile-send-message"
                className="flex-1 min-w-[120px] inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-full bg-rose-600 hover:bg-rose-700 text-white text-sm font-bold no-underline">
                <ChatCircleDots size={14} /> {t('profile.send_message', { defaultValue: 'Envoyer un message' })}
              </a>
            )}
          </div>

          {/* Completion bar — owner only */}
          {isSelf && typeof profile.profile_completion_pct === 'number' && (
            <div className="mt-3">
              <ProfileCompletionBar pct={profile.profile_completion_pct} />
            </div>
          )}
        </div>
      </section>

      {/* Private mask */}
      {isPrivate && !isSelf && (
        <section className="px-4 mt-4" data-testid="public-profile-private-mask">
          <div className="bg-white rounded-2xl border border-slate-100 p-6 text-center">
            <Lock size={28} className="mx-auto text-slate-400 mb-2" />
            <h3 className="font-bold mb-1">{t('profile.private_message', { defaultValue: 'Ce profil est privé' })}</h3>
            <p className="text-xs text-slate-500">{t('profile.visibility_info', { defaultValue: 'Profil privé : seul votre nom et titre sont visibles par les autres' })}</p>
          </div>
        </section>
      )}

      {/* Full sections — hidden when isPrivate */}
      {!isPrivate && (
        <main className="px-4 mt-4 pb-12 grid gap-4 md:grid-cols-3">
          <div className="md:col-span-2 space-y-4">
            {profile.bio && (
              <ProfileCard title={t('profile.bio', { defaultValue: 'À propos' })} testId="public-profile-bio-card">
                <p className="text-sm whitespace-pre-wrap break-words">{profile.bio}</p>
              </ProfileCard>
            )}
            {Array.isArray(profile.experience) && profile.experience.length > 0 && (
              <ProfileCard title={t('profile.experience', { defaultValue: 'Expériences' })} testId="public-profile-experience-card">
                {profile.experience.map((e, i) => (
                  <ItemRow key={i} icon={<Briefcase size={14}/>}
                    title={`${e.title || ''}${e.company ? ' · ' + e.company : ''}`}
                    subtitle={e.period} description={e.description} />
                ))}
              </ProfileCard>
            )}
            {Array.isArray(profile.education) && profile.education.length > 0 && (
              <ProfileCard title={t('profile.education', { defaultValue: 'Formation' })} testId="public-profile-education-card">
                {profile.education.map((e, i) => (
                  <ItemRow key={i} icon={<GraduationCap size={14}/>}
                    title={`${e.degree || ''}${e.school ? ' · ' + e.school : ''}`}
                    subtitle={e.year} description={e.description} />
                ))}
              </ProfileCard>
            )}
            {Array.isArray(profile.achievements) && profile.achievements.length > 0 && (
              <ProfileCard title={t('profile.achievements', { defaultValue: 'Réalisations' })} testId="public-profile-achievements-card">
                {profile.achievements.map((a, i) => (
                  <ItemRow key={i} icon={<Crown size={14}/>}
                    title={a.title} subtitle={a.date} description={a.description} />
                ))}
              </ProfileCard>
            )}
          </div>
          <aside className="space-y-4">
            {Array.isArray(profile.skills) && profile.skills.length > 0 && (
              <ProfileCard title={t('profile.skills', { defaultValue: 'Compétences' })} testId="public-profile-skills-card">
                <div className="flex flex-wrap gap-1.5">
                  {profile.skills.map((s, i) => (
                    <span key={i} className="px-2 py-0.5 rounded-full bg-slate-100 text-xs font-semibold text-slate-700">{s}</span>
                  ))}
                </div>
              </ProfileCard>
            )}
            {Array.isArray(profile.languages_spoken) && profile.languages_spoken.length > 0 && (
              <ProfileCard title={t('profile.languages', { defaultValue: 'Langues parlées' })} testId="public-profile-languages-card">
                <div className="flex flex-wrap gap-1.5">
                  {profile.languages_spoken.map((l, i) => (
                    <span key={i} className="px-2 py-0.5 rounded-full bg-indigo-50 text-xs font-semibold text-indigo-700">{l}</span>
                  ))}
                </div>
              </ProfileCard>
            )}
            {(profile.website_url || profile.linkedin_url || profile.twitter_url) && (
              <ProfileCard title={t('profile.links', { defaultValue: 'Liens' })} testId="public-profile-links-card">
                <div className="flex flex-col gap-2 text-sm">
                  {profile.website_url && (
                    <a href={profile.website_url} target="_blank" rel="noopener noreferrer"
                       className="inline-flex items-center gap-1.5 text-blue-600 hover:underline truncate">
                      <Globe size={14}/> {profile.website_url}
                    </a>
                  )}
                  {profile.linkedin_url && (
                    <a href={profile.linkedin_url} target="_blank" rel="noopener noreferrer"
                       className="inline-flex items-center gap-1.5 text-blue-600 hover:underline truncate">
                      <LinkedinLogo size={14}/> LinkedIn
                    </a>
                  )}
                  {profile.twitter_url && (
                    <a href={profile.twitter_url} target="_blank" rel="noopener noreferrer"
                       className="inline-flex items-center gap-1.5 text-blue-600 hover:underline truncate">
                      <TwitterLogo size={14}/> X / Twitter
                    </a>
                  )}
                </div>
              </ProfileCard>
            )}
          </aside>
        </main>
      )}
    </div>
  );
}

function ProfileCard({ title, children, testId }) {
  return (
    <section data-testid={testId} className="bg-white rounded-2xl border border-slate-100 p-4">
      <h3 className="text-sm font-bold mb-3">{title}</h3>
      <div className="space-y-2.5">{children}</div>
    </section>
  );
}

function ItemRow({ icon, title, subtitle, description }) {
  return (
    <div className="flex gap-2">
      <div className="flex-shrink-0 mt-1 text-slate-500">{icon}</div>
      <div className="min-w-0 flex-1">
        {title && <div className="text-sm font-semibold truncate">{title}</div>}
        {subtitle && <div className="text-xs text-slate-500">{subtitle}</div>}
        {description && <div className="text-xs text-slate-600 mt-1 break-words">{description}</div>}
      </div>
    </div>
  );
}
