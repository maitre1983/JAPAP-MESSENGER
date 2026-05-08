import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { useAuth } from '@/context/AuthContext';
import { useTranslation } from 'react-i18next';
import {
  CaretLeft, Plus, MagnifyingGlass, Briefcase, MapPin, CurrencyDollar, Clock, X,
  CheckCircle, GraduationCap, Heart, Eye, ShareNetwork, Link as LinkIcon, PencilSimple, Trash, Copy
} from '@phosphor-icons/react';
import { toast } from 'sonner';

const API = process.env.REACT_APP_BACKEND_URL;

const TYPE_LABELS = {
  full_time: 'Temps plein', part_time: 'Temps partiel',
  contract: 'Contrat', internship: 'Stage', freelance: 'Freelance',
};

const OFFER_TYPE_LABELS = {
  job: '💼 Emploi', mission: '🧩 Mission freelance',
  annonce: '📣 Annonce', scholarship: '🎓 Bourse d\'études',
};

// iter166 — Empty form template by offer type. Lets the dynamic form
// reset cleanly when the user toggles between job / mission / scholarship.
const EMPTY_FORM = {
  title: '', description: '', category: 'other', type: 'full_time',
  location: '', salary_min: '', salary_max: '', salary_usd: '',
  remote: false, offer_type: 'job', budget_usd: '', deadline: '',
  company_name: '', website: '', contact_email: '', contact_phone: '',
  university_name: '', country_of_study: '', level_of_study: 'master',
  field_of_study: '', application_url: '',
};

export default function JobsModule({ onBack, offersMode = false }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [view, setView] = useState('list');
  const [jobs, setJobs] = useState([]);
  const [categories, setCategories] = useState([]);
  const [levels, setLevels] = useState([]);
  const [activeCat, setActiveCat] = useState('');
  const [search, setSearch] = useState('');
  const [selectedJob, setSelectedJob] = useState(null);
  const [myPostings, setMyPostings] = useState([]);
  const [myApps, setMyApps] = useState([]);
  const [applications, setApplications] = useState([]);
  const [showCreate, setShowCreate] = useState(false);
  const [showApply, setShowApply] = useState(false);
  const [coverLetter, setCoverLetter] = useState('');
  const [offerType, setOfferType] = useState(offersMode ? '' : 'job');
  const [form, setForm] = useState(EMPTY_FORM);
  const [editing, setEditing] = useState(false);

  const load = useCallback(async () => {
    try {
      const qs = new URLSearchParams();
      if (activeCat) qs.set('category', activeCat);
      if (search) qs.set('search', search);
      if (offerType) qs.set('offer_type', offerType);
      const [cats, lvls, list] = await Promise.all([
        axios.get(`${API}/api/jobs/categories`, { withCredentials: true }),
        axios.get(`${API}/api/jobs/levels`, { withCredentials: true }),
        axios.get(`${API}/api/jobs/list?${qs.toString()}`, { withCredentials: true }),
      ]);
      setCategories(cats.data);
      setLevels(lvls.data);
      setJobs(list.data.jobs);
    } catch { /* silent */ }
  }, [activeCat, search, offerType]);

  useEffect(() => { load(); }, [load]);

  const openJob = async (id) => {
    try {
      const { data } = await axios.get(`${API}/api/jobs/${id}`, { withCredentials: true });
      setSelectedJob(data);
      if (data.is_owner) {
        try {
          const apps = await axios.get(`${API}/api/jobs/${id}/applications`, { withCredentials: true });
          setApplications(apps.data);
        } catch { setApplications([]); }
      } else {
        setApplications([]);
      }
      setView('detail');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Impossible d\'ouvrir l\'offre');
    }
  };

  const loadMy = async () => {
    try {
      const [p, a] = await Promise.all([
        axios.get(`${API}/api/jobs/my/postings`, { withCredentials: true }),
        axios.get(`${API}/api/jobs/my/applications`, { withCredentials: true }),
      ]);
      setMyPostings(p.data); setMyApps(a.data);
    } catch { /* silent */ }
  };

  const handleCreate = async (e) => {
    e.preventDefault();
    // iter166 — Build payload with only relevant fields per offer_type
    // to avoid sending empty strings (which trip 422 on Optional[float]).
    const isScholarship = form.offer_type === 'scholarship';
    const payload = {
      title: form.title.trim(),
      description: form.description,
      category: form.category,
      offer_type: form.offer_type,
      location: form.location,
      remote: form.remote,
      contact_email: form.contact_email || null,
      contact_phone: form.contact_phone || null,
      website: form.website || null,
    };
    if (isScholarship) {
      Object.assign(payload, {
        university_name: form.university_name,
        country_of_study: form.country_of_study,
        level_of_study: form.level_of_study,
        field_of_study: form.field_of_study,
        application_url: form.application_url,
        deadline: form.deadline || null,
      });
    } else {
      Object.assign(payload, {
        type: form.type,
        company_name: form.company_name || null,
        salary_usd: parseFloat(form.salary_usd) || null,
        salary_min: parseFloat(form.salary_min) || 0,
        salary_max: parseFloat(form.salary_max) || 0,
        budget_usd: parseFloat(form.budget_usd) || 0,
        deadline: form.deadline || null,
      });
    }
    try {
      const url = editing && selectedJob
        ? `${API}/api/jobs/${selectedJob.job_id}`
        : `${API}/api/jobs/create`;
      const method = editing ? 'put' : 'post';
      await axios[method](url, payload, { withCredentials: true });
      toast.success(editing ? 'Offre mise à jour' : OFFER_TYPE_LABELS[form.offer_type] + ' publiée');
      setShowCreate(false);
      setEditing(false);
      setForm(EMPTY_FORM);
      load();
      if (editing && selectedJob) openJob(selectedJob.job_id);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur');
    }
  };

  const handleApply = async (e) => {
    e.preventDefault();
    try {
      await axios.post(`${API}/api/jobs/${selectedJob.job_id}/apply`,
        { cover_letter: coverLetter }, { withCredentials: true });
      toast.success('Candidature envoyée !');
      setShowApply(false);
      setCoverLetter('');
      openJob(selectedJob.job_id);
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const handleLike = async () => {
    if (!selectedJob) return;
    try {
      const { data } = await axios.post(
        `${API}/api/jobs/${selectedJob.job_id}/like`, {}, { withCredentials: true });
      setSelectedJob((j) => j ? { ...j, has_liked: data.liked, likes_count: data.likes_count } : j);
    } catch { /* silent */ }
  };

  const handleDelete = async () => {
    if (!selectedJob) return;
    if (!window.confirm(`Supprimer "${selectedJob.title}" ? Cette action est irréversible.`)) return;
    try {
      await axios.delete(`${API}/api/jobs/${selectedJob.job_id}`, { withCredentials: true });
      toast.success('Offre supprimée');
      setView('list');
      setSelectedJob(null);
      load();
    } catch (err) { toast.error(err.response?.data?.detail || 'Erreur'); }
  };

  const startEdit = () => {
    if (!selectedJob) return;
    setForm({
      ...EMPTY_FORM,
      ...selectedJob,
      salary_min: selectedJob.salary_min || '',
      salary_max: selectedJob.salary_max || '',
      salary_usd: selectedJob.salary_usd || '',
      budget_usd: selectedJob.budget_usd || '',
      remote: !!selectedJob.remote,
    });
    setEditing(true);
    setShowCreate(true);
  };

  const shareLink = () => {
    if (!selectedJob) return '';
    return `${window.location.origin}/jobs/${selectedJob.job_id}`;
  };

  const copyShareLink = async () => {
    try {
      await navigator.clipboard.writeText(shareLink());
      toast.success('Lien copié');
    } catch { toast.error('Impossible de copier'); }
  };

  const shareOn = (platform) => {
    if (!selectedJob) return;
    const url = encodeURIComponent(shareLink());
    const text = encodeURIComponent(`${selectedJob.title} — sur JAPAP`);
    const targets = {
      whatsapp: `https://wa.me/?text=${text}%20${url}`,
      facebook: `https://www.facebook.com/sharer/sharer.php?u=${url}`,
      telegram: `https://t.me/share/url?url=${url}&text=${text}`,
    };
    window.open(targets[platform], '_blank', 'noopener,noreferrer');
  };

  const fmtMoney = (amount, ccy) => {
    if (!amount) return null;
    try {
      return new Intl.NumberFormat('fr-FR', {
        maximumFractionDigits: 0,
      }).format(parseFloat(amount)) + ' ' + (ccy || 'USD');
    } catch { return `${amount} ${ccy || 'USD'}`; }
  };

  // ═══════════════ DETAIL VIEW (premium) ═══════════════
  if (view === 'detail' && selectedJob) {
    const sj = selectedJob;
    const isScholarship = sj.offer_type === 'scholarship';
    const orgName = isScholarship ? sj.university_name : sj.company_name;
    return (
      <div className="p-4 sm:p-6 max-w-3xl mx-auto pb-32" data-testid="job-detail">
        <button onClick={() => setView('list')}
          className="text-xs font-['Manrope'] mb-3 flex items-center gap-1 hover:underline"
          style={{ color: 'var(--jp-text-muted)' }} data-testid="back-to-jobs">
          <CaretLeft size={14} /> Retour
        </button>

        <div className="jp-card-elevated p-5 sm:p-7">
          {/* Header */}
          <div className="flex items-start justify-between gap-3 mb-2">
            <div className="flex-1 min-w-0">
              <span className="jp-badge jp-badge-primary text-[10px] mb-2 inline-block">
                {OFFER_TYPE_LABELS[sj.offer_type] || sj.offer_type}
              </span>
              <h2 className="font-['Outfit'] text-2xl sm:text-3xl font-extrabold leading-tight"
                style={{ color: 'var(--jp-text)' }} data-testid="job-detail-title">
                {sj.title}
              </h2>
              {orgName && (
                <p className="text-sm font-['Manrope'] mt-1.5 font-semibold"
                  style={{ color: 'var(--jp-primary)' }} data-testid="job-detail-org">
                  {isScholarship ? <GraduationCap size={16} className="inline mr-1" /> : <Briefcase size={16} className="inline mr-1" />}
                  {orgName}
                </p>
              )}
              <p className="text-xs font-['Manrope'] mt-1" style={{ color: 'var(--jp-text-muted)' }}>
                Publié par {sj.first_name} {sj.last_name}
              </p>
            </div>
            {sj.is_owner && (
              <div className="flex flex-col gap-1.5">
                <button onClick={startEdit} className="jp-btn jp-btn-ghost text-xs py-1 px-2"
                  data-testid="job-edit-btn" title="Modifier">
                  <PencilSimple size={14} />
                </button>
                <button onClick={handleDelete} className="jp-btn jp-btn-ghost text-xs py-1 px-2"
                  style={{ color: '#E01C2E' }} data-testid="job-delete-btn" title="Supprimer">
                  <Trash size={14} />
                </button>
              </div>
            )}
          </div>

          {/* Meta */}
          <div className="flex gap-2 sm:gap-3 flex-wrap text-xs font-['Manrope'] mb-5"
            style={{ color: 'var(--jp-text-secondary)' }}>
            {sj.location && (
              <span className="flex items-center gap-1"><MapPin size={14} /> {sj.location}</span>
            )}
            {isScholarship && sj.country_of_study && (
              <span className="flex items-center gap-1"><MapPin size={14} /> Pays : {sj.country_of_study}</span>
            )}
            {!isScholarship && parseFloat(sj.salary_usd || 0) > 0 && (
              <span className="flex items-center gap-1 font-semibold" style={{ color: '#10B981' }}
                data-testid="job-detail-salary">
                <CurrencyDollar size={14} />
                {fmtMoney(sj.salary_usd, 'USD')}
                {sj.display_currency && sj.display_currency !== 'USD' && (
                  <span className="opacity-70 ml-1">≈ {fmtMoney(sj.salary_local, sj.display_currency)}</span>
                )}
              </span>
            )}
            {sj.remote && <span className="jp-badge jp-badge-success">Télétravail</span>}
            {sj.deadline && (
              <span className="flex items-center gap-1"><Clock size={14} />
                Limite : {new Date(sj.deadline).toLocaleDateString('fr-FR')}
              </span>
            )}
            {isScholarship && sj.level_of_study && (
              <span className="jp-badge jp-badge-neutral text-[10px]">
                Niveau : {(levels.find(l => l.id === sj.level_of_study) || {}).name || sj.level_of_study}
              </span>
            )}
            {isScholarship && sj.field_of_study && (
              <span className="flex items-center gap-1">📚 {sj.field_of_study}</span>
            )}
          </div>

          {/* Engagement bar */}
          <div className="flex items-center gap-3 sm:gap-5 py-3 border-t border-b mb-5"
            style={{ borderColor: 'var(--jp-border)' }} data-testid="job-engagement">
            <button onClick={handleLike} data-testid="job-like-btn"
              className="flex items-center gap-1.5 text-sm font-['Manrope'] transition-transform active:scale-90"
              style={{ color: sj.has_liked ? '#E01C2E' : 'var(--jp-text-muted)' }}>
              <Heart size={20} weight={sj.has_liked ? 'fill' : 'regular'} />
              <span className="font-semibold">{sj.likes_count || 0}</span>
            </button>
            <span className="flex items-center gap-1.5 text-sm font-['Manrope']"
              style={{ color: 'var(--jp-text-muted)' }} data-testid="job-views">
              <Eye size={20} /> {sj.views_count || 0}
            </span>
            <span className="flex items-center gap-1.5 text-sm font-['Manrope']"
              style={{ color: 'var(--jp-text-muted)' }}>
              <Briefcase size={18} /> {sj.applications_count || 0}
            </span>
            <div className="ml-auto flex items-center gap-1.5">
              <button onClick={copyShareLink} title={t('jobs.copier_le_lien')}
                data-testid="job-share-copy"
                className="p-1.5 rounded-full hover:bg-[var(--jp-surface-secondary)]">
                <Copy size={18} style={{ color: 'var(--jp-text-muted)' }} />
              </button>
              <button onClick={() => shareOn('whatsapp')} title={t('jobs.whatsapp')}
                data-testid="job-share-whatsapp"
                className="p-1.5 rounded-full hover:bg-green-50">
                <ShareNetwork size={18} style={{ color: '#25D366' }} />
              </button>
              <button onClick={() => shareOn('facebook')} title={t('jobs.facebook')}
                data-testid="job-share-facebook"
                className="p-1.5 rounded-full hover:bg-blue-50">
                <ShareNetwork size={18} style={{ color: '#1877F2' }} />
              </button>
              <button onClick={() => shareOn('telegram')} title={t('jobs.telegram')}
                data-testid="job-share-telegram"
                className="p-1.5 rounded-full hover:bg-sky-50">
                <ShareNetwork size={18} style={{ color: '#0088CC' }} />
              </button>
            </div>
          </div>

          {/* Description */}
          <h3 className="font-['Outfit'] text-base font-bold mb-2" style={{ color: 'var(--jp-text)' }}>
            Description
          </h3>
          <p className="text-sm font-['Manrope'] whitespace-pre-wrap mb-5"
            style={{ color: 'var(--jp-text)' }}>
            {sj.description || 'Pas de description.'}
          </p>

          {/* Contact */}
          {(sj.website || sj.contact_email || sj.contact_phone || sj.application_url) && (
            <div className="rounded-xl p-4 mb-5"
              style={{ background: 'var(--jp-surface-secondary)' }} data-testid="job-contact">
              <h3 className="font-['Outfit'] text-sm font-bold mb-2"
                style={{ color: 'var(--jp-text)' }}>
                {isScholarship ? 'Candidature' : 'Contact'}
              </h3>
              <div className="space-y-1.5 text-xs font-['Manrope']" style={{ color: 'var(--jp-text)' }}>
                {sj.application_url && (
                  <a href={sj.application_url} target="_blank" rel="noopener noreferrer"
                    className="flex items-center gap-1.5 underline" style={{ color: 'var(--jp-primary)' }}
                    data-testid="job-application-url">
                    <LinkIcon size={12} /> {sj.application_url}
                  </a>
                )}
                {sj.website && (
                  <a href={sj.website} target="_blank" rel="noopener noreferrer"
                    className="flex items-center gap-1.5 underline" style={{ color: 'var(--jp-primary)' }}>
                    <LinkIcon size={12} /> {sj.website}
                  </a>
                )}
                {sj.contact_email && (
                  <a href={`mailto:${sj.contact_email}`} className="flex items-center gap-1.5 underline"
                    style={{ color: 'var(--jp-primary)' }}>
                    ✉️ {sj.contact_email}
                  </a>
                )}
                {sj.contact_phone && (
                  <a href={`tel:${sj.contact_phone}`} className="flex items-center gap-1.5 underline"
                    style={{ color: 'var(--jp-primary)' }}>
                    📞 {sj.contact_phone}
                  </a>
                )}
              </div>
            </div>
          )}

          {/* Actions */}
          {!sj.is_owner && !sj.has_applied && !isScholarship && (
            <button onClick={() => setShowApply(true)}
              className="jp-btn jp-btn-primary jp-btn-lg jp-btn-full" data-testid="apply-btn">
              Postuler
            </button>
          )}
          {!sj.is_owner && sj.has_applied && (
            <div className="jp-alert jp-alert-success">
              <CheckCircle size={16} className="inline mr-1" /> Vous avez déjà postulé.
            </div>
          )}
          {isScholarship && sj.application_url && !sj.is_owner && (
            <a href={sj.application_url} target="_blank" rel="noopener noreferrer"
              className="jp-btn jp-btn-primary jp-btn-lg jp-btn-full"
              data-testid="scholarship-apply-link">
              <LinkIcon size={16} /> Candidater sur le site
            </a>
          )}

          {/* Owner: applications */}
          {sj.is_owner && applications.length > 0 && (
            <div className="mt-6">
              <h4 className="font-['Outfit'] text-sm font-bold mb-3" style={{ color: 'var(--jp-text)' }}>
                Candidatures ({applications.length})
              </h4>
              <div className="space-y-2">
                {applications.map(a => (
                  <div key={a.app_id} className="p-3 rounded-xl"
                    style={{ background: 'var(--jp-surface-secondary)' }} data-testid={`app-${a.app_id}`}>
                    <div className="flex items-center gap-3 mb-2">
                      <div className="jp-avatar jp-avatar-sm"
                        style={{ background: 'var(--jp-primary)', color: '#fff' }}>
                        {a.applicant.avatar
                          ? <img src={a.applicant.avatar} alt="" />
                          : (a.applicant.name[0] || '?').toUpperCase()}
                      </div>
                      <div className="flex-1">
                        <p className="text-sm font-semibold font-['Manrope']"
                          style={{ color: 'var(--jp-text)' }}>{a.applicant.name}</p>
                        <p className="text-[10px] font-['Manrope']"
                          style={{ color: 'var(--jp-text-muted)' }}>{a.applicant.email}</p>
                      </div>
                      <span className="jp-badge jp-badge-neutral text-[10px]">{a.status}</span>
                    </div>
                    {a.cover_letter && (
                      <p className="text-xs font-['Manrope'] pl-10"
                        style={{ color: 'var(--jp-text-secondary)' }}>"{a.cover_letter}"</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Apply Modal */}
        {showApply && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div className="absolute inset-0 bg-black/50" onClick={() => setShowApply(false)} />
            <div className="relative bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl"
              data-testid="apply-modal">
              <div className="flex justify-between items-center mb-4">
                <h3 className="font-['Outfit'] text-lg font-bold"
                  style={{ color: 'var(--jp-text)' }}>{t('jobs.candidature')}</h3>
                <button onClick={() => setShowApply(false)}><X size={18} /></button>
              </div>
              <form onSubmit={handleApply} className="space-y-4">
                <div>
                  <label className="jp-label">Lettre de motivation</label>
                  <textarea value={coverLetter} onChange={e => setCoverLetter(e.target.value)}
                    className="jp-input text-sm resize-none" style={{ height: '120px' }}
                    placeholder="Pourquoi postulez-vous ?" data-testid="cover-letter" />
                </div>
                <button type="submit" className="jp-btn jp-btn-primary jp-btn-full"
                  data-testid="submit-apply">Envoyer</button>
              </form>
            </div>
          </div>
        )}

        {showCreate && <CreateJobModal
          form={form} setForm={setForm} categories={categories} levels={levels}
          editing={editing}
          onClose={() => { setShowCreate(false); setEditing(false); setForm(EMPTY_FORM); }}
          onSubmit={handleCreate} offersMode={offersMode} />}
      </div>
    );
  }

  // ═══════════════ MY JOBS VIEW ═══════════════
  if (view === 'my') {
    return (
      <div className="p-6 max-w-4xl mx-auto pb-24" data-testid="my-jobs-page">
        <button onClick={() => setView('list')}
          className="text-xs font-['Manrope'] mb-3 flex items-center gap-1"
          style={{ color: 'var(--jp-text-muted)' }}>
          <CaretLeft size={14} /> Retour offres
        </button>
        <h2 className="font-['Outfit'] text-xl font-bold mb-4"
          style={{ color: 'var(--jp-text)' }}>{t('jobs.mes_offres_candidatures')}</h2>

        <section className="mb-6">
          <h3 className="font-['Outfit'] text-sm font-bold mb-2"
            style={{ color: 'var(--jp-text-secondary)' }}>Mes offres ({myPostings.length})</h3>
          <div className="space-y-2">
            {myPostings.map(j => (
              <button key={j.job_id} onClick={() => openJob(j.job_id)}
                className="w-full jp-card p-3 text-left jp-card-hover"
                data-testid={`my-posting-${j.job_id}`}>
                <p className="text-sm font-bold font-['Manrope']"
                  style={{ color: 'var(--jp-text)' }}>{j.title}</p>
                <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
                  {OFFER_TYPE_LABELS[j.offer_type] || j.offer_type}
                  {' · '}<Eye size={10} className="inline" /> {j.views_count || 0}
                  {' · '}<Heart size={10} className="inline" /> {j.likes_count || 0}
                  {' · '}{j.applications_count || 0} candidat(s)
                </p>
              </button>
            ))}
            {myPostings.length === 0 && (
              <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>Aucune offre.</p>
            )}
          </div>
        </section>

        <section>
          <h3 className="font-['Outfit'] text-sm font-bold mb-2"
            style={{ color: 'var(--jp-text-secondary)' }}>Mes candidatures ({myApps.length})</h3>
          <div className="space-y-2">
            {myApps.map(a => (
              <button key={a.app_id} onClick={() => openJob(a.job_id)}
                className="w-full jp-card p-3 text-left jp-card-hover"
                data-testid={`my-app-${a.app_id}`}>
                <p className="text-sm font-bold font-['Manrope']"
                  style={{ color: 'var(--jp-text)' }}>{a.job_title}</p>
                <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>Statut : {a.status}</p>
              </button>
            ))}
            {myApps.length === 0 && (
              <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>Aucune candidature.</p>
            )}
          </div>
        </section>
      </div>
    );
  }

  // ═══════════════ LIST VIEW ═══════════════
  return (
    <div className="p-4 sm:p-6 max-w-6xl mx-auto pb-24" data-testid="jobs-page">
      <div className="flex items-start sm:items-center justify-between gap-3 mb-6 flex-wrap">
        <div>
          <button onClick={onBack}
            className="text-xs font-['Manrope'] mb-1 flex items-center gap-1"
            style={{ color: 'var(--jp-text-muted)' }} data-testid="back-to-services-from-jobs">
            <CaretLeft size={14} /> Services
          </button>
          <h1 className="font-['Outfit'] text-2xl font-bold"
            style={{ color: 'var(--jp-text)' }}>{offersMode ? 'Offres' : 'Jobs'}</h1>
          <p className="text-xs" style={{ color: 'var(--jp-text-secondary)' }}>
            {offersMode
              ? 'Emplois, missions, annonces et bourses d\'études. Publiez gratuitement.'
              : 'Trouvez ou publiez une offre d\'emploi.'}
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => { loadMy(); setView('my'); }}
            className="jp-btn jp-btn-ghost" data-testid="my-jobs-btn">Mes jobs</button>
          <button onClick={() => { setEditing(false); setForm(EMPTY_FORM); setShowCreate(true); }}
            className="jp-btn jp-btn-primary" data-testid="create-job-btn">
            <Plus size={16} /> Publier
          </button>
        </div>
      </div>

      <div className="flex gap-3 mb-4 flex-wrap items-center">
        <div className="relative flex-1 min-w-[220px]">
          <MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2"
            size={16} style={{ color: 'var(--jp-text-muted)' }} />
          <input value={search} onChange={e => setSearch(e.target.value)}
            className="jp-input text-sm" style={{ paddingLeft: '36px' }}
            placeholder="Rechercher..." data-testid="jobs-search" />
        </div>
        {offersMode && (
          <div className="flex gap-2 overflow-x-auto pb-1" data-testid="offers-type-filter">
            {[
              { v: '', label: 'Tout' },
              { v: 'job', label: '💼 Emplois' },
              { v: 'mission', label: '🧩 Missions' },
              { v: 'scholarship', label: '🎓 Bourses' },
              { v: 'annonce', label: '📣 Annonces' },
            ].map(({ v, label }) => (
              <button key={v} onClick={() => setOfferType(v)}
                data-testid={`offers-type-${v || 'all'}`}
                className={`px-3 py-1.5 rounded-full text-xs font-bold whitespace-nowrap ${offerType === v ? 'jp-btn-primary' : 'jp-btn-ghost'}`}>
                {label}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex gap-2 mb-6 overflow-x-auto jp-scrollbar pb-2"
        data-testid="jobs-categories">
        <button onClick={() => setActiveCat('')} data-testid="jobs-cat-all"
          className="px-4 py-2 rounded-full text-xs font-bold font-['Manrope'] whitespace-nowrap"
          style={{
            background: !activeCat ? 'var(--jp-primary)' : 'var(--jp-surface-secondary)',
            color: !activeCat ? '#fff' : 'var(--jp-text)',
          }}>{t('jobs.toutes')}</button>
        {categories.map(c => (
          <button key={c.id} onClick={() => setActiveCat(c.id)}
            data-testid={`jobs-cat-${c.id}`}
            className="px-4 py-2 rounded-full text-xs font-bold font-['Manrope'] whitespace-nowrap"
            style={{
              background: activeCat === c.id ? c.color : 'var(--jp-surface-secondary)',
              color: activeCat === c.id ? '#fff' : 'var(--jp-text)',
            }}>{c.name}</button>
        ))}
      </div>

      {/* Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {jobs.map(j => {
          const isScholarship = j.offer_type === 'scholarship';
          const orgName = isScholarship ? j.university_name : j.company_name;
          return (
            <button key={j.job_id} onClick={() => openJob(j.job_id)}
              className="jp-card p-4 text-left jp-card-hover" data-testid={`job-${j.job_id}`}>
              <div className="flex items-start justify-between gap-2 mb-2">
                <div className="flex-1 min-w-0">
                  {isScholarship && (
                    <span className="inline-flex items-center gap-1 text-[10px] font-bold mb-1.5 px-1.5 py-0.5 rounded-full"
                      style={{ background: '#FEF3C7', color: '#92400E' }}>
                      <GraduationCap size={11} /> Bourse
                    </span>
                  )}
                  <h4 className="font-['Outfit'] font-bold text-sm mb-1 line-clamp-1"
                    style={{ color: 'var(--jp-text)' }}>{j.title}</h4>
                  {orgName && (
                    <p className="text-xs font-['Manrope'] font-semibold"
                      style={{ color: 'var(--jp-primary)' }}>{orgName}</p>
                  )}
                  <p className="text-[10px] font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
                    {j.first_name} {j.last_name}
                  </p>
                </div>
                {!isScholarship && j.type && (
                  <span className="jp-badge jp-badge-neutral text-[10px]">{TYPE_LABELS[j.type]}</span>
                )}
              </div>
              <p className="text-xs font-['Manrope'] line-clamp-2 mb-2"
                style={{ color: 'var(--jp-text-secondary)' }}>{j.description}</p>
              <div className="flex gap-2 flex-wrap text-[10px] font-['Manrope']"
                style={{ color: 'var(--jp-text-muted)' }}>
                {j.location && (
                  <span className="flex items-center gap-1"><MapPin size={10} /> {j.location}</span>
                )}
                {!isScholarship && parseFloat(j.salary_usd || 0) > 0 && (
                  <span className="flex items-center gap-1 font-bold" style={{ color: '#10B981' }}>
                    <CurrencyDollar size={10} /> {fmtMoney(j.salary_usd, 'USD')}
                  </span>
                )}
                {isScholarship && j.deadline && (
                  <span className="flex items-center gap-1" style={{ color: '#E01C2E' }}>
                    <Clock size={10} /> Limite : {new Date(j.deadline).toLocaleDateString('fr-FR')}
                  </span>
                )}
                {j.remote && <span style={{ color: '#10B981' }}>{t('jobs.teletravail')}</span>}
                <span className="flex items-center gap-1 ml-auto">
                  <Eye size={10} /> {j.views_count || 0} · <Heart size={10} /> {j.likes_count || 0}
                </span>
              </div>
            </button>
          );
        })}
        {jobs.length === 0 && (
          <div className="col-span-full jp-card p-12 text-center">
            <Briefcase size={40} className="mx-auto mb-3"
              style={{ color: 'var(--jp-text-muted)', opacity: 0.4 }} />
            <p className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Aucune offre.</p>
          </div>
        )}
      </div>

      {showCreate && <CreateJobModal
        form={form} setForm={setForm} categories={categories} levels={levels}
        editing={editing}
        onClose={() => { setShowCreate(false); setEditing(false); setForm(EMPTY_FORM); }}
        onSubmit={handleCreate} offersMode={offersMode} />}
    </div>
  );
}

/**
 * iter166 — Dynamic Create/Edit modal. The form schema mutates based on
 * `form.offer_type` so the user only sees the fields relevant to a job vs
 * a freelance mission vs a scholarship.
 */
function CreateJobModal({ form, setForm, categories, levels, editing, onClose, onSubmit, offersMode }) {
  const { t } = useTranslation();
  const isScholarship = form.offer_type === 'scholarship';
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative bg-white rounded-2xl max-w-lg w-full p-5 sm:p-6 shadow-2xl flex flex-col"
        style={{ maxHeight: 'calc(100vh - 32px)' }}
        data-testid="create-job-modal">
        <div className="flex justify-between items-center mb-3">
          <h3 className="font-['Outfit'] text-lg font-bold" style={{ color: 'var(--jp-text)' }}>
            {editing ? 'Modifier l\'offre' : 'Nouvelle offre'}
          </h3>
          <button onClick={onClose} aria-label={t('jobs.fermer')}><X size={18} /></button>
        </div>

        <form id="create-job-form" onSubmit={onSubmit} className="space-y-3 overflow-y-auto pr-1 flex-1">
          {offersMode && !editing && (
            <div>
              <label className="jp-label text-xs">Type d'offre</label>
              <select value={form.offer_type}
                onChange={e => setForm(f => ({ ...f, offer_type: e.target.value }))}
                className="jp-input text-sm" data-testid="job-form-offer-type">
                <option value="job">💼 Emploi (CDI/CDD/Stage)</option>
                <option value="mission">🧩 Mission freelance</option>
                <option value="scholarship">🎓 Bourse d'études</option>
                <option value="annonce">📣 Petite annonce</option>
              </select>
            </div>
          )}

          <input required value={form.title}
            onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
            placeholder={isScholarship ? 'Titre de la bourse' : 'Titre du poste'}
            className="jp-input text-sm" data-testid="job-form-title" />

          {/* Org name (dynamic label) */}
          {isScholarship ? (
            <input required value={form.university_name}
              onChange={e => setForm(f => ({ ...f, university_name: e.target.value }))}
              placeholder={t('jobs.nom_de_l_universite_organisme')}
              className="jp-input text-sm" data-testid="job-form-university" />
          ) : (
            <input value={form.company_name}
              onChange={e => setForm(f => ({ ...f, company_name: e.target.value }))}
              placeholder={t('jobs.nom_de_l_entreprise_optionnel')}
              className="jp-input text-sm" data-testid="job-form-company" />
          )}

          <textarea value={form.description}
            onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            placeholder="Description complète" className="jp-input text-sm resize-none"
            style={{ height: '90px' }} />

          <div className="grid grid-cols-2 gap-3">
            <select value={form.category}
              onChange={e => setForm(f => ({ ...f, category: e.target.value }))}
              className="jp-input text-sm" data-testid="job-form-category">
              {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            {isScholarship ? (
              <select value={form.level_of_study}
                onChange={e => setForm(f => ({ ...f, level_of_study: e.target.value }))}
                className="jp-input text-sm" data-testid="job-form-level">
                {levels.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
              </select>
            ) : form.offer_type === 'job' ? (
              <select value={form.type}
                onChange={e => setForm(f => ({ ...f, type: e.target.value }))}
                className="jp-input text-sm" data-testid="job-form-type">
                {Object.entries(TYPE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            ) : (
              <input type="date" value={form.deadline}
                onChange={e => setForm(f => ({ ...f, deadline: e.target.value }))}
                className="jp-input text-sm" data-testid="job-form-deadline" />
            )}
          </div>

          {/* Scholarship-specific extras */}
          {isScholarship && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <input value={form.country_of_study}
                  onChange={e => setForm(f => ({ ...f, country_of_study: e.target.value }))}
                  placeholder={t('jobs.pays_d_etude_ex_fr_us_ca')}
                  className="jp-input text-sm" data-testid="job-form-country" maxLength={80} />
                <input type="date" value={form.deadline}
                  onChange={e => setForm(f => ({ ...f, deadline: e.target.value }))}
                  placeholder="Date limite" className="jp-input text-sm"
                  data-testid="job-form-deadline" />
              </div>
              <input value={form.field_of_study}
                onChange={e => setForm(f => ({ ...f, field_of_study: e.target.value }))}
                placeholder={t('jobs.domaine_d_etude_ex_informatique_med')}
                className="jp-input text-sm" data-testid="job-form-field" />
              <input value={form.application_url}
                onChange={e => setForm(f => ({ ...f, application_url: e.target.value }))}
                placeholder={t('jobs.lien_de_candidature_https')}
                className="jp-input text-sm" data-testid="job-form-application-url" />
            </>
          )}

          <input value={form.location}
            onChange={e => setForm(f => ({ ...f, location: e.target.value }))}
            placeholder="Ville / localisation" className="jp-input text-sm" />

          {/* Salary fields — jobs/missions only */}
          {form.offer_type === 'mission' && (
            <input type="number" min="0" step="0.01" value={form.budget_usd}
              onChange={e => setForm(f => ({ ...f, budget_usd: e.target.value }))}
              placeholder={t('jobs.budget_total_usd')}
              className="jp-input text-sm" data-testid="job-form-budget" />
          )}
          {form.offer_type === 'job' && (
            <div>
              <label className="jp-label text-xs">Salaire (USD, mensuel)</label>
              <input type="number" min="0" step="0.01" value={form.salary_usd}
                onChange={e => setForm(f => ({ ...f, salary_usd: e.target.value }))}
                placeholder={t('jobs.ex_1500')}
                className="jp-input text-sm" data-testid="job-form-salary-usd" />
              <p className="text-[10px] mt-1" style={{ color: 'var(--jp-text-muted)' }}>
                Affiché automatiquement dans la devise locale du candidat (XAF, GHS...).
              </p>
            </div>
          )}

          {/* Contact fields (all types) */}
          <div className="pt-2 border-t" style={{ borderColor: 'var(--jp-border)' }}>
            <p className="text-[11px] font-bold uppercase tracking-wider mb-2"
              style={{ color: 'var(--jp-text-muted)' }}>{t('jobs.contact')}</p>
            <div className="space-y-2">
              <input type="email" value={form.contact_email}
                onChange={e => setForm(f => ({ ...f, contact_email: e.target.value }))}
                placeholder={t('jobs.email_de_contact')}
                className="jp-input text-sm" data-testid="job-form-email" />
              <input type="tel" value={form.contact_phone}
                onChange={e => setForm(f => ({ ...f, contact_phone: e.target.value }))}
                placeholder={t('jobs.telephone_optionnel')}
                className="jp-input text-sm" data-testid="job-form-phone" />
              {!isScholarship && (
                <input type="url" value={form.website}
                  onChange={e => setForm(f => ({ ...f, website: e.target.value }))}
                  placeholder={t('jobs.site_internet_optionnel')}
                  className="jp-input text-sm" data-testid="job-form-website" />
              )}
            </div>
          </div>

          {!isScholarship && (
            <label className="flex items-center gap-2 text-xs font-['Manrope']"
              style={{ color: 'var(--jp-text)' }}>
              <input type="checkbox" checked={form.remote}
                onChange={e => setForm(f => ({ ...f, remote: e.target.checked }))} />
              Télétravail possible
            </label>
          )}
        </form>

        {/* iter166 — sticky CTA so the Publier button stays visible on small
            screens (was clipped on Android <600px height + virtual keyboard). */}
        <div
          className="pt-3 mt-2 border-t"
          style={{
            borderColor: 'var(--jp-border)',
            paddingBottom: 'env(safe-area-inset-bottom, 0px)',
          }}
        >
          <button type="submit" form="create-job-form"
            className="jp-btn jp-btn-primary jp-btn-full" data-testid="submit-job">
            {editing ? 'Enregistrer' : 'Publier'}
          </button>
        </div>
      </div>
    </div>
  );
}
