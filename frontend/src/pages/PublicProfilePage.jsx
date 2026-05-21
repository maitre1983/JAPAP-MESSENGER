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

  // iter240l-fix Bug 1 — Masquer le bouton flottant PWA refresh sur cette route.
  // Il est ajouté/retiré via une classe body pour ne pas démonter le composant
  // global et ne pas affecter les autres pages.
  useEffect(() => {
    document.body.classList.add('hide-pwa-refresh');
    return () => document.body.classList.remove('hide-pwa-refresh');
  }, []);

  // iter240l-fix Bug 2 — Fetch des publications récentes (publiques uniquement).
  const [recentPosts, setRecentPosts] = useState(null);
  useEffect(() => {
    if (!profile?.user_id) return;
    let alive = true;
    axios
      .get(`${API}/api/users/${profile.user_id}/public-posts?limit=10`, { withCredentials: true })
      .then(({ data }) => { if (alive) setRecentPosts(Array.isArray(data?.posts) ? data.posts : []); })
      .catch(() => { if (alive) setRecentPosts([]); });
    return () => { alive = false; };
  }, [profile?.user_id]);

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
                {/* iter241a-share-tiers — "Forecast Influencer" badge,
                    awarded when the user has >threshold winning referrals
                    in the trailing 30 days. */}
                {profile.is_forecast_influencer && (
                  <span
                    data-testid="public-profile-forecast-influencer-badge"
                    title={t('profile.forecast_influencer_tooltip', { defaultValue: 'A attiré plus de 10 paris gagnants en 30 jours via ses partages de prédictions.' })}
                    className="text-[10px] px-2 py-0.5 rounded-full font-bold inline-flex items-center gap-1"
                    style={{
                      background: 'linear-gradient(90deg,#7c3aed,#db2777)',
                      color: 'white',
                    }}>
                    🎁 {t('profile.forecast_influencer_badge', { defaultValue: 'Influenceur Prédictions' })}
                  </span>
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
            {/* iter240l-fix — Toutes les sections affichées avec placeholder
                quand vides. L'API retourne `about` (textarea longue) ET `bio`
                (legacy). On préfère `about`, fallback `bio`. */}
            <ProfileCard title={t('profile.about', { defaultValue: 'À propos' })} testId="public-profile-bio-card">
              {(profile.about || profile.bio) ? (
                <p className="text-sm whitespace-pre-wrap break-words">{profile.about || profile.bio}</p>
              ) : (
                <EmptyPlaceholder t={t} />
              )}
            </ProfileCard>

            <ProfileCard title={t('profile.experience', { defaultValue: 'Expériences' })} testId="public-profile-experience-card">
              {Array.isArray(profile.experience) && profile.experience.length > 0 ? (
                profile.experience.map((e, i) => (
                  <ItemRow key={i} icon={<Briefcase size={14}/>}
                    title={`${e.title || ''}${e.company ? ' · ' + e.company : ''}`}
                    subtitle={e.period} description={e.description} />
                ))
              ) : <EmptyPlaceholder t={t} />}
            </ProfileCard>

            <ProfileCard title={t('profile.education', { defaultValue: 'Formation' })} testId="public-profile-education-card">
              {Array.isArray(profile.education) && profile.education.length > 0 ? (
                profile.education.map((e, i) => (
                  <ItemRow key={i} icon={<GraduationCap size={14}/>}
                    title={`${e.degree || ''}${e.school ? ' · ' + e.school : ''}`}
                    subtitle={e.year} description={e.description} />
                ))
              ) : <EmptyPlaceholder t={t} />}
            </ProfileCard>

            <ProfileCard title={t('profile.achievements', { defaultValue: 'Réalisations' })} testId="public-profile-achievements-card">
              {Array.isArray(profile.achievements) && profile.achievements.length > 0 ? (
                profile.achievements.map((a, i) => (
                  <ItemRow key={i} icon={<Crown size={14}/>}
                    title={a.title} subtitle={a.date} description={a.description} />
                ))
              ) : <EmptyPlaceholder t={t} />}
            </ProfileCard>

            {/* iter240l-fix — Publications récentes (public posts only). */}
            <ProfileCard title={t('profile.recent_posts', { defaultValue: 'Publications récentes' })} testId="public-profile-posts-card">
              {recentPosts === null ? (
                <p className="text-xs text-slate-400 italic text-center py-3">
                  {t('profile.loading', { defaultValue: 'Chargement…' })}
                </p>
              ) : recentPosts.length === 0 ? (
                <EmptyPlaceholder t={t} />
              ) : (
                <div className="space-y-2">
                  {recentPosts.map((p) => (
                    <a key={p.post_id} href={`/feed?post=${p.post_id}`}
                       data-testid={`public-profile-post-${p.post_id}`}
                       className="block p-2 rounded-lg hover:bg-slate-50 transition no-underline text-slate-800">
                      {p.content && (
                        <p className="text-sm line-clamp-2 break-words">{p.content}</p>
                      )}
                      {Array.isArray(p.media_urls) && p.media_urls.length > 0 && (
                        <div className="flex gap-1 mt-1 overflow-hidden">
                          {p.media_urls.slice(0, 3).map((u, i) => (
                            <img key={i} src={u} alt="" loading="lazy"
                                 className="h-16 w-16 object-cover rounded-md" />
                          ))}
                        </div>
                      )}
                      <div className="text-[10px] text-slate-400 mt-1">
                        {fmtRelTime(p.created_at, i18n.language)}
                        {(p.likes_count ?? 0) > 0 && <> · ❤ {p.likes_count}</>}
                        {(p.comments_count ?? 0) > 0 && <> · 💬 {p.comments_count}</>}
                      </div>
                    </a>
                  ))}
                </div>
              )}
            </ProfileCard>
          </div>
          <aside className="space-y-4">
            <ProfileCard title={t('profile.skills', { defaultValue: 'Compétences' })} testId="public-profile-skills-card">
              {Array.isArray(profile.skills) && profile.skills.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {profile.skills.map((s, i) => (
                    <span key={i} className="px-2 py-0.5 rounded-full bg-slate-100 text-xs font-semibold text-slate-700">{s}</span>
                  ))}
                </div>
              ) : <EmptyPlaceholder t={t} />}
            </ProfileCard>

            {Array.isArray(profile.languages_spoken) && profile.languages_spoken.length > 0 && (
              <ProfileCard title={t('profile.languages', { defaultValue: 'Langues parlées' })} testId="public-profile-languages-card">
                <div className="flex flex-wrap gap-1.5">
                  {profile.languages_spoken.map((l, i) => (
                    <span key={i} className="px-2 py-0.5 rounded-full bg-indigo-50 text-xs font-semibold text-indigo-700">{l}</span>
                  ))}
                </div>
              </ProfileCard>
            )}

            <ProfileCard title={t('profile.social_links', { defaultValue: 'Réseaux sociaux' })} testId="public-profile-links-card">
              {(profile.website_url || profile.linkedin_url || profile.twitter_url) ? (
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
              ) : <EmptyPlaceholder t={t} />}
            </ProfileCard>
          </aside>
        </main>
      )}
    </div>
  );
}

function EmptyPlaceholder({ t }) {
  return (
    <p className="text-xs text-slate-400 italic text-center py-3" data-testid="public-profile-empty">
      {t('profile.no_info', { defaultValue: 'Aucune information renseignée' })}
    </p>
  );
}

function fmtRelTime(iso, lang) {
  if (!iso) return '';
  try {
    const dt = new Date(iso);
    const diff = (Date.now() - dt.getTime()) / 1000;
    if (diff < 60) return `${Math.round(diff)}s`;
    if (diff < 3600) return `${Math.round(diff/60)}m`;
    if (diff < 86400) return `${Math.round(diff/3600)}h`;
    if (diff < 604800) return `${Math.round(diff/86400)}d`;
    return dt.toLocaleDateString(lang || 'fr-FR', { day: '2-digit', month: 'short' });
  } catch { return ''; }
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
