/**
 * TemplatesPane — list + create/edit + AI-generate + delete.
 */
import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import { Plus, PencilSimple, TrashSimple, Sparkle, FileText, FloppyDisk, X, PaperPlaneTilt } from '@phosphor-icons/react';
import { msgApi, VARIABLES, renderPreview } from './messagingApi';
import AIGenerateModal from './AIGenerateModal';
import SendToAudienceModal from './SendToAudienceModal';

const CATEGORIES = ['welcome', 'reactivation', 'pro', 'referral', 'connect', 'migration', 'custom'];

export default function TemplatesPane() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null);  // null | 'new' | template
  const [sendingTpl, setSendingTpl] = useState(null);  // template to send-to-audience

  const reload = useCallback(async () => {
    setLoading(true);
    try { setItems(await msgApi.templates()); }
    catch { toast.error('Chargement templates impossible.'); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { reload(); }, [reload]);

  const onDelete = async (t) => {
    if (!window.confirm(`Supprimer le template "${t.name}" ?`)) return;
    try { await msgApi.deleteTemplate(t.template_id); toast.success('Supprimé.'); reload(); }
    catch (e) { toast.error(e?.response?.data?.detail || 'Erreur.'); }
  };

  return (
    <div data-testid="messaging-templates-pane">
      <div className="mb-3 flex justify-between items-center">
        <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
          {items.length} template{items.length > 1 ? 's' : ''}
        </p>
        <button className="jp-btn jp-btn-primary text-xs flex items-center gap-1"
                onClick={() => setEditing('new')}
                data-testid="messaging-template-create">
          <Plus size={14} weight="bold" /> Nouveau template
        </button>
      </div>

      {loading && <div className="text-xs opacity-60">Chargement…</div>}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        {items.map((t) => (
          <div key={t.template_id}
               className="p-3 rounded-xl flex items-start gap-3"
               style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}
               data-testid={`template-row-${t.template_id}`}>
            <div className="w-10 h-10 rounded-full flex items-center justify-center shrink-0 shrink-0"
                 style={{ background: 'rgba(59,130,246,0.15)', color: '#3B82F6' }}>
              <FileText size={18} weight="fill" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="font-semibold text-sm">{t.name}</span>
                <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full uppercase"
                      style={{ background: 'rgba(247,147,26,0.12)', color: '#F7931A' }}>
                  {t.category}
                </span>
                {t.source === 'ai' && (
                  <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full inline-flex items-center gap-0.5"
                        style={{ background: 'rgba(139,92,246,0.15)', color: '#7c3aed' }}>
                    <Sparkle size={8} weight="fill" /> IA
                  </span>
                )}
              </div>
              <div className="text-[11px] opacity-70 truncate mt-0.5">{t.subject}</div>
            </div>
            <div className="flex flex-col gap-1 shrink-0">
              <button onClick={() => setSendingTpl(t)} className="p-1.5 rounded-full hover:bg-white/10"
                      title="Envoyer à une audience"
                      data-testid={`template-send-${t.template_id}`}
                      style={{ color: '#E01C2E' }}>
                <PaperPlaneTilt size={14} weight="fill" />
              </button>
              <button onClick={() => setEditing(t)} className="p-1.5 rounded-full hover:bg-white/10"
                      data-testid={`template-edit-${t.template_id}`}>
                <PencilSimple size={14} />
              </button>
              <button onClick={() => onDelete(t)} className="p-1.5 rounded-full hover:bg-white/10"
                      style={{ color: '#b91c1c' }}>
                <TrashSimple size={14} />
              </button>
            </div>
          </div>
        ))}
      </div>

      {editing && (
        <TemplateEditor tpl={editing === 'new' ? null : editing}
                        onClose={() => setEditing(null)}
                        onSaved={() => { setEditing(null); reload(); }} />
      )}

      {sendingTpl && (
        <SendToAudienceModal
          template={sendingTpl}
          onClose={() => setSendingTpl(null)}
          onSent={() => { /* campaign created; no need to reload templates list */ }}
        />
      )}
    </div>
  );
}

function TemplateEditor({ tpl, onClose, onSaved }) {
  const [form, setForm] = useState({
    name: tpl?.name || '',
    language: tpl?.language || 'fr',
    category: tpl?.category || 'custom',
    subject: tpl?.subject || '',
    preview_text: tpl?.preview_text || '',
    body_html: tpl?.body_html || '',
    body_text: tpl?.body_text || '',
    cta_label: tpl?.cta_label || '',
    cta_url: tpl?.cta_url || '',
    source: tpl?.source || 'manual',
  });
  const [showAI, setShowAI] = useState(false);
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const onSave = async () => {
    if (!form.name || !form.subject || !form.body_html) return toast.error('Nom, sujet et corps requis.');
    setBusy(true);
    try {
      if (tpl) await msgApi.updateTemplate(tpl.template_id, form);
      else await msgApi.createTemplate(form);
      toast.success(tpl ? 'Template mis à jour.' : 'Template créé.');
      onSaved();
    } catch (e) { toast.error(e?.response?.data?.detail || 'Erreur.'); }
    finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3"
         style={{ background: 'rgba(0,0,0,0.7)' }}
         onClick={onClose}
         data-testid="template-editor-modal">
      <div className="w-full max-w-3xl max-h-[92vh] rounded-2xl overflow-hidden flex flex-col"
           style={{ background: 'var(--jp-surface)' }}
           onClick={(e) => e.stopPropagation()}>
        <header className="px-5 py-3 flex items-center justify-between"
                style={{ borderBottom: '1px solid var(--jp-border)' }}>
          <h2 className="font-['Outfit'] font-extrabold text-lg">
            {tpl ? 'Éditer le template' : 'Nouveau template'}
          </h2>
          <button onClick={onClose} className="p-2 rounded-full hover:bg-white/10">
            <X size={18} />
          </button>
        </header>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-0 overflow-y-auto flex-1">
          <div className="p-4 space-y-2 overflow-y-auto"
               style={{ borderRight: '1px solid var(--jp-border)' }}>
            <Field label="Nom">
              <input value={form.name} onChange={(e) => set('name', e.target.value)}
                     className="jp-input" data-testid="template-field-name" />
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Catégorie">
                <select value={form.category} onChange={(e) => set('category', e.target.value)}
                        className="jp-input">
                  {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </Field>
              <Field label="Langue">
                <select value={form.language} onChange={(e) => set('language', e.target.value)}
                        className="jp-input">
                  <option value="fr">Français</option>
                  <option value="en">English</option>
                </select>
              </Field>
            </div>
            <button onClick={() => setShowAI(true)}
                    className="jp-btn jp-btn-secondary text-xs flex items-center gap-1 w-full justify-center"
                    data-testid="template-btn-ai">
              <Sparkle size={14} weight="fill" /> Générer avec l'IA
            </button>
            <Field label="Sujet">
              <input value={form.subject} onChange={(e) => set('subject', e.target.value)}
                     className="jp-input" data-testid="template-field-subject" />
            </Field>
            <Field label="Preview text">
              <input value={form.preview_text} onChange={(e) => set('preview_text', e.target.value)}
                     className="jp-input" maxLength={160} />
            </Field>
            <Field label="Corps HTML">
              <textarea value={form.body_html} onChange={(e) => set('body_html', e.target.value)}
                        rows={10} className="jp-input font-mono text-xs" />
            </Field>
            <div className="flex flex-wrap gap-1">
              {VARIABLES.slice(0, 8).map((v) => (
                <button key={v.key} onClick={() => set('body_html', (form.body_html || '') + ` {{${v.key}}}`)}
                        className="text-[10px] px-2 py-0.5 rounded-full"
                        style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
                  {`{{${v.key}}}`}
                </button>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Field label="CTA label"><input value={form.cta_label} onChange={(e) => set('cta_label', e.target.value)} className="jp-input" /></Field>
              <Field label="CTA URL"><input value={form.cta_url} onChange={(e) => set('cta_url', e.target.value)} className="jp-input" /></Field>
            </div>
          </div>
          <div className="p-4 overflow-y-auto" style={{ background: 'var(--jp-surface-secondary)' }}>
            <div className="text-[11px] font-bold uppercase tracking-wide mb-2"
                 style={{ color: 'var(--jp-text-muted)' }}>Aperçu</div>
            <div className="rounded-xl p-4 text-sm"
                 style={{ background: '#fff', color: '#18181B', border: '1px solid var(--jp-border)' }}
                 data-testid="template-preview">
              <div className="font-bold mb-2">{renderPreview(form.subject)}</div>
              <div className="text-xs mb-2" style={{ color: '#71717a' }}>{renderPreview(form.preview_text)}</div>
              <div dangerouslySetInnerHTML={{ __html: renderPreview(form.body_html) }} />
              {form.cta_label && (
                <div style={{ textAlign: 'center', marginTop: 18 }}>
                  <span style={{ display: 'inline-block', padding: '8px 18px', background: '#F7931A', color: '#fff', borderRadius: 999, fontSize: 12, fontWeight: 700 }}>
                    {form.cta_label}
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
        <footer className="px-5 py-3 flex justify-end gap-2"
                style={{ borderTop: '1px solid var(--jp-border)' }}>
          <button onClick={onClose} className="jp-btn jp-btn-ghost text-xs">Annuler</button>
          <button onClick={onSave} disabled={busy}
                  className="jp-btn jp-btn-primary text-xs flex items-center gap-1"
                  data-testid="template-btn-save">
            <FloppyDisk size={13} /> {busy ? 'Sauvegarde…' : 'Enregistrer'}
          </button>
        </footer>
        {showAI && (
          <AIGenerateModal onClose={() => setShowAI(false)}
                           onGenerated={(t) => {
                             setForm((f) => ({ ...f, ...t, source: 'ai' }));
                             setShowAI(false);
                             toast.success('Template généré — ajustez et enregistrez.');
                           }} />
        )}
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
