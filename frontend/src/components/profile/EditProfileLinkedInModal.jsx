// iter240j — Modal d'édition des sections LinkedIn-style du profil.
// Sections: headline, bio, location, profession+company, skills,
// languages_spoken, experience[], education[], achievements[], links.
// Visibilité (public/privé) gérée via toggle séparé sur la page profil.
// ADDITIF : la modal existante (avatar/cover/identité) reste inchangée.
import { useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import { X, Plus, Trash } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;


const emptyExp = () => ({ title: '', company: '', period: '', description: '' });
const emptyEdu = () => ({ degree: '', school: '', year: '', description: '' });
const emptyAch = () => ({ title: '', date: '', description: '' });

export default function EditProfileLinkedInModal({ open, profile, onClose, onSaved }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({});
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open || !profile) return;
    setForm({
      headline: profile.headline || '',
      bio: profile.bio || '',
      location: profile.location || '',
      profession: profile.profession || '',
      company: profile.company || '',
      website_url: profile.website_url || '',
      linkedin_url: profile.linkedin_url || '',
      twitter_url: profile.twitter_url || '',
      skills: Array.isArray(profile.skills) ? profile.skills : [],
      languages_spoken: Array.isArray(profile.languages_spoken) ? profile.languages_spoken : [],
      experience: Array.isArray(profile.experience) ? profile.experience : [],
      education: Array.isArray(profile.education) ? profile.education : [],
      achievements: Array.isArray(profile.achievements) ? profile.achievements : [],
    });
  }, [open, profile]);

  if (!open) return null;

  const save = async () => {
    setSaving(true);
    try {
      await axios.put(`${API}/api/users/profile`, form, { withCredentials: true });
      toast.success(t('profile.save_success', { defaultValue: 'Profil mis à jour.' }));
      onSaved?.();
      onClose();
    } catch (e) {
      toast.error(e?.response?.data?.detail || t('profile.save_failed', { defaultValue: 'Échec de la mise à jour.' }));
    } finally {
      setSaving(false);
    }
  };

  const addTo = (key, blank) => setForm((f) => ({ ...f, [key]: [...(f[key] || []), blank()] }));
  const removeFrom = (key, idx) => setForm((f) => ({ ...f, [key]: f[key].filter((_, i) => i !== idx) }));
  const updateItem = (key, idx, patch) => setForm((f) => ({
    ...f, [key]: f[key].map((it, i) => i === idx ? { ...it, ...patch } : it),
  }));

  return (
    <div className="fixed inset-0 z-[70] bg-black/60 flex items-end sm:items-center justify-center p-2"
         data-testid="edit-profile-linkedin-modal" onClick={onClose}>
      <div className="bg-white rounded-t-2xl sm:rounded-2xl w-full max-w-2xl max-h-[92vh] overflow-y-auto flex flex-col"
           onClick={(e) => e.stopPropagation()}>
        <header className="sticky top-0 bg-white border-b border-slate-100 px-4 py-3 flex items-center justify-between z-10">
          <h2 className="text-base font-bold">{t('profile.edit', { defaultValue: 'Modifier le profil' })}</h2>
          <button onClick={onClose} data-testid="edit-profile-close" className="p-1 rounded-full hover:bg-slate-100">
            <X size={18} />
          </button>
        </header>

        <div className="p-4 space-y-5">
          <FieldText label={t('profile.headline', { defaultValue: 'Titre professionnel' })}
            v={form.headline} maxLength={220} onChange={(v) => setForm({ ...form, headline: v })}
            testId="edit-profile-headline" />
          <FieldTextarea label={t('profile.bio', { defaultValue: 'À propos' })}
            v={form.bio} maxLength={2000} onChange={(v) => setForm({ ...form, bio: v })}
            testId="edit-profile-bio" />

          <div className="grid sm:grid-cols-2 gap-3">
            <FieldText label={t('profile.location', { defaultValue: 'Localisation' })}
              v={form.location} onChange={(v) => setForm({ ...form, location: v })}
              testId="edit-profile-location" />
            <FieldText label={t('profile.profession', { defaultValue: 'Profession' })}
              v={form.profession} onChange={(v) => setForm({ ...form, profession: v })}
              testId="edit-profile-profession" />
            <FieldText label={t('profile.company', { defaultValue: 'Entreprise' })}
              v={form.company} onChange={(v) => setForm({ ...form, company: v })}
              testId="edit-profile-company" />
            <FieldText label={t('profile.website', { defaultValue: 'Site web' })}
              v={form.website_url} onChange={(v) => setForm({ ...form, website_url: v })}
              testId="edit-profile-website" placeholder="https://…" />
            <FieldText label="LinkedIn"
              v={form.linkedin_url} onChange={(v) => setForm({ ...form, linkedin_url: v })}
              testId="edit-profile-linkedin" placeholder="https://linkedin.com/in/…" />
            <FieldText label="X / Twitter"
              v={form.twitter_url} onChange={(v) => setForm({ ...form, twitter_url: v })}
              testId="edit-profile-twitter" placeholder="https://x.com/…" />
          </div>

          <ChipsField
            label={t('profile.skills', { defaultValue: 'Compétences' })}
            items={form.skills} max={15}
            onChange={(s) => setForm({ ...form, skills: s })}
            testId="edit-profile-skills" />
          <ChipsField
            label={t('profile.languages', { defaultValue: 'Langues parlées' })}
            items={form.languages_spoken}
            onChange={(s) => setForm({ ...form, languages_spoken: s })}
            testId="edit-profile-languages" />

          <DynamicList
            label={t('profile.experience', { defaultValue: 'Expériences' })}
            addLabel={t('profile.add_experience', { defaultValue: 'Ajouter une expérience' })}
            items={form.experience}
            onAdd={() => addTo('experience', emptyExp)}
            onRemove={(i) => removeFrom('experience', i)}
            onUpdate={(i, p) => updateItem('experience', i, p)}
            fields={[
              { key: 'title', label: t('profile.title', { defaultValue: 'Titre' }) },
              { key: 'company', label: t('profile.company', { defaultValue: 'Entreprise' }) },
              { key: 'period', label: t('profile.period', { defaultValue: 'Période' }) },
              { key: 'description', label: t('profile.description', { defaultValue: 'Description' }), textarea: true },
            ]}
            testId="edit-profile-experience" />
          <DynamicList
            label={t('profile.education', { defaultValue: 'Formation' })}
            addLabel={t('profile.add_education', { defaultValue: 'Ajouter une formation' })}
            items={form.education}
            onAdd={() => addTo('education', emptyEdu)}
            onRemove={(i) => removeFrom('education', i)}
            onUpdate={(i, p) => updateItem('education', i, p)}
            fields={[
              { key: 'degree', label: t('profile.degree', { defaultValue: 'Diplôme' }) },
              { key: 'school', label: t('profile.school', { defaultValue: 'École' }) },
              { key: 'year', label: t('profile.year', { defaultValue: 'Année' }) },
            ]}
            testId="edit-profile-education" />
          <DynamicList
            label={t('profile.achievements', { defaultValue: 'Réalisations' })}
            addLabel={t('profile.add_achievement', { defaultValue: 'Ajouter une réalisation' })}
            items={form.achievements}
            onAdd={() => addTo('achievements', emptyAch)}
            onRemove={(i) => removeFrom('achievements', i)}
            onUpdate={(i, p) => updateItem('achievements', i, p)}
            fields={[
              { key: 'title', label: t('profile.title', { defaultValue: 'Titre' }) },
              { key: 'date', label: t('profile.date', { defaultValue: 'Date' }) },
              { key: 'description', label: t('profile.description', { defaultValue: 'Description' }), textarea: true },
            ]}
            testId="edit-profile-achievements" />
        </div>

        <footer className="sticky bottom-0 bg-white border-t border-slate-100 px-4 py-3 flex gap-2"
                style={{ paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 12px)' }}>
          <button onClick={onClose}
                  className="flex-1 py-2 rounded-full border border-slate-300 font-bold">
            {t('profile.cancel', { defaultValue: 'Annuler' })}
          </button>
          <button onClick={save} disabled={saving}
                  data-testid="edit-profile-save"
                  className="flex-[2] py-2 rounded-full bg-rose-600 hover:bg-rose-700 disabled:bg-slate-300 text-white font-bold">
            {saving
              ? t('profile.saving', { defaultValue: 'Enregistrement…' })
              : t('profile.save', { defaultValue: 'Enregistrer' })}
          </button>
        </footer>
      </div>
    </div>
  );
}

function FieldText({ label, v, onChange, maxLength, placeholder, testId }) {
  return (
    <div>
      <label className="text-xs font-semibold text-slate-700">{label}</label>
      <input value={v || ''} onChange={(e) => onChange(e.target.value)}
             maxLength={maxLength} placeholder={placeholder} data-testid={testId}
             className="w-full mt-1 px-3 py-2 border border-slate-200 rounded-lg text-sm" />
    </div>
  );
}
function FieldTextarea({ label, v, onChange, maxLength, testId }) {
  return (
    <div>
      <label className="text-xs font-semibold text-slate-700">{label}</label>
      <textarea value={v || ''} onChange={(e) => onChange(e.target.value)}
                maxLength={maxLength} rows={4} data-testid={testId}
                className="w-full mt-1 px-3 py-2 border border-slate-200 rounded-lg text-sm resize-none" />
    </div>
  );
}
function ChipsField({ label, items, max, onChange, testId }) {
  const [draft, setDraft] = useState('');
  const { t } = useTranslation();
  const add = () => {
    const v = (draft || '').trim();
    if (!v) return;
    if (max && (items || []).length >= max) return;
    if ((items || []).includes(v)) return;
    onChange([...(items || []), v]);
    setDraft('');
  };
  return (
    <div data-testid={testId}>
      <label className="text-xs font-semibold text-slate-700">{label}{max ? ` (max ${max})` : ''}</label>
      <div className="flex flex-wrap gap-1.5 mt-1 mb-2">
        {(items || []).map((it, i) => (
          <span key={i} className="px-2 py-0.5 rounded-full bg-slate-100 text-xs font-semibold flex items-center gap-1">
            {it}
            <button type="button" onClick={() => onChange(items.filter((_, idx) => idx !== i))}
                    className="text-slate-500 hover:text-rose-600"><X size={10} /></button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input value={draft} onChange={(e) => setDraft(e.target.value)}
               onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }}
               placeholder={t('profile.add_chip', { defaultValue: 'Ajouter…' })}
               data-testid={`${testId}-input`}
               className="flex-1 px-3 py-1.5 border border-slate-200 rounded-lg text-sm" />
        <button type="button" onClick={add} data-testid={`${testId}-add`}
                className="px-3 rounded-full bg-rose-600 text-white text-xs font-bold">
          {t('profile.add', { defaultValue: 'Ajouter' })}
        </button>
      </div>
    </div>
  );
}
function DynamicList({ label, addLabel, items, fields, onAdd, onRemove, onUpdate, testId }) {
  return (
    <div data-testid={testId}>
      <div className="flex items-center justify-between mb-1.5">
        <label className="text-xs font-semibold text-slate-700">{label}</label>
        <button type="button" onClick={onAdd} data-testid={`${testId}-add`}
                className="text-xs font-bold text-rose-600 inline-flex items-center gap-1">
          <Plus size={12} /> {addLabel}
        </button>
      </div>
      <div className="space-y-2">
        {(items || []).map((it, i) => (
          <div key={i} className="rounded-xl border border-slate-200 p-3 relative">
            <button type="button" onClick={() => onRemove(i)}
                    className="absolute top-2 right-2 text-slate-400 hover:text-rose-600">
              <Trash size={14} />
            </button>
            <div className="grid gap-2">
              {fields.map((f) => f.textarea ? (
                <textarea key={f.key} value={it[f.key] || ''} placeholder={f.label}
                          onChange={(e) => onUpdate(i, { [f.key]: e.target.value })}
                          rows={3}
                          className="w-full px-2 py-1.5 border border-slate-200 rounded text-xs resize-none" />
              ) : (
                <input key={f.key} value={it[f.key] || ''} placeholder={f.label}
                       onChange={(e) => onUpdate(i, { [f.key]: e.target.value })}
                       className="w-full px-2 py-1.5 border border-slate-200 rounded text-xs" />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
