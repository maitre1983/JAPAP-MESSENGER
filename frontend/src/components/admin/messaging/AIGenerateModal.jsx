/**
 * AIGenerateModal — Claude Sonnet 4.5 bridge for template generation.
 */
import { useState } from 'react';
import { toast } from 'sonner';
import { X, Sparkle, ArrowRight } from '@phosphor-icons/react';
import { msgApi } from './messagingApi';
import { useTranslation } from 'react-i18next';

const GOAL_PRESETS = [
  'Réactiver les utilisateurs inactifs depuis 30 jours',
  'Encourager le passage au plan Pro',
  'Expliquer la migration JAPAP 1.0 vers 4.0 et inviter à réinitialiser le mot de passe',
  'Motiver le parrainage : gagner 5 USD par ami invité',
  'Éduquer sur les appels vidéo et résumés IA',
  'Promouvoir JAPAP Connect auprès des utilisateurs jamais actifs',
];

const TONES = ['chaleureux', 'direct', 'urgent', 'célébratoire', 'professionnel'];
const CTA_TYPES = ['primary action', 'soft invitation', 'urgent', 'educational'];

export default function AIGenerateModal({ onClose, onGenerated }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    goal: '', audience: '', tone: 'chaleureux', language: 'fr',
    cta_type: 'primary action', extra_context: '',
  });
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const onGenerate = async () => {
    if (!form.goal.trim()) return toast.error('Objectif requis.');
    setBusy(true);
    try {
      const r = await msgApi.aiGenerate(form);
      onGenerated(r.template);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Échec génération IA.');
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-3"
         style={{ background: 'rgba(0,0,0,0.8)' }}
         onClick={onClose}
         data-testid="ai-generate-modal">
      <div className="w-full max-w-xl rounded-2xl overflow-hidden max-h-[92vh] flex flex-col"
           style={{ background: 'var(--jp-surface)' }}
           onClick={(e) => e.stopPropagation()}>
        <header className="px-5 py-4 flex items-center justify-between"
                style={{ borderBottom: '1px solid var(--jp-border)',
                         background: 'linear-gradient(135deg, rgba(247,147,26,0.1), rgba(236,72,153,0.1))' }}>
          <div className="flex items-center gap-2">
            <Sparkle size={20} weight="fill" style={{ color: '#F7931A' }} />
            <div>
              <h2 className="font-['Outfit'] font-extrabold text-lg">Générer avec l'IA</h2>
              <p className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
                Claude Sonnet 4.5 · optimisé copywriting
              </p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 rounded-full hover:bg-white/10" data-testid="ai-modal-close">
            <X size={18} />
          </button>
        </header>

        <div className="p-5 space-y-3 overflow-y-auto">
          <Field label="🎯 Objectif de la campagne">
            <textarea value={form.goal} onChange={(e) => set('goal', e.target.value)}
                      rows={3} className="jp-input"
                      placeholder={t('a_i_generate_modal.ex_reactiver_les_utilisateurs_pro_i')}
                      data-testid="ai-field-goal" />
            <div className="flex flex-wrap gap-1 mt-1.5">
              {GOAL_PRESETS.map((g) => (
                <button key={g} onClick={() => set('goal', g)}
                        className="text-[10px] px-2 py-1 rounded-full transition-colors hover:bg-white/10"
                        style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
                  {g.length > 40 ? g.slice(0, 40) + '…' : g}
                </button>
              ))}
            </div>
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Audience">
              <input value={form.audience} onChange={(e) => set('audience', e.target.value)}
                     className="jp-input" placeholder="Utilisateurs Pro, Afrique Ouest…" />
            </Field>
            <Field label="Langue">
              <select value={form.language} onChange={(e) => set('language', e.target.value)}
                      className="jp-input">
                <option value="fr">{t('a_i_generate_modal.francais')}</option>
                <option value="en">{t('a_i_generate_modal.english')}</option>
              </select>
            </Field>
            <Field label="Ton">
              <select value={form.tone} onChange={(e) => set('tone', e.target.value)}
                      className="jp-input">
                {TONES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>
            <Field label="Type de CTA">
              <select value={form.cta_type} onChange={(e) => set('cta_type', e.target.value)}
                      className="jp-input">
                {CTA_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>
          </div>
          <Field label="Contexte additionnel (optionnel)">
            <textarea value={form.extra_context} onChange={(e) => set('extra_context', e.target.value)}
                      rows={2} className="jp-input"
                      placeholder={t('a_i_generate_modal.ex_campagne_lancee_en_avril_offre_p')} />
          </Field>
        </div>

        <footer className="px-5 py-3 flex justify-end gap-2"
                style={{ borderTop: '1px solid var(--jp-border)' }}>
          <button onClick={onClose} className="jp-btn jp-btn-ghost text-xs">Annuler</button>
          <button onClick={onGenerate} disabled={busy || !form.goal.trim()}
                  className="jp-btn jp-btn-primary text-xs flex items-center gap-1 disabled:opacity-40"
                  data-testid="ai-btn-generate">
            {busy ? 'Génération…' : 'Générer'} <ArrowRight size={13} weight="bold" />
          </button>
        </footer>
      </div>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label className="block">
      <span className="block text-[10px] font-bold uppercase tracking-wide mb-1"
            style={{ color: 'var(--jp-text-muted)' }}>{label}</span>
      {children}
    </label>
  );
}
