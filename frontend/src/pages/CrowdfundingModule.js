import { useEffect, useState, useCallback, useRef } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useAuth } from '@/context/AuthContext';
import { useTranslation } from 'react-i18next';
import {
  Heart, Users, GraduationCap, Briefcase, Siren, Plus, CaretLeft, X, CheckCircle,
  Trophy, Lightning, Cpu, Palette, MedalMilitary, Crown, Globe, Lock, Coins, ChartBar,
  ArrowsClockwise, Gear, ShareNetwork, WhatsappLogo,
} from '@phosphor-icons/react';
import { extractErrorMessage } from '@/utils/errorMessage';
import VoteCelebration from '@/components/crowdfunding/VoteCelebration';
import EngagementBanner from '@/components/crowdfunding/EngagementBanner';
import JuryHallOfFame from '@/components/JuryHallOfFame';
import { useEngagementState, trackEngagementEvent } from '@/hooks/useEngagementState';
import RecruiterPanel from '@/components/crowdfunding/RecruiterPanel';

const API = process.env.REACT_APP_BACKEND_URL;

const CAT_ICONS = {
  business: Briefcase,
  tech: Cpu,
  art: Palette,
  health: Heart,
  education: GraduationCap,
  sport: Trophy,
  community: Users,
  emergency: Siren,
};

const fmt = (n) => new Intl.NumberFormat('fr-FR').format(Number(n || 0));

// ─── Header — global cycle counter ───────────────────────────────────────
function CycleHeader({ state, onAdmin, isAdmin }) {
  const { t } = useTranslation();
  if (!state) return null;
  if (state.between_cycles) {
    return (
      <div className="bg-gradient-to-br from-indigo-900 to-rose-900 text-white p-5 rounded-2xl shadow-lg" data-testid="cf-state-between">
        <div className="flex items-center gap-2 mb-2">
          <Lock weight="fill" size={22} className="text-amber-300" />
          <span className="text-sm font-semibold uppercase tracking-wide">Entre deux cycles</span>
        </div>
        <h2 className="text-2xl font-bold leading-tight mb-2">Le challenge précédent est terminé.</h2>
        {state.last_cycle?.winner_user_id && (
          <p className="text-white/80 text-sm mb-3">
            Cycle {state.last_cycle.cycle_number} clôturé · Un nouveau cycle sera annoncé bientôt.
          </p>
        )}
        {isAdmin && (
          <button
            data-testid="cf-admin-shortcut-newcycle"
            onClick={onAdmin}
            className="mt-2 bg-white text-rose-700 font-bold px-4 py-2 rounded-full text-sm hover:bg-rose-50">
            Démarrer le prochain cycle
          </button>
        )}
      </div>
    );
  }

  const c = state.cycle;
  const pct = Math.min(100, Math.round((state.projects_count / Math.max(1, c.threshold_projects)) * 100));

  return (
    <div className="bg-gradient-to-br from-indigo-700 via-fuchsia-700 to-rose-600 text-white p-5 rounded-2xl shadow-xl" data-testid="cf-state-active">
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="text-[11px] uppercase tracking-widest text-white/70">Cycle #{c.cycle_number}</div>
          <h2 className="text-2xl font-bold flex items-center gap-2 mt-1">
            {c.votes_open ? <Lightning weight="fill" className="text-amber-300" /> : <Lock weight="fill" className="text-white/70" />}
            {c.votes_open ? 'Votes ouverts !' : t('crowdfunding.compte_a_rebours')}
          </h2>
        </div>
        <div className="text-right">
          <div className="text-[11px] uppercase tracking-widest text-white/70">Récompense</div>
          <div className="font-extrabold text-xl flex items-center gap-1">
            <Coins weight="fill" className="text-amber-300" />
            {fmt(c.reward_amount)} {c.reward_currency}
          </div>
        </div>
      </div>

      {!c.votes_open ? (
        <>
          <div className="text-sm text-white/80 mb-2">
            Les votes ouvriront dès que <b>{c.threshold_projects}</b> projets seront en lice.
          </div>
          <div className="bg-white/20 rounded-full h-3 overflow-hidden mb-1">
            <div
              className="bg-amber-300 h-full transition-all duration-500"
              style={{ width: `${pct}%` }}
              data-testid="cf-threshold-bar" />
          </div>
          <div className="text-xs text-white/90 font-mono" data-testid="cf-threshold-counter">
            {state.projects_count} / {c.threshold_projects} projets
            {state.projects_remaining_to_open > 0 && (
              <span className="text-amber-200 ml-2">
                {' · encore '}{state.projects_remaining_to_open} pour déclencher
              </span>
            )}
          </div>
        </>
      ) : (
        <div className="text-sm text-white/90">
          Le premier projet à atteindre <b>{fmt(c.votes_to_win)} votes</b> remporte le pactole.
          {state.projects_count > 0 && (
            <span> · {state.projects_count} projets en lice.</span>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Project card ────────────────────────────────────────────────────────
function ProjectCard({ project, votesOpen, votesToWin, currentUserId, voting, onVote, onShare, isAdmin, onAdminChanged }) {
  const { t } = useTranslation();
  const Icon = CAT_ICONS[project.category] || Users;
  const pct = Math.min(100, Math.round((project.votes_count / Math.max(1, votesToWin)) * 100));
  const isMine = currentUserId && project.user_id === currentUserId;
  const isWinner = project.status === 'winner';
  const isVotingThis = voting === project.slug;
  // iter239x — Login required is the ONLY condition; not-logged users get
  // a redirect-to-login button instead of being silently disabled.
  const notLoggedIn = !currentUserId;
  const disabled = notLoggedIn ? false : (!votesOpen || isMine || project.voted_by_me || isWinner || isVotingThis);

  const handleVoteClick = () => {
    if (notLoggedIn) {
      const redirect = encodeURIComponent(`/crowdfunding/p/${project.slug}`);
      window.location.href = `/login?redirect=${redirect}`;
      return;
    }
    onVote(project);
  };

  return (
    <div
      className={`bg-white rounded-xl p-4 shadow-sm border ${isWinner ? 'border-amber-400 ring-2 ring-amber-300' : 'border-slate-200'}`}
      data-testid={`cf-project-card-${project.slug}`}>
      {isWinner && (
        <div className="flex items-center gap-1 text-amber-600 text-xs font-bold uppercase tracking-wide mb-2">
          <Crown weight="fill" /> Gagnant
        </div>
      )}

      <div className="flex items-start gap-3 mb-2">
        {project.owner_avatar ? (
          <img src={project.owner_avatar} alt="" loading="lazy" decoding="async" className="w-10 h-10 rounded-full object-cover flex-shrink-0" />
        ) : (
          <div className="w-10 h-10 rounded-full bg-rose-100 flex items-center justify-center flex-shrink-0">
            <Users size={20} className="text-rose-600" />
          </div>
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1 text-[11px] text-slate-500 uppercase tracking-wide">
            <Icon size={12} /> {project.category}
            {project.country_code && <span>· {project.country_code}</span>}
          </div>
          <h3 className="font-bold text-slate-900 leading-tight mt-0.5 line-clamp-2">{project.title}</h3>
          <div className="text-xs text-slate-500 mt-0.5">par {project.owner_name}</div>
        </div>
      </div>

      {project.image_url && (
        <img src={project.image_url} alt={project.title}
             loading="lazy" decoding="async"
             className="w-full h-32 object-cover rounded-lg mb-2"
             onError={(e) => { e.target.style.display = 'none'; }} />
      )}

      <p className="text-sm text-slate-600 mb-3 line-clamp-2">{project.description}</p>

      <div className="mb-2">
        <div className="flex items-baseline justify-between mb-1">
          <span className="text-lg font-extrabold text-rose-600" data-testid={`cf-project-votes-${project.slug}`}>
            {fmt(project.votes_count)} votes
          </span>
          <span className="text-xs text-slate-500">/ {fmt(votesToWin)} pour gagner</span>
        </div>
        <div className="bg-slate-200 rounded-full h-2 overflow-hidden">
          <div className="bg-gradient-to-r from-rose-500 to-amber-500 h-full transition-all"
               style={{ width: `${pct}%` }} />
        </div>
      </div>

      <div className="flex gap-2">
        <button
          onClick={handleVoteClick}
          disabled={disabled}
          data-testid={`cf-vote-btn-${project.slug}`}
          className={`flex-1 py-2 rounded-full text-sm font-bold transition ${
            notLoggedIn
              ? 'bg-indigo-600 text-white hover:bg-indigo-700 active:scale-95'
              : disabled && !isVotingThis
                ? 'bg-slate-100 text-slate-400 cursor-not-allowed'
                : 'bg-rose-600 text-white hover:bg-rose-700 active:scale-95'
          } ${isVotingThis ? 'opacity-70 cursor-wait' : ''}`}>
          {isVotingThis ? (
            <span className="inline-flex items-center gap-2">
              <span className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
              Vote…
            </span>
          ) : notLoggedIn ? t('crowdfunding.vote_login_required_btn', { defaultValue: '🔐 Connectez-vous pour voter' }) :
             isWinner ? 'Gagnant' :
             isMine ? 'Ton projet' :
             project.voted_by_me ? '✓ Voté' :
             !votesOpen ? t('crowdfunding.verrouille') :
             t('crowdfunding.vote_btn', { defaultValue: '🗳 Voter' })}
        </button>
        <button
          onClick={() => onShare(project)}
          data-testid={`cf-share-btn-${project.slug}`}
          className="bg-emerald-500 text-white px-3 py-2 rounded-full text-sm font-bold hover:bg-emerald-600">
          <ShareNetwork size={16} weight="bold" />
        </button>
      </div>

      {/* iter239w — Bandeau d'état non-public + actions admin */}
      {project.status && !['active', 'winner'].includes(project.status) && (
        <div className={`mt-2 text-xs px-2 py-1 rounded-md inline-block font-semibold ${
          project.status === 'pending_review' ? 'bg-amber-100 text-amber-700' :
          project.status === 'suspended' ? 'bg-orange-100 text-orange-700' :
          project.status === 'expired' ? 'bg-slate-100 text-slate-600' :
          project.status === 'disqualified' ? 'bg-rose-100 text-rose-700' :
          'bg-slate-100 text-slate-600'
        }`} data-testid={`cf-project-status-${project.slug}`}>
          {project.status === 'pending_review' ? '⏳ En attente de validation' :
           project.status === 'suspended' ? '⏸️ Suspendu' :
           project.status === 'expired' ? '⌛ Expiré' :
           project.status === 'disqualified' ? '🚫 Disqualifié' :
           project.status}
        </div>
      )}
      {isAdmin && <AdminProjectActions project={project} onChanged={onAdminChanged} />}
    </div>
  );
}

// ─── Create-project modal ────────────────────────────────────────────────
function CreateProjectModal({ open, onClose, onCreated, eligibility, termsAccepted }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    title: '', description: '', objective: '',
    category: 'community', image_url: '',
    country_code: '', duration_days: 30,
  });
  const [submitting, setSubmitting] = useState(false);
  // iter239v — Explicit validation feedback so the user is never stuck on a
  // silently-blocked button (iPhone landscape regression).
  const [formError, setFormError] = useState('');

  if (!open) return null;

  const submit = async (e) => {
    e.preventDefault();
    if (submitting) return;
    setFormError('');

    // iter239w — Garde-fou : conditions doivent avoir été acceptées (le flux
    // parent garantit normalement le passage par TermsModal, mais on protège
    // ici contre tout court-circuit).
    if (!termsAccepted) {
      setFormError(t('crowdfunding.terms_required', {
        defaultValue: 'Tu dois accepter les conditions de participation.',
      }));
      return;
    }

    // Explicit (non-silent) validation — mirrors required/minLength on inputs.
    const title = (form.title || '').trim();
    const description = (form.description || '').trim();
    if (title.length < 4) {
      setFormError(t('crowdfunding.error_title_too_short', { defaultValue: 'Le titre doit contenir au moins 4 caractères.' }));
      return;
    }
    if (description.length < 20) {
      setFormError(t('crowdfunding.error_description_too_short', { defaultValue: 'La description doit contenir au moins 20 caractères.' }));
      return;
    }

    setSubmitting(true);
    try {
      const payload = { ...form, terms_accepted: true, terms_version: 'v1' };
      const { data } = await axios.post(`${API}/api/crowdfunding/projects`, payload, {
        withCredentials: true,
      });
      toast.success(t('crowdfunding.created_success', { defaultValue: 'Projet créé !' }));
      onCreated(data);
    } catch (err) {
      const msg = extractErrorMessage(err, t('crowdfunding.create_failed', { defaultValue: 'Création impossible.' }));
      setFormError(msg);
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const eligible = eligibility?.eligible !== false;

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-end sm:items-center justify-center p-2" onClick={onClose}>
      {/*
        iter239v — Sticky-footer layout so the "Launch my project" button
        is ALWAYS reachable on small viewports (iPhone 13 Pro Max portrait
        ≈ 390×844 with safe-area top + keyboard). The header stays fixed
        too, the middle scrolls, and `safe-area-inset-bottom` keeps the
        button above the home indicator. `100dvh` (dynamic viewport
        height) avoids the Safari 100vh-includes-toolbars bug.
      */}
      <div
        className="bg-white rounded-t-2xl sm:rounded-2xl w-full max-w-lg flex flex-col"
        style={{
          maxHeight: 'min(92vh, 92dvh)',
          WebkitOverflowScrolling: 'touch',
        }}
        onClick={(e) => e.stopPropagation()}
        data-testid="cf-create-modal">

        {/* Header fixe */}
        <div className="flex items-center justify-between px-5 pt-5 pb-3 border-b border-slate-100 flex-shrink-0">
          <h3 className="text-lg font-bold text-slate-900">{t('crowdfunding.create_title', { defaultValue: 'Créer mon projet' })}</h3>
          <button onClick={onClose} aria-label={t('common.close', { defaultValue: 'Fermer' })}
                  className="p-1.5 rounded-full hover:bg-slate-100"><X size={20} /></button>
        </div>

        {/* Zone scrollable (iOS momentum scroll via WebkitOverflowScrolling parent) */}
        <div className="flex-1 overflow-y-auto px-5 py-3"
             style={{ WebkitOverflowScrolling: 'touch' }}>

        {!eligible && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 mb-3 text-sm" data-testid="cf-create-not-eligible">
            <div className="font-bold text-amber-800 mb-1">{t('crowdfunding.not_eligible_title', { defaultValue: "Tu n'es pas encore éligible" })}</div>
            <ul className="text-amber-700 text-xs space-y-0.5 list-disc list-inside">
              {eligibility.reasons.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
          </div>
        )}

        <form id="cf-create-form" onSubmit={submit} noValidate className="space-y-3">
          <div>
            <label className="text-xs font-semibold text-slate-700">Titre *</label>
            <input
              required maxLength={160} minLength={4}
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              data-testid="cf-create-title"
              className="w-full mt-1 px-3 py-2 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-rose-500 outline-none"
              placeholder={t('crowdfunding.ex_restaurant_solidaire_a_yaounde')} />
          </div>

          <div>
            <label className="text-xs font-semibold text-slate-700">Description *</label>
            <textarea
              required maxLength={4000} minLength={20}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              data-testid="cf-create-description"
              rows={4}
              className="w-full mt-1 px-3 py-2 border border-slate-300 rounded-lg text-sm focus:ring-2 focus:ring-rose-500 outline-none"
              placeholder={t('crowdfunding.presente_ton_projet_en_detail_min_2')} />
            <div className="text-[10px] text-slate-400 mt-0.5">{form.description.length} / 4000</div>
          </div>

          <div>
            <label className="text-xs font-semibold text-slate-700">Objectif (résumé d'impact)</label>
            <input
              maxLength={2000}
              value={form.objective}
              onChange={(e) => setForm({ ...form, objective: e.target.value })}
              data-testid="cf-create-objective"
              className="w-full mt-1 px-3 py-2 border border-slate-300 rounded-lg text-sm"
              placeholder={t('crowdfunding.ex_creer_10_emplois_et_nourrir_200')} />
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs font-semibold text-slate-700">Catégorie</label>
              <select
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value })}
                data-testid="cf-create-category"
                className="w-full mt-1 px-3 py-2 border border-slate-300 rounded-lg text-sm">
                <option value="business">{t('crowdfunding.business_startup')}</option>
                <option value="tech">Tech</option>
                <option value="art">{t('crowdfunding.art_culture')}</option>
                <option value="health">{t('crowdfunding.sante')}</option>
                <option value="education">{t('crowdfunding.education')}</option>
                <option value="sport">{t('crowdfunding.sport')}</option>
                <option value="community">{t('crowdfunding.communautaire')}</option>
                <option value="emergency">{t('crowdfunding.urgence')}</option>
              </select>
            </div>
            <div>
              <label className="text-xs font-semibold text-slate-700">Pays (ISO 2)</label>
              <input
                maxLength={2}
                value={form.country_code}
                onChange={(e) => setForm({ ...form, country_code: e.target.value.toUpperCase() })}
                data-testid="cf-create-country"
                className="w-full mt-1 px-3 py-2 border border-slate-300 rounded-lg text-sm uppercase"
                placeholder="CM" />
            </div>
          </div>

          <div>
            <label className="text-xs font-semibold text-slate-700">Image (URL, optionnel)</label>
            <input
              type="url" maxLength={500}
              value={form.image_url}
              onChange={(e) => setForm({ ...form, image_url: e.target.value })}
              data-testid="cf-create-image"
              className="w-full mt-1 px-3 py-2 border border-slate-300 rounded-lg text-sm"
              placeholder={t('crowdfunding.https')} />
          </div>

          <div>
            <label className="text-xs font-semibold text-slate-700">Durée (jours)</label>
            <input
              type="number" min={7} max={180}
              value={form.duration_days}
              onChange={(e) => setForm({ ...form, duration_days: Number(e.target.value) })}
              data-testid="cf-create-duration"
              className="w-full mt-1 px-3 py-2 border border-slate-300 rounded-lg text-sm" />
          </div>
        </form>

        {/* iter239v — Inline error block so validation feedback is never silent. */}
        {formError && (
          <div
            role="alert"
            aria-live="polite"
            data-testid="cf-create-error"
            className="mt-3 bg-rose-50 border border-rose-200 text-rose-700 text-sm rounded-lg px-3 py-2">
            {formError}
          </div>
        )}
        </div>
        {/* /scrollable body */}

        {/* iter239v — Sticky footer: button always reachable above iOS home
            indicator (safe-area-inset-bottom) and Android nav bar. */}
        <div
          className="flex-shrink-0 border-t border-slate-100 bg-white px-5 pt-3"
          style={{ paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 12px)' }}>
          <button
            type="submit"
            form="cf-create-form"
            disabled={submitting || !eligible}
            data-testid="cf-create-submit"
            className="w-full bg-rose-600 text-white py-3 rounded-full font-bold hover:bg-rose-700 disabled:bg-slate-300 disabled:cursor-not-allowed shadow-lg">
            {submitting
              ? t('crowdfunding.creation', { defaultValue: 'Création…' })
              : t('crowdfunding.launch_btn', { defaultValue: 'Lancer mon projet' })}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── My Dashboard panel ──────────────────────────────────────────────────
function MyDashboard({ me, engagement, onCreate, onEdit, onDelete }) {
  const { t } = useTranslation();
  if (!me) return null;
  if (me.between_cycles) {
    return (
      <div className="bg-slate-100 rounded-xl p-3 text-sm text-slate-600" data-testid="cf-my-between">
        Aucun cycle actif. Tu pourras créer ton projet dès qu'un nouveau cycle démarrera.
      </div>
    );
  }

  if (!me.project) {
    return (
      <div className="bg-gradient-to-r from-rose-50 to-amber-50 border border-rose-200 rounded-xl p-4" data-testid="cf-my-noproject">
        <h3 className="font-bold text-slate-900 mb-1">Tu n'as pas encore de projet</h3>
        <p className="text-xs text-slate-600 mb-3">
          Lance le tien et invite tes contacts à voter. <b>1 projet par cycle</b>.
        </p>
        <button
          data-testid="cf-my-create-cta"
          onClick={onCreate}
          disabled={!me.eligibility?.eligible}
          className="bg-rose-600 text-white px-4 py-2 rounded-full text-sm font-bold disabled:bg-slate-300">
          {me.eligibility?.eligible ? '+ Créer mon projet' : t('crowdfunding.pas_encore_eligible')}
        </button>
        {!me.eligibility?.eligible && (
          <ul className="mt-2 text-xs text-slate-600 list-disc list-inside" data-testid="cf-my-eligibility-reasons">
            {me.eligibility.reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        )}
      </div>
    );
  }

  const p = me.project;
  const sp = engagement?.share_performance;
  const velocity = engagement?.vote_velocity_10m || 0;
  const canEdit = !p.votes_started && p.votes_count === 0;

  return (
    <div className="bg-white border border-rose-300 rounded-xl p-4 shadow-sm" data-testid="cf-my-project">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-bold text-slate-900 flex items-center gap-1">
          <Crown weight="fill" className="text-rose-600" /> Mon projet
        </h3>
        <div className="flex items-center gap-1">
          {p.rank > 0 && <span className="text-xs bg-rose-100 text-rose-700 px-2 py-0.5 rounded-full font-bold">Rang #{p.rank}</span>}
          {canEdit && onEdit && (
            <button
              onClick={() => onEdit(p)}
              data-testid="cf-my-edit-btn"
              className="text-xs px-2 py-0.5 bg-slate-100 hover:bg-slate-200 rounded-full">
              ✏️ Éditer
            </button>
          )}
          {canEdit && onDelete && (
            <button
              onClick={() => onDelete(p)}
              data-testid="cf-my-delete-btn"
              className="text-xs px-2 py-0.5 bg-red-50 text-red-700 hover:bg-red-100 rounded-full">
              🗑
            </button>
          )}
        </div>
      </div>
      <div className="text-sm font-semibold text-slate-900 line-clamp-1">{p.title}</div>
      <div className="text-xs text-slate-500 mb-2">{p.category} {p.country_code && `· ${p.country_code}`}</div>

      <div className="flex items-baseline justify-between mb-1">
        <span className="text-2xl font-extrabold text-rose-600" data-testid="cf-my-votes">{fmt(p.votes_count)}</span>
        <span className="text-xs text-slate-500">votes (objectif {fmt(me.votes_to_win)})</span>
      </div>
      <div className="bg-slate-200 rounded-full h-2 overflow-hidden">
        <div className="bg-gradient-to-r from-rose-500 to-amber-500 h-full" style={{ width: `${p.progress_pct || 0}%` }} />
      </div>

      {/* iter142E — Velocity + share performance widgets */}
      {(velocity > 0 || (sp && sp.last_share_votes > 0)) && (
        <div className="mt-3 grid grid-cols-2 gap-2" data-testid="cf-my-perf-widgets">
          {velocity > 0 && (
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-2" data-testid="cf-my-velocity">
              <div className="text-[10px] uppercase tracking-wider text-amber-700 font-bold">Vitesse</div>
              <div className="text-lg font-extrabold text-amber-700">🚀 +{velocity}</div>
              <div className="text-[10px] text-amber-700/80">votes / 10 min</div>
            </div>
          )}
          {sp && sp.last_share_votes > 0 && (
            <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-2" data-testid="cf-my-share-perf">
              <div className="text-[10px] uppercase tracking-wider text-emerald-700 font-bold">Dernier partage</div>
              <div className="text-lg font-extrabold text-emerald-700">🔥 {sp.last_share_votes}</div>
              <div className="text-[10px] text-emerald-700/80">
                votes générés{sp.best_channel ? ` · via ${sp.best_channel}` : ''}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Admin panel ─────────────────────────────────────────────────────────
function AdminPanel({ open, onClose, onChanged, activeCycle }) {
  const { t } = useTranslation();
  const [tab, setTab] = useState('cycle');
  const [cycle, setCycle] = useState({ threshold_projects: 50, votes_to_win: 100, reward_amount: 50000, reward_currency: 'XAF', notes: '' });
  const [activeUpdate, setActiveUpdate] = useState({});
  const [settings, setSettings] = useState(null);
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState([]);

  const loadAll = useCallback(async () => {
    try {
      const [s, h] = await Promise.all([
        axios.get(`${API}/api/crowdfunding/admin/settings`, { withCredentials: true }),
        axios.get(`${API}/api/crowdfunding/admin/cycles`, { withCredentials: true }),
      ]);
      setSettings(s.data);
      setHistory(h.data);
    } catch (e) {
      toast.error(extractErrorMessage(e, 'Chargement admin échoué.'));
    }
  }, []);

  useEffect(() => { if (open) loadAll(); }, [open, loadAll]);

  if (!open) return null;

  const startCycle = async () => {
    setBusy(true);
    try {
      await axios.post(`${API}/api/crowdfunding/admin/cycles`, cycle, { withCredentials: true });
      toast.success('Nouveau cycle lancé.');
      onChanged();
      loadAll();
    } catch (e) {
      toast.error(extractErrorMessage(e, 'Impossible de lancer le cycle.'));
    } finally { setBusy(false); }
  };

  const updateActive = async () => {
    setBusy(true);
    try {
      await axios.put(`${API}/api/crowdfunding/admin/cycles/active`, activeUpdate, { withCredentials: true });
      toast.success('Cycle actif mis à jour.');
      onChanged();
      loadAll();
    } catch (e) {
      toast.error(extractErrorMessage(e, 'Mise à jour échouée.'));
    } finally { setBusy(false); }
  };

  const saveSettings = async () => {
    setBusy(true);
    try {
      await axios.put(`${API}/api/crowdfunding/admin/settings`, settings, { withCredentials: true });
      toast.success('Réglages sauvegardés.');
    } catch (e) {
      toast.error(extractErrorMessage(e, 'Sauvegarde échouée.'));
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-end sm:items-center justify-center p-2" onClick={onClose}>
      <div className="bg-white rounded-t-2xl sm:rounded-2xl w-full max-w-2xl max-h-[92vh] overflow-y-auto"
           onClick={(e) => e.stopPropagation()}
           data-testid="cf-admin-panel">
        <div className="sticky top-0 bg-white border-b px-5 py-3 flex items-center justify-between">
          <h3 className="font-bold text-lg flex items-center gap-2"><Gear weight="fill" /> Admin Crowdfunding</h3>
          <button onClick={onClose} className="p-1.5 rounded-full hover:bg-slate-100"><X size={20} /></button>
        </div>

        <div className="px-5 pt-3 flex gap-1 border-b">
          {[['cycle', 'Cycles'], ['settings', 'Réglages'], ['history', 'Historique']].map(([k, v]) => (
            <button
              key={k}
              data-testid={`cf-admin-tab-${k}`}
              onClick={() => setTab(k)}
              className={`px-3 py-2 text-sm font-semibold border-b-2 ${tab === k ? 'border-rose-600 text-rose-600' : 'border-transparent text-slate-500'}`}>
              {v}
            </button>
          ))}
        </div>

        <div className="p-5 space-y-5">
          {tab === 'cycle' && (
            <>
              <section>
                <h4 className="font-bold text-slate-900 mb-2">Démarrer un nouveau cycle</h4>
                <div className="grid grid-cols-2 gap-2">
                  <NumField label="Seuil projets" v={cycle.threshold_projects} onChange={(v) => setCycle({ ...cycle, threshold_projects: v })} testid="cf-admin-threshold" />
                  <NumField label="Votes pour gagner" v={cycle.votes_to_win} onChange={(v) => setCycle({ ...cycle, votes_to_win: v })} testid="cf-admin-votes2win" />
                  <NumField label="Récompense" v={cycle.reward_amount} onChange={(v) => setCycle({ ...cycle, reward_amount: v })} testid="cf-admin-reward" />
                  <div>
                    <label className="text-xs font-semibold text-slate-700">Devise</label>
                    <input
                      value={cycle.reward_currency}
                      onChange={(e) => setCycle({ ...cycle, reward_currency: e.target.value.toUpperCase() })}
                      data-testid="cf-admin-currency"
                      className="w-full mt-1 px-3 py-2 border rounded-lg text-sm uppercase" />
                  </div>
                </div>
                <textarea
                  value={cycle.notes}
                  onChange={(e) => setCycle({ ...cycle, notes: e.target.value })}
                  data-testid="cf-admin-notes"
                  placeholder={t('crowdfunding.notes_optionnel_campagne_saisonnier')}
                  className="w-full mt-2 px-3 py-2 border rounded-lg text-sm" rows={2} />
                <button
                  data-testid="cf-admin-start-cycle"
                  onClick={startCycle}
                  disabled={busy}
                  className="mt-2 w-full bg-rose-600 text-white py-2 rounded-full font-bold disabled:bg-slate-300">
                  Démarrer ce cycle
                </button>
              </section>

              <section className="pt-3 border-t">
                <h4 className="font-bold text-slate-900 mb-2">Modifier le cycle actif (legacy)</h4>
                <div className="grid grid-cols-2 gap-2">
                  <NumField label="Nouveau seuil" v={activeUpdate.threshold_projects ?? ''} onChange={(v) => setActiveUpdate({ ...activeUpdate, threshold_projects: v })} testid="cf-admin-update-threshold" />
                  <NumField label="Nouveau votes/win" v={activeUpdate.votes_to_win ?? ''} onChange={(v) => setActiveUpdate({ ...activeUpdate, votes_to_win: v })} testid="cf-admin-update-votes2win" />
                  <NumField label="Nouvelle récompense" v={activeUpdate.reward_amount ?? ''} onChange={(v) => setActiveUpdate({ ...activeUpdate, reward_amount: v })} testid="cf-admin-update-reward" />
                </div>
                <button
                  data-testid="cf-admin-update-active"
                  onClick={updateActive}
                  disabled={busy}
                  className="mt-2 w-full bg-indigo-600 text-white py-2 rounded-full font-bold disabled:bg-slate-300">
                  Mettre à jour le cycle actif
                </button>
              </section>

              {/* iter239w — Panneau étendu avec dates configurables + minimum_votes_required */}
              <section className="pt-3 border-t" data-testid="cf-admin-cycle-control-section">
                <CycleControlPanel cycle={activeCycle} onChanged={() => { onChanged(); loadAll(); }} />
              </section>
            </>
          )}

          {tab === 'settings' && settings && (
            <section className="space-y-2 pb-2">
              <h4 className="font-bold text-slate-900">Critères d'éligibilité</h4>
              <NumField label="Âge min. du compte (jours)" v={settings.min_account_age_days} onChange={(v) => setSettings({ ...settings, min_account_age_days: v })} testid="cf-admin-set-age" />
              <NumField label="Score d'activité min." v={settings.min_activity_score} onChange={(v) => setSettings({ ...settings, min_activity_score: v })} testid="cf-admin-set-score" />
              <h4 className="font-bold text-slate-900 pt-3">Défauts du prochain cycle</h4>
              <NumField label="Seuil projets par défaut" v={settings.default_threshold_projects} onChange={(v) => setSettings({ ...settings, default_threshold_projects: v })} testid="cf-admin-set-thr" />
              <NumField label="Votes/win par défaut" v={settings.default_votes_to_win} onChange={(v) => setSettings({ ...settings, default_votes_to_win: v })} testid="cf-admin-set-vw" />
              <NumField label="Récompense par défaut" v={settings.default_reward_amount} onChange={(v) => setSettings({ ...settings, default_reward_amount: Number(v) })} testid="cf-admin-set-rw" />
              <button
                data-testid="cf-admin-save-settings"
                onClick={saveSettings}
                disabled={busy}
                className="sticky mt-3 w-full bg-rose-600 text-white py-3 rounded-full font-bold disabled:bg-slate-300 shadow-lg"
                style={{ bottom: 'calc(12px + env(safe-area-inset-bottom, 0px))' }}
              >
                Enregistrer les réglages
              </button>
            </section>
          )}

          {tab === 'history' && (
            <section>
              <h4 className="font-bold text-slate-900 mb-2">Historique des cycles</h4>
              {history.length === 0 ? (
                <div className="text-sm text-slate-500">Aucun cycle pour l'instant.</div>
              ) : (
                <div className="space-y-2">
                  {history.map((h) => (
                    <div key={h.cycle_id} className="border rounded-lg p-3 text-sm" data-testid={`cf-admin-history-${h.cycle_number}`}>
                      <div className="flex items-center justify-between">
                        <div className="font-bold">Cycle #{h.cycle_number}</div>
                        <span className={`text-[10px] px-2 py-0.5 rounded-full ${
                          h.status === 'active' ? 'bg-emerald-100 text-emerald-700'
                          : h.status === 'completed' ? 'bg-amber-100 text-amber-700'
                          : 'bg-slate-100 text-slate-600'
                        }`}>{h.status}</span>
                      </div>
                      <div className="text-xs text-slate-500 mt-1">
                        Seuil {h.threshold_projects} · Votes/win {h.votes_to_win} · Récompense {fmt(h.reward_amount)} {h.reward_currency}
                      </div>
                      {h.winner_user_id && (
                        <div className="text-xs text-emerald-700 mt-0.5">🏆 Gagnant désigné</div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

function NumField({ label, v, onChange, testid }) {
  return (
    <div>
      <label className="text-xs font-semibold text-slate-700">{label}</label>
      <input
        type="number"
        value={v}
        onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
        data-testid={testid}
        className="w-full mt-1 px-3 py-2 border rounded-lg text-sm" />
    </div>
  );
}

// ─── Main page ───────────────────────────────────────────────────────────
export default function CrowdfundingModule({ onBack }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin' || user?.role === 'superadmin';

  const [state, setState] = useState(null);
  const [projects, setProjects] = useState([]);
  const [me, setMe] = useState(null);
  const [sort, setSort] = useState('votes');
  const [showCreate, setShowCreate] = useState(false);
  const [showAdmin, setShowAdmin] = useState(false);
  // iter239w — Conditions de participation obligatoires avant création.
  const [showTerms, setShowTerms] = useState(false);
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [editing, setEditing] = useState(null);   // project being edited
  const [loading, setLoading] = useState(true);
  const [voting, setVoting] = useState(null);  // current slug being voted
  const [celebration, setCelebration] = useState(null);  // {ownerName, shareUrls}

  const { data: engagement, refresh: refreshEngagement } = useEngagementState({
    enabled: !!user,
  });

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [s, p, mePromise] = await Promise.all([
        axios.get(`${API}/api/crowdfunding/state`, { withCredentials: true }),
        axios.get(`${API}/api/crowdfunding/projects?sort=${sort}`, { withCredentials: true }),
        user ? axios.get(`${API}/api/crowdfunding/me`, { withCredentials: true }) : Promise.resolve({ data: null }),
      ]);
      setState(s.data);
      setProjects(p.data);
      setMe(mePromise.data);
    } catch (e) {
      toast.error(extractErrorMessage(e, 'Chargement échoué.'));
    } finally { setLoading(false); }
  }, [sort, user]);

  useEffect(() => { reload(); }, [reload]);

  // iter239x — Listen to global PWA refresh button to re-fetch all data.
  useEffect(() => {
    const handler = () => reload();
    window.addEventListener('japap:refresh', handler);
    return () => window.removeEventListener('japap:refresh', handler);
  }, [reload]);

  const onVote = async (project) => {
    if (voting) return;
    setVoting(project.slug);
    try {
      const [voteRes, shareRes] = await Promise.all([
        axios.post(`${API}/api/crowdfunding/projects/${project.slug}/vote`, {}, { withCredentials: true }),
        axios.get(`${API}/api/crowdfunding/projects/${project.slug}/share`, { withCredentials: true }).catch(() => null),
      ]);
      const data = voteRes.data;
      trackEngagementEvent('vote', { project_id: project.project_id });
      // iter239x — Jury vote feedback
      if (data.is_jury_vote || data.vote_weight > 1) {
        toast.success(
          t('crowdfunding.jury_vote_success', {
            defaultValue: '🎖️ Ton vote de juré compte pour +{{weight}} !',
            weight: data.vote_weight,
          }),
          { duration: 5000 }
        );
      }
      if (data.won) {
        toast.success('🏆 Projet gagnant ! Récompense créditée.', { duration: 5000 });
      }
      setCelebration({
        ownerName: project.owner_name || 'l\'auteur',
        whatsappUrl: shareRes?.data?.whatsapp_url,
        shareUrl: shareRes?.data?.share_url,
        shareText: shareRes?.data?.share_text,
      });
      reload();
      refreshEngagement();
    } catch (e) {
      const detail = e?.response?.data?.detail;
      // iter239x — Login required: redirect to /login
      if (e?.response?.status === 401 && detail?.code === 'login_required') {
        const redirect = encodeURIComponent(`/crowdfunding/p/${project.slug}`);
        window.location.href = `/login?redirect=${redirect}`;
        return;
      }
      if (detail?.code === 'VOTES_NOT_OPEN') {
        toast.error(detail.message);
      } else {
        toast.error(extractErrorMessage(e, 'Vote impossible.'));
      }
    } finally { setVoting(null); }
  };

  const onShare = async (project) => {
    try {
      const { data } = await axios.get(
        `${API}/api/crowdfunding/projects/${project.slug}/share`,
        { withCredentials: true });
      trackEngagementEvent('share', { project_id: project.project_id });
      if (navigator.share) {
        try {
          await navigator.share({ title: project.title, text: data.share_text, url: data.share_url });
          refreshEngagement();
          return;
        } catch {}
      }
      window.open(data.whatsapp_url, '_blank');
      refreshEngagement();
    } catch (e) {
      toast.error(extractErrorMessage(e, 'Partage impossible.'));
    }
  };

  const onEngagementCta = (action) => {
    if (action === 'share' && me?.project) {
      onShare({ slug: me.project.slug, project_id: me.project.project_id, title: me.project.title });
    } else if (action === 'vote' && projects.length > 0) {
      // Scroll to first non-mine project
      const target = projects.find((p) => p.user_id !== user?.user_id);
      if (target) {
        const el = document.querySelector(`[data-testid="cf-project-card-${target.slug}"]`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    } else if (action === 'invite' && me?.project) {
      onShare({ slug: me.project.slug, project_id: me.project.project_id, title: me.project.title });
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 pb-24">
      <header className="sticky top-0 z-30 bg-white border-b">
        <div className="max-w-2xl mx-auto px-4 py-3 flex items-center gap-2">
          {onBack && (
            <button onClick={onBack} className="p-1.5 rounded-full hover:bg-slate-100" data-testid="cf-back-btn">
              <CaretLeft size={22} />
            </button>
          )}
          <h1 className="font-bold text-lg flex-1">Crowdfunding viral</h1>
          <button onClick={reload} className="p-1.5 rounded-full hover:bg-slate-100" data-testid="cf-reload-btn" aria-label="Rafraîchir">
            <ArrowsClockwise size={20} />
          </button>
          {isAdmin && (
            <button
              onClick={() => setShowAdmin(true)}
              data-testid="cf-admin-btn"
              className="bg-slate-900 text-white px-3 py-1.5 rounded-full text-xs font-semibold flex items-center gap-1">
              <Gear size={14} /> Admin
            </button>
          )}
        </div>
      </header>

      <main className="max-w-2xl mx-auto px-4 py-4 space-y-4">
        <CycleHeader state={state} onAdmin={() => setShowAdmin(true)} isAdmin={isAdmin} />

        {user && engagement?.message && (
          <EngagementBanner
            payload={engagement}
            onPrimaryAction={onEngagementCta}
            onDismiss={refreshEngagement}
          />
        )}

        {user && <MyDashboard
          me={me}
          engagement={engagement}
          onCreate={() => {
            // iter239w — Si conditions non acceptées dans cette session, on
            // affiche le modal Terms d'abord. Sinon on ouvre directement Create.
            if (termsAccepted) {
              setShowCreate(true);
            } else {
              setShowTerms(true);
            }
          }}
          onEdit={(p) => setEditing(p)}
          onDelete={async (p) => {
            if (!window.confirm('Supprimer définitivement ton projet ?')) return;
            try {
              await axios.delete(`${API}/api/crowdfunding/projects/${p.slug}`,
                                 { withCredentials: true });
              toast.success('Projet supprimé.');
              reload();
            } catch (e) {
              toast.error(extractErrorMessage(e, 'Suppression refusée.'));
            }
          }}
        />}

        <RecruiterPanel
          user={user}
          onShare={() => {
            // Scroll to first project so user can hit a Share CTA.
            const el = document.querySelector('[data-testid="cf-projects-list"]');
            if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
          }}
        />

        {/* iter239y — Hall of Fame des Jurés */}
        <JuryHallOfFame />

        {state && !state.between_cycles && (
          <div className="flex gap-2 items-center" data-testid="cf-sort-bar">
            <span className="text-xs text-slate-500">Trier :</span>
            <button
              onClick={() => setSort('votes')}
              data-testid="cf-sort-votes"
              className={`px-3 py-1 rounded-full text-xs font-semibold ${sort === 'votes' ? 'bg-rose-600 text-white' : 'bg-white border border-slate-300 text-slate-700'}`}>
              <ChartBar size={12} className="inline" /> Top votes
            </button>
            <button
              onClick={() => setSort('recent')}
              data-testid="cf-sort-recent"
              className={`px-3 py-1 rounded-full text-xs font-semibold ${sort === 'recent' ? 'bg-rose-600 text-white' : 'bg-white border border-slate-300 text-slate-700'}`}>
              Récents
            </button>
            <span className="ml-auto text-xs text-slate-500" data-testid="cf-projects-count">
              {projects.length} projet{projects.length > 1 ? 's' : ''}
            </span>
          </div>
        )}

        {loading ? (
          <div className="text-center py-12 text-slate-500" data-testid="cf-loading">Chargement…</div>
        ) : projects.length === 0 ? (
          <div className="text-center py-12 text-slate-500" data-testid="cf-empty">
            {state?.between_cycles ? 'Pas de cycle actif.' : 'Aucun projet pour l\'instant — sois le premier !'}
          </div>
        ) : (
          <div className="space-y-3" data-testid="cf-projects-list">
            {projects.map((p) => (
              <ProjectCard
                key={p.project_id}
                project={p}
                votesOpen={state?.cycle?.votes_open}
                votesToWin={state?.cycle?.minimum_votes_required || state?.cycle?.votes_to_win || 100}
                currentUserId={user?.user_id}
                voting={voting}
                onVote={onVote}
                onShare={onShare}
                isAdmin={isAdmin}
                onAdminChanged={reload} />
            ))}
          </div>
        )}
      </main>

      <CreateProjectModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={() => { setShowCreate(false); reload(); }}
        eligibility={me?.eligibility}
        termsAccepted={termsAccepted} />

      {/* iter239w — Modal Conditions obligatoires (scroll-to-bottom + checkbox) */}
      <CrowdfundingTermsModal
        open={showTerms}
        cycle={state?.cycle}
        onAccept={() => {
          setTermsAccepted(true);
          setShowTerms(false);
          setShowCreate(true);
        }}
        onDecline={() => setShowTerms(false)} />

      <AdminPanel
        open={showAdmin}
        onClose={() => setShowAdmin(false)}
        onChanged={reload}
        activeCycle={state?.cycle} />

      {celebration && (
        <VoteCelebration
          ownerName={celebration.ownerName}
          onClose={() => setCelebration(null)}
          onShareWhatsApp={() => {
            if (celebration.whatsappUrl) window.open(celebration.whatsappUrl, '_blank');
            setCelebration(null);
          }}
          onShareNative={async () => {
            if (navigator.share && celebration.shareUrl) {
              try {
                await navigator.share({
                  title: 'Soutien au projet JAPAP',
                  text: celebration.shareText,
                  url: celebration.shareUrl,
                });
              } catch {}
            } else if (celebration.whatsappUrl) {
              window.open(celebration.whatsappUrl, '_blank');
            }
            setCelebration(null);
          }}
        />
      )}

      {editing && (
        <EditProjectModal
          project={editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); reload(); }}
        />
      )}
    </div>
  );
}

// ─── Edit modal ──────────────────────────────────────────────────────────
function EditProjectModal({ project, onClose, onSaved }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    title: project.title || '',
    description: project.description || '',
    objective: project.objective || '',
    category: project.category || 'community',
    country_code: project.country_code || '',
    image_url: project.image_url || '',
  });
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      await axios.put(`${API}/api/crowdfunding/projects/${project.slug}`, form,
                      { withCredentials: true });
      toast.success('Projet mis à jour.');
      onSaved();
    } catch (err) {
      toast.error(extractErrorMessage(err, 'Mise à jour refusée.'));
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-end sm:items-center justify-center p-2" onClick={onClose}>
      <div className="bg-white rounded-t-2xl sm:rounded-2xl w-full max-w-lg max-h-[92vh] overflow-y-auto p-5"
           onClick={(e) => e.stopPropagation()} data-testid="cf-edit-modal">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-lg font-bold text-slate-900">Éditer mon projet</h3>
          <button onClick={onClose} className="p-1.5 rounded-full hover:bg-slate-100"><X size={20} /></button>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <input
            value={form.title} required minLength={4} maxLength={160}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            data-testid="cf-edit-title"
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm font-bold" />
          <textarea
            value={form.description} required minLength={20} maxLength={4000} rows={4}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            data-testid="cf-edit-description"
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm" />
          <input
            value={form.image_url} type="url" maxLength={500}
            onChange={(e) => setForm({ ...form, image_url: e.target.value })}
            data-testid="cf-edit-image"
            placeholder={t('crowdfunding.https')}
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm" />
          <button
            type="submit" disabled={busy}
            data-testid="cf-edit-submit"
            className="w-full bg-rose-600 text-white py-3 rounded-full font-bold disabled:bg-slate-300">
            {busy ? 'Enregistrement…' : 'Enregistrer'}
          </button>
        </form>
      </div>
    </div>
  );
}


// ─── iter239w — Modal Conditions de participation (obligatoires) ─────────
function CrowdfundingTermsModal({ open, cycle, onAccept, onDecline }) {
  const { t, i18n } = useTranslation();
  const [scrolledToBottom, setScrolledToBottom] = useState(false);
  const [checked, setChecked] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => {
    if (open) { setScrolledToBottom(false); setChecked(false); }
  }, [open]);

  if (!open) return null;

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    if (el.scrollHeight - el.scrollTop <= el.clientHeight + 12) {
      setScrolledToBottom(true);
    }
  };

  const fmtDate = (iso) => {
    if (!iso) return '—';
    try {
      return new Date(iso).toLocaleDateString(i18n.language || 'fr-FR', {
        day: '2-digit', month: 'long', year: 'numeric',
      });
    } catch { return iso; }
  };
  const fmtAmount = (n) => new Intl.NumberFormat(i18n.language || 'fr-FR').format(Number(n || 0));

  const ctx = {
    minimum: cycle?.minimum_votes_required ?? cycle?.votes_to_win ?? 100,
    duration_start: fmtDate(cycle?.started_at),
    duration_end: fmtDate(cycle?.ended_at),
    reward: fmtAmount(cycle?.reward_amount),
    currency: cycle?.reward_currency || 'XAF',
  };

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-end sm:items-center justify-center p-2"
         onClick={onDecline} data-testid="cf-terms-modal">
      <div
        className="bg-white rounded-t-2xl sm:rounded-2xl w-full max-w-lg flex flex-col"
        style={{ maxHeight: 'min(92vh, 92dvh)' }}
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 pt-5 pb-3 border-b border-slate-100 flex-shrink-0">
          <h3 className="text-lg font-bold text-slate-900">
            {t('crowdfunding.terms_title', { defaultValue: 'Conditions de participation' })}
          </h3>
          <button onClick={onDecline} aria-label={t('common.close', { defaultValue: 'Fermer' })}
                  className="p-1.5 rounded-full hover:bg-slate-100"><X size={20} /></button>
        </div>

        <div
          ref={scrollRef}
          onScroll={handleScroll}
          data-testid="cf-terms-body"
          className="flex-1 overflow-y-auto px-5 py-3 text-sm text-slate-700 space-y-3"
          style={{ WebkitOverflowScrolling: 'touch' }}>
          <p>{t('crowdfunding.terms_intro')}</p>

          <div>
            <h4 className="font-bold text-slate-900 mb-1">1. {t('crowdfunding.terms_section1_title')}</h4>
            <p>{t('crowdfunding.terms_section1_body', ctx)}</p>
          </div>
          <div>
            <h4 className="font-bold text-slate-900 mb-1">2. {t('crowdfunding.terms_section2_title')}</h4>
            <p>{t('crowdfunding.terms_section2_body')}</p>
          </div>
          <div>
            <h4 className="font-bold text-slate-900 mb-1">3. {t('crowdfunding.terms_section3_title')}</h4>
            <p>{t('crowdfunding.terms_section3_body')}</p>
          </div>
          <div>
            <h4 className="font-bold text-slate-900 mb-1">4. {t('crowdfunding.terms_section4_title')}</h4>
            <p>{t('crowdfunding.terms_section4_body')}</p>
          </div>
          <div>
            <h4 className="font-bold text-slate-900 mb-1">5. {t('crowdfunding.terms_section5_title')}</h4>
            <p>{t('crowdfunding.terms_section5_body')}</p>
          </div>
          <div style={{ height: 6 }} />
        </div>

        <div className="flex-shrink-0 border-t border-slate-100 bg-white px-5 pt-3"
             style={{ paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 12px)' }}>
          {!scrolledToBottom && (
            <p className="text-xs text-slate-500 text-center mb-2" data-testid="cf-terms-scroll-hint">
              {t('crowdfunding.terms_scroll_to_read', { defaultValue: '⬇️ Faites défiler pour lire toutes les conditions' })}
            </p>
          )}
          <label className={`flex items-start gap-2 mb-2 ${scrolledToBottom ? '' : 'opacity-40 cursor-not-allowed'}`}>
            <input
              type="checkbox"
              checked={checked}
              disabled={!scrolledToBottom}
              onChange={(e) => setChecked(e.target.checked)}
              data-testid="cf-terms-checkbox"
              className="mt-1" />
            <span className="text-sm text-slate-700">
              {t('crowdfunding.terms_checkbox_label')}
            </span>
          </label>
          <div className="flex gap-2">
            <button onClick={onDecline}
                    data-testid="cf-terms-decline"
                    className="flex-1 py-3 rounded-full border border-slate-300 text-slate-700 font-semibold">
              {t('crowdfunding.terms_decline', { defaultValue: 'Annuler' })}
            </button>
            <button onClick={onAccept}
                    disabled={!checked}
                    data-testid="cf-terms-accept"
                    className={`flex-[2] py-3 rounded-full font-bold text-white ${checked ? 'bg-rose-600 hover:bg-rose-700' : 'bg-slate-300 cursor-not-allowed'}`}>
              {t('crowdfunding.terms_accept', { defaultValue: 'J\'accepte et je continue' })}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── iter239w — Bloc d'actions admin sur un projet (approve / suspend / …)
function AdminProjectActions({ project, onChanged }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);

  const call = async (path, body = null, method = 'POST') => {
    const reasonNeeded = ['suspend', 'force-delete', 'disqualify'].some(s => path.endsWith(s));
    let reason = body?.reason;
    if (reasonNeeded && !reason) {
      reason = window.prompt(t('crowdfunding.admin_reason_prompt', {
        defaultValue: 'Motif (min 5 caractères) :',
      }));
      if (!reason || reason.trim().length < 5) {
        toast.error(t('crowdfunding.admin_reason_required', {
          defaultValue: 'Motif requis (5 caractères minimum).',
        }));
        return;
      }
      body = { reason: reason.trim() };
    }
    setBusy(true);
    try {
      const url = `${API}/api/crowdfunding/admin/projects/${project.slug}/${path}`;
      const cfg = { withCredentials: true };
      if (method === 'DELETE') {
        await axios.delete(url, { ...cfg, data: body || {} });
      } else {
        await axios.post(url, body || {}, cfg);
      }
      toast.success(t('crowdfunding.admin_action_ok', { defaultValue: 'Action effectuée.' }));
      if (onChanged) onChanged();
    } catch (e) {
      toast.error(extractErrorMessage(e, t('crowdfunding.admin_action_failed', { defaultValue: 'Action refusée.' })));
    } finally { setBusy(false); }
  };

  return (
    <div className="flex flex-wrap gap-1.5 mt-2" data-testid={`cf-admin-actions-${project.slug}`}>
      {project.status === 'pending_review' && (
        <button onClick={() => call('approve')} disabled={busy}
                data-testid={`cf-admin-approve-${project.slug}`}
                className="px-2.5 py-1 rounded-md bg-emerald-600 text-white text-xs font-semibold disabled:opacity-50">
          ✅ {t('crowdfunding.admin_approve', { defaultValue: 'Approuver' })}
        </button>
      )}
      {project.status === 'active' && (
        <button onClick={() => call('suspend')} disabled={busy}
                data-testid={`cf-admin-suspend-${project.slug}`}
                className="px-2.5 py-1 rounded-md bg-amber-500 text-white text-xs font-semibold disabled:opacity-50">
          ⏸️ {t('crowdfunding.admin_suspend', { defaultValue: 'Suspendre' })}
        </button>
      )}
      {project.status === 'suspended' && (
        <button onClick={() => call('reactivate')} disabled={busy}
                data-testid={`cf-admin-reactivate-${project.slug}`}
                className="px-2.5 py-1 rounded-md bg-blue-600 text-white text-xs font-semibold disabled:opacity-50">
          ▶️ {t('crowdfunding.admin_reactivate', { defaultValue: 'Réactiver' })}
        </button>
      )}
      {project.status !== 'deleted' && project.status !== 'winner' && (
        <button onClick={() => call('disqualify')} disabled={busy}
                data-testid={`cf-admin-disqualify-${project.slug}`}
                className="px-2.5 py-1 rounded-md bg-rose-600 text-white text-xs font-semibold disabled:opacity-50">
          🚫 {t('crowdfunding.admin_disqualify', { defaultValue: 'Disqualifier' })}
        </button>
      )}
      {project.status !== 'winner' && project.status !== 'deleted' && (
        <button onClick={() => call('force-delete', null, 'DELETE')} disabled={busy}
                data-testid={`cf-admin-delete-${project.slug}`}
                className="px-2.5 py-1 rounded-md bg-slate-900 text-white text-xs font-semibold disabled:opacity-50">
          🗑️ {t('crowdfunding.admin_delete', { defaultValue: 'Supprimer' })}
        </button>
      )}
    </div>
  );
}

// ─── iter239w — Panneau admin de contrôle du cycle actif ─────────────────
function CycleControlPanel({ cycle, onChanged }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    started_at: '', ended_at: '',
    minimum_votes_required: '', reward_amount: '',
    threshold_projects: '', reward_currency: '',
  });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!cycle) return;
    const toLocalDT = (iso) => {
      if (!iso) return '';
      try {
        const d = new Date(iso);
        const pad = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
      } catch { return ''; }
    };
    setForm({
      started_at: toLocalDT(cycle.started_at),
      ended_at: toLocalDT(cycle.ended_at),
      minimum_votes_required: String(cycle.minimum_votes_required ?? cycle.votes_to_win ?? ''),
      reward_amount: String(cycle.reward_amount ?? ''),
      threshold_projects: String(cycle.threshold_projects ?? ''),
      reward_currency: cycle.reward_currency || 'XAF',
    });
  }, [cycle]);

  const save = async () => {
    setBusy(true);
    try {
      const payload = {};
      if (form.started_at) payload.started_at = new Date(form.started_at).toISOString();
      if (form.ended_at) payload.ended_at = new Date(form.ended_at).toISOString();
      if (form.minimum_votes_required) payload.minimum_votes_required = parseInt(form.minimum_votes_required, 10);
      if (form.reward_amount) payload.reward_amount = parseFloat(form.reward_amount);
      if (form.threshold_projects) payload.threshold_projects = parseInt(form.threshold_projects, 10);
      if (form.reward_currency) payload.reward_currency = form.reward_currency;
      await axios.put(`${API}/api/crowdfunding/admin/cycles/active`, payload, { withCredentials: true });
      toast.success(t('crowdfunding.admin_action_ok', { defaultValue: 'Cycle mis à jour.' }));
      if (onChanged) onChanged();
    } catch (e) {
      toast.error(extractErrorMessage(e, t('crowdfunding.admin_action_failed', { defaultValue: 'Mise à jour refusée.' })));
    } finally { setBusy(false); }
  };

  if (!cycle) return (
    <div className="text-sm text-slate-500" data-testid="cf-admin-cycle-empty">
      {t('crowdfunding.admin_no_cycle', { defaultValue: 'Aucun cycle actif.' })}
    </div>
  );

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4" data-testid="cf-admin-cycle-panel">
      <h4 className="text-sm font-bold text-slate-900 mb-3">
        {t('crowdfunding.admin_cycle_control', { defaultValue: 'Contrôle du cycle' })}
      </h4>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <div>
          <label className="text-xs font-semibold text-slate-700">{t('crowdfunding.admin_start_date', { defaultValue: 'Date de début' })}</label>
          <input type="datetime-local" value={form.started_at}
                 onChange={(e) => setForm({ ...form, started_at: e.target.value })}
                 data-testid="cf-admin-started-at"
                 className="w-full mt-1 px-2 py-1.5 border border-slate-300 rounded-lg text-sm" />
        </div>
        <div>
          <label className="text-xs font-semibold text-slate-700">{t('crowdfunding.admin_end_date', { defaultValue: 'Date de fin' })}</label>
          <input type="datetime-local" value={form.ended_at}
                 onChange={(e) => setForm({ ...form, ended_at: e.target.value })}
                 data-testid="cf-admin-ended-at"
                 className="w-full mt-1 px-2 py-1.5 border border-slate-300 rounded-lg text-sm" />
        </div>
        <div>
          <label className="text-xs font-semibold text-slate-700">{t('crowdfunding.minimum_votes_required', { defaultValue: 'Minimum de votes pour gagner' })}</label>
          <input type="number" min={2} value={form.minimum_votes_required}
                 onChange={(e) => setForm({ ...form, minimum_votes_required: e.target.value })}
                 data-testid="cf-admin-min-votes"
                 className="w-full mt-1 px-2 py-1.5 border border-slate-300 rounded-lg text-sm" />
        </div>
        <div>
          <label className="text-xs font-semibold text-slate-700">{t('crowdfunding.reward', { defaultValue: 'Prix à gagner' })}</label>
          <input type="number" min={0} value={form.reward_amount}
                 onChange={(e) => setForm({ ...form, reward_amount: e.target.value })}
                 placeholder="50000 | 100000 | 1000000 | …"
                 data-testid="cf-admin-reward"
                 className="w-full mt-1 px-2 py-1.5 border border-slate-300 rounded-lg text-sm" />
        </div>
        <div>
          <label className="text-xs font-semibold text-slate-700">Threshold projets (avant ouverture votes)</label>
          <input type="number" min={2} value={form.threshold_projects}
                 onChange={(e) => setForm({ ...form, threshold_projects: e.target.value })}
                 data-testid="cf-admin-threshold"
                 className="w-full mt-1 px-2 py-1.5 border border-slate-300 rounded-lg text-sm" />
        </div>
        <div>
          <label className="text-xs font-semibold text-slate-700">Devise</label>
          <input type="text" maxLength={10} value={form.reward_currency}
                 onChange={(e) => setForm({ ...form, reward_currency: e.target.value.toUpperCase() })}
                 data-testid="cf-admin-currency"
                 className="w-full mt-1 px-2 py-1.5 border border-slate-300 rounded-lg text-sm uppercase" />
        </div>
      </div>
      <button onClick={save} disabled={busy}
              data-testid="cf-admin-save-cycle"
              className="w-full mt-3 bg-indigo-700 text-white py-2.5 rounded-lg font-bold disabled:bg-slate-300">
        💾 {busy ? '…' : t('crowdfunding.admin_save_cycle', { defaultValue: 'Enregistrer les modifications' })}
      </button>
    </div>
  );
}

// iter239w — Exporté pour tests / réutilisation depuis AdminPage si besoin.
export { CrowdfundingTermsModal, AdminProjectActions, CycleControlPanel };
