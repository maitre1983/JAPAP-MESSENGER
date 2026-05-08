/**
 * AIImproveButton — "✨ Améliorer avec IA" — Phase 2.
 * Opens a popover with 4 actions:
 *   Améliorer | Corriger | Reformuler | Générer
 * Each calls POST /api/ai/improve-text and replaces the text on success.
 *
 * Props:
 *   text       : current text in the composer
 *   onApply    : (improved:string) => void       called when user accepts the AI result
 *   disabled?  : boolean
 */
import { useState, useRef, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Sparkle, CheckCircle, ArrowClockwise, Translate, PencilLine, MagicWand } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

const ACTIONS = [
  { id: 'suggest',  label: 'Améliorer', icon: Sparkle,      desc: 'Version plus accrocheuse' },
  { id: 'correct',  label: 'Corriger',  icon: CheckCircle,  desc: 'Orthographe & grammaire' },
  { id: 'rephrase', label: 'Reformuler', icon: ArrowClockwise, desc: 'Reformulation plus fluide' },
  { id: 'generate', label: 'Générer',   icon: MagicWand,    desc: 'À partir d\'un mot-clé' },
];

export default function AIImproveButton({ text, onApply, disabled }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(null); // action id while loading
  const [preview, setPreview] = useState(null); // { action, improved }
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) { setOpen(false); setPreview(null); } };
    window.addEventListener('mousedown', close);
    return () => window.removeEventListener('mousedown', close);
  }, [open]);

  const run = async (action) => {
    const src = (text || '').trim();
    if (!src) { toast.error('Écrivez quelque chose ou un mot-clé d\'abord.'); return; }
    setLoading(action);
    try {
      const { data } = await axios.post(`${API}/api/ai/improve-text`,
        { text: src, action }, { withCredentials: true });
      setPreview({ action, improved: data.improved });
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur IA');
    } finally { setLoading(null); }
  };

  const apply = () => {
    if (preview?.improved) {
      onApply?.(preview.improved);
      toast.success('Texte IA appliqué ✨');
    }
    setOpen(false); setPreview(null);
  };

  return (
    <div className="relative" ref={ref}>
      <button type="button" disabled={disabled}
        data-testid="ai-improve-btn"
        onClick={() => setOpen(v => !v)}
        className="text-[11px] font-['Manrope'] font-bold px-3 py-1.5 rounded-full flex items-center gap-1 transition-all hover:scale-[1.02] disabled:opacity-40"
        style={{
          background: 'linear-gradient(135deg, #9333EA 0%, #E01C2E 100%)',
          color: 'white',
          boxShadow: '0 4px 12px -4px rgba(147,51,234,0.5)',
        }}>
        <Sparkle size={12} weight="fill" /> IA
      </button>
      {open && (
        <div className="absolute right-0 top-10 z-40 w-72 rounded-2xl p-2 shadow-2xl jp-animate-scaleIn"
          style={{ background: 'white', border: '1px solid var(--jp-border)' }}
          data-testid="ai-improve-popover">
          {!preview && (
            <>
              <p className="text-[11px] px-3 py-1.5" style={{ color: 'var(--jp-text-muted)' }}>
                Que souhaitez-vous faire ?
              </p>
              {ACTIONS.map(a => (
                <button key={a.id} type="button" onClick={() => run(a.id)}
                  disabled={!!loading}
                  data-testid={`ai-action-${a.id}`}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left transition-colors hover:bg-black/5 disabled:opacity-60">
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
                    style={{ background: 'linear-gradient(135deg, #9333EA33 0%, #E01C2E33 100%)' }}>
                    <a.icon size={15} weight="bold" style={{ color: '#9333EA' }} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-['Manrope'] font-bold">{a.label}</div>
                    <div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>{a.desc}</div>
                  </div>
                  {loading === a.id && <div className="w-3 h-3 rounded-full border-2 border-t-transparent animate-spin" style={{ borderColor: 'var(--jp-primary)', borderTopColor: 'transparent' }} />}
                </button>
              ))}
            </>
          )}
          {preview && (
            <div className="p-2">
              <p className="text-[10px] uppercase tracking-wider font-bold mb-1" style={{ color: 'var(--jp-primary)' }}>
                <Sparkle size={10} weight="fill" className="inline mr-1" />
                Proposition IA
              </p>
              <div className="text-sm font-['Manrope'] p-3 rounded-lg mb-2 whitespace-pre-wrap"
                style={{ background: 'var(--jp-primary-subtle)', maxHeight: '160px', overflow: 'auto' }}
                data-testid="ai-improve-preview">
                {preview.improved}
              </div>
              <div className="flex gap-2">
                <button type="button" onClick={apply} data-testid="ai-apply"
                  className="flex-1 jp-btn jp-btn-primary jp-btn-sm">
                  <CheckCircle size={14} /> Appliquer
                </button>
                <button type="button" onClick={() => run(preview.action)}
                  disabled={!!loading}
                  className="jp-btn jp-btn-ghost jp-btn-sm"
                  data-testid="ai-retry">
                  <ArrowClockwise size={14} />
                </button>
                <button type="button" onClick={() => setPreview(null)}
                  className="jp-btn jp-btn-ghost jp-btn-sm">
                  Autre
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
