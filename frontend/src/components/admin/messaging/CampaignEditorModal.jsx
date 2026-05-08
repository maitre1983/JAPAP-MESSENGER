/**
 * CampaignEditorModal — create/edit campaign + inline preview + test-send.
 * Structured textarea with variables helper + live preview.
 */
import { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { X, PaperPlaneTilt, FloppyDisk, Eye, Sparkle } from '@phosphor-icons/react';
import { msgApi, VARIABLES, renderPreview } from './messagingApi';
import AIGenerateModal from './AIGenerateModal';
import IndividualTargetingPicker from './IndividualTargetingPicker';

export default function CampaignEditorModal({ campaign, onClose, onSaved }) {
  const isEditing = Boolean(campaign);
  const readOnly = isEditing && campaign.status !== 'draft';
  // Decode existing individual targets (mixed list of user_id strings + {email:...})
  const initialTargets = (() => {
    const raw = campaign?.individual_user_ids || [];
    const user_ids = []; const emails = []; const resolved = {};
    for (const item of raw) {
      if (typeof item === 'string') { user_ids.push(item); resolved[item] = { name: item, email: '' }; }
      else if (item && item.email) { emails.push(item.email); resolved[item.email] = { email: item.email, external: true }; }
    }
    return { user_ids, emails, resolved };
  })();
  const initialMode = (initialTargets.user_ids.length || initialTargets.emails.length) ? 'individual' : 'segment';
  const [audienceMode, setAudienceMode] = useState(initialMode);
  const [targets, setTargets] = useState(initialTargets);
  const [form, setForm] = useState({
    name: campaign?.name || '',
    subject: campaign?.subject || '',
    preview_text: campaign?.preview_text || '',
    body_html: campaign?.body_html || '',
    body_text: campaign?.body_text || '',
    cta_label: campaign?.cta_label || '',
    cta_url: campaign?.cta_url || '',
    language: campaign?.language || 'fr',
    segment_id: campaign?.segment_id || 'seg_all_users',
    template_id: campaign?.template_id || null,
  });
  const [segments, setSegments] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [sample, setSample] = useState(null);
  const [testBusy, setTestBusy] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
  const [showAI, setShowAI] = useState(false);
  const [recipientTest, setRecipientTest] = useState('');
  const [audienceCount, setAudienceCount] = useState(null);

  useEffect(() => {
    msgApi.segments().then(setSegments).catch(() => {});
    msgApi.templates().then(setTemplates).catch(() => {});
  }, []);

  useEffect(() => {
    if (audienceMode !== 'segment' || !form.segment_id) { setAudienceCount(null); return; }
    msgApi.segmentPreview({ segment_id: form.segment_id, sample_size: 1 })
      .then((d) => { setAudienceCount(d.count); if (d.sample?.[0]) setSample(d.sample[0]); })
      .catch(() => setAudienceCount(null));
  }, [form.segment_id, audienceMode]);

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const loadTemplate = (tpl_id) => {
    const t = templates.find((x) => x.template_id === tpl_id);
    if (!t) return;
    setForm((f) => ({
      ...f, template_id: tpl_id,
      subject: t.subject, preview_text: t.preview_text || '',
      body_html: t.body_html, body_text: t.body_text || '',
      cta_label: t.cta_label || '', cta_url: t.cta_url || '',
      language: t.language || f.language,
    }));
    toast.success(`Template "${t.name}" chargé.`);
  };

  const onSave = async () => {
    if (!form.name || !form.subject || !form.body_html) return toast.error('Nom, sujet et corps requis.');
    if (audienceMode === 'individual' && targets.user_ids.length === 0 && targets.emails.length === 0) {
      return toast.error('Sélectionnez au moins un destinataire.');
    }
    const payload = {
      ...form,
      segment_id: audienceMode === 'segment' ? form.segment_id : null,
      individual_user_ids: audienceMode === 'individual' ? targets.user_ids : null,
      individual_emails:   audienceMode === 'individual' ? targets.emails   : null,
    };
    setSaveBusy(true);
    try {
      if (isEditing) {
        await msgApi.updateCampaign(campaign.campaign_id, payload);
        toast.success('Campagne enregistrée.');
      } else {
        await msgApi.createCampaign(payload);
        toast.success('Campagne créée.');
      }
      onSaved();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur.');
    } finally { setSaveBusy(false); }
  };

  const onTestSend = async () => {
    if (!isEditing) return toast.error('Sauvegardez la campagne avant de tester.');
    setTestBusy(true);
    try {
      await msgApi.testCampaign(campaign.campaign_id, recipientTest || null);
      toast.success(`Test envoyé${recipientTest ? ` à ${recipientTest}` : ' à votre email admin'}.`);
    } catch (e) { toast.error(e?.response?.data?.detail || 'Échec test-send.'); }
    finally { setTestBusy(false); }
  };

  const insertVar = (key) => {
    // Best-effort: append {{var}} at end of body_html (mobile-friendly; no cursor tracking)
    set('body_html', (form.body_html || '') + ` {{${key}}}`);
  };

  const previewSubject = renderPreview(form.subject, sample);
  const previewBody = renderPreview(form.body_html, sample);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3"
         style={{ background: 'rgba(0,0,0,0.7)' }}
         onClick={onClose}
         data-testid="campaign-editor-modal">
      <div className="w-full max-w-4xl max-h-[92vh] rounded-2xl overflow-hidden flex flex-col"
           style={{ background: 'var(--jp-surface)', color: 'var(--jp-text)' }}
           onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <header className="px-5 py-3 flex items-center justify-between"
                style={{ borderBottom: '1px solid var(--jp-border)' }}>
          <div>
            <h2 className="font-['Outfit'] font-extrabold text-lg">
              {isEditing ? (readOnly ? 'Aperçu campagne' : 'Éditer campagne') : 'Nouvelle campagne'}
            </h2>
            {audienceCount !== null && audienceMode === 'segment' && (
              <p className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
                Audience estimée : <strong>{audienceCount}</strong> destinataire{audienceCount > 1 ? 's' : ''}
              </p>
            )}
            {audienceMode === 'individual' && (
              <p className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
                Ciblage individuel : <strong>{targets.user_ids.length + targets.emails.length}</strong> destinataire(s)
              </p>
            )}
          </div>
          <button onClick={onClose} className="p-2 rounded-full hover:bg-white/10"
                  data-testid="campaign-editor-close">
            <X size={18} />
          </button>
        </header>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-0 overflow-y-auto flex-1">
          {/* LEFT: form */}
          <div className="p-5 space-y-3 overflow-y-auto"
               style={{ borderRight: '1px solid var(--jp-border)' }}>
            <Field label="Nom interne">
              <input value={form.name} onChange={(e) => set('name', e.target.value)}
                     disabled={readOnly} className="jp-input" data-testid="campaign-field-name" />
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Langue">
                <select value={form.language} onChange={(e) => set('language', e.target.value)}
                        disabled={readOnly} className="jp-input">
                  <option value="fr">Français</option>
                  <option value="en">English</option>
                </select>
              </Field>
              <Field label="Audience">
                <div className="flex rounded-lg overflow-hidden"
                     style={{ border: '1px solid var(--jp-border)' }}>
                  <button type="button" onClick={() => !readOnly && setAudienceMode('segment')}
                          disabled={readOnly}
                          className="flex-1 text-xs py-1.5 font-semibold transition-colors"
                          style={{
                            background: audienceMode === 'segment' ? 'var(--jp-primary)' : 'transparent',
                            color: audienceMode === 'segment' ? '#fff' : 'var(--jp-text-muted)',
                          }}
                          data-testid="audience-mode-segment">
                    Segment
                  </button>
                  <button type="button" onClick={() => !readOnly && setAudienceMode('individual')}
                          disabled={readOnly}
                          className="flex-1 text-xs py-1.5 font-semibold transition-colors"
                          style={{
                            background: audienceMode === 'individual' ? 'var(--jp-primary)' : 'transparent',
                            color: audienceMode === 'individual' ? '#fff' : 'var(--jp-text-muted)',
                          }}
                          data-testid="audience-mode-individual">
                    Individuel
                  </button>
                </div>
              </Field>
            </div>
            {audienceMode === 'segment' ? (
              <Field label="Segment">
                <select value={form.segment_id} onChange={(e) => set('segment_id', e.target.value)}
                        disabled={readOnly} className="jp-input" data-testid="campaign-field-segment">
                  {segments.map((s) => (
                    <option key={s.segment_id} value={s.segment_id}>
                      {s.name} ({s.estimated_count})
                    </option>
                  ))}
                </select>
              </Field>
            ) : (
              <div className="p-3 rounded-xl"
                   style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
                <IndividualTargetingPicker value={targets} onChange={setTargets} disabled={readOnly} />
              </div>
            )}
            {!readOnly && (
              <Field label="Pré-remplir depuis un template">
                <div className="flex gap-2">
                  <select value={form.template_id || ''} onChange={(e) => e.target.value && loadTemplate(e.target.value)}
                          className="jp-input flex-1" data-testid="campaign-field-template">
                    <option value="">— Choisir —</option>
                    {templates.map((t) => (
                      <option key={t.template_id} value={t.template_id}>{t.name}</option>
                    ))}
                  </select>
                  <button onClick={() => setShowAI(true)}
                          className="jp-btn jp-btn-secondary text-xs flex items-center gap-1 shrink-0"
                          data-testid="campaign-btn-ai-generate">
                    <Sparkle size={14} weight="fill" /> Générer avec l'IA
                  </button>
                </div>
              </Field>
            )}
            <Field label="Sujet">
              <input value={form.subject} onChange={(e) => set('subject', e.target.value)}
                     disabled={readOnly} className="jp-input" data-testid="campaign-field-subject"
                     placeholder="Bonjour {{first_name}}" />
            </Field>
            <Field label="Preview text (texte d'aperçu)">
              <input value={form.preview_text} onChange={(e) => set('preview_text', e.target.value)}
                     disabled={readOnly} className="jp-input" maxLength={160} />
            </Field>
            <Field label="Corps HTML">
              <textarea value={form.body_html} onChange={(e) => set('body_html', e.target.value)}
                        disabled={readOnly} rows={10}
                        className="jp-input font-mono text-xs"
                        data-testid="campaign-field-body"
                        placeholder="<p>Bonjour {{first_name}},</p>" />
              {!readOnly && (
                <div className="flex flex-wrap gap-1 mt-2">
                  {VARIABLES.map((v) => (
                    <button key={v.key} onClick={() => insertVar(v.key)}
                            className="text-[10px] px-2 py-0.5 rounded-full transition-colors hover:bg-white/10"
                            style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}
                            title={v.label}>
                      {`{{${v.key}}}`}
                    </button>
                  ))}
                </div>
              )}
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Label CTA">
                <input value={form.cta_label} onChange={(e) => set('cta_label', e.target.value)}
                       disabled={readOnly} className="jp-input" placeholder="Ouvrir JAPAP" />
              </Field>
              <Field label="URL CTA">
                <input value={form.cta_url} onChange={(e) => set('cta_url', e.target.value)}
                       disabled={readOnly} className="jp-input" placeholder="https://..." />
              </Field>
            </div>
          </div>

          {/* RIGHT: live preview */}
          <div className="p-5 overflow-y-auto" style={{ background: 'var(--jp-surface-secondary)' }}>
            <div className="text-[11px] font-bold uppercase tracking-wide mb-2 flex items-center gap-1"
                 style={{ color: 'var(--jp-text-muted)' }}>
              <Eye size={12} weight="bold" /> Aperçu live
            </div>
            <div className="rounded-xl p-4" style={{ background: '#fff', color: '#18181B', border: '1px solid var(--jp-border)' }}
                 data-testid="campaign-preview">
              <div className="text-[10px] uppercase tracking-wide" style={{ color: '#71717a' }}>Sujet</div>
              <div className="font-bold text-sm mb-2">{previewSubject || <em style={{ color: '#71717a' }}>— vide —</em>}</div>
              <div className="text-[10px] uppercase tracking-wide" style={{ color: '#71717a' }}>Preview</div>
              <div className="text-xs mb-3" style={{ color: '#52525b' }}>{renderPreview(form.preview_text, sample)}</div>
              <hr style={{ borderColor: '#e4e4e7', margin: '8px 0' }} />
              <div className="text-sm leading-relaxed"
                   dangerouslySetInnerHTML={{ __html: previewBody || '<em style="color:#71717a">— vide —</em>' }} />
              {form.cta_label && (
                <div style={{ textAlign: 'center', margin: '20px 0 0' }}>
                  <span style={{
                    display: 'inline-block', padding: '10px 22px', background: '#F7931A',
                    color: '#fff', borderRadius: '999px', fontWeight: 700, fontSize: 13,
                  }}>{form.cta_label}</span>
                </div>
              )}
              <p className="text-[10px] text-center mt-4" style={{ color: '#a1a1aa' }}>
                © JAPAP — Se désabonner
              </p>
            </div>
            {sample && (
              <p className="text-[10px] mt-2" style={{ color: 'var(--jp-text-muted)' }}>
                Échantillon : <code>{sample.email}</code>
              </p>
            )}
          </div>
        </div>

        {/* Footer actions */}
        <footer className="px-5 py-3 flex items-center gap-2 flex-wrap"
                style={{ borderTop: '1px solid var(--jp-border)' }}>
          {isEditing && (
            <>
              <input value={recipientTest} onChange={(e) => setRecipientTest(e.target.value)}
                     placeholder="Email test (défaut: admin)"
                     className="jp-input text-xs flex-1 min-w-[200px]" type="email"
                     data-testid="campaign-test-email" />
              <button onClick={onTestSend} disabled={testBusy}
                      className="jp-btn jp-btn-secondary text-xs flex items-center gap-1"
                      data-testid="campaign-btn-test">
                <PaperPlaneTilt size={13} /> {testBusy ? 'Envoi…' : 'Test-send'}
              </button>
            </>
          )}
          {!readOnly && (
            <button onClick={onSave} disabled={saveBusy}
                    className="jp-btn jp-btn-primary text-xs flex items-center gap-1"
                    data-testid="campaign-btn-save">
              <FloppyDisk size={13} weight="bold" /> {saveBusy ? 'Enregistrement…' : 'Enregistrer'}
            </button>
          )}
        </footer>
      </div>

      {showAI && (
        <AIGenerateModal
          onClose={() => setShowAI(false)}
          onGenerated={(tpl) => {
            setForm((f) => ({ ...f, ...tpl }));
            setShowAI(false);
            toast.success('Template généré par l\'IA — éditez avant de sauver.');
          }}
        />
      )}
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
