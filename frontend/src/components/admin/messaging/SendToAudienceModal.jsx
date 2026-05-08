/**
 * SendToAudienceModal — iter94 Go-Live quick win.
 *
 * Allows an admin to pick an audience (or a Migration 1.0→4.0 batch) and
 * send a saved template in one click. Mandatory preview + confirmation.
 * All orchestration is backend-side; this modal only collects user intent.
 */
import { useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  X, PaperPlaneTilt, Warning, UsersThree, CheckCircle,
  ArrowLeft, Hourglass,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

const BATCH_STATUS_STYLES = {
  not_sent:  { label: 'Non envoyé',  color: '#52525b', bg: '#f4f4f5' },
  pending:   { label: 'En file',     color: '#0369a1', bg: '#e0f2fe' },
  sending:   { label: 'Envoi…',      color: '#9A6700', bg: '#FEF3C7' },
  sent:      { label: 'Envoyé',      color: '#047857', bg: '#d1fae5' },
  failed:    { label: 'Échec',       color: '#b91c1c', bg: '#fee2e2' },
  paused:    { label: 'Pause',       color: '#78350f', bg: '#fed7aa' },
};

export default function SendToAudienceModal({ template, onClose, onSent }) {
  const [step, setStep] = useState('pick');        // pick | preview | sending | done
  const [opts, setOpts] = useState(null);
  const [selected, setSelected] = useState(null);  // {kind:'segment'|'batch', ...}
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const r = await axios.get(
          `${API}/api/admin/messaging/audience-options`,
          { withCredentials: true },
        );
        setOpts(r.data);
      } catch (e) {
        toast.error(e?.response?.data?.detail || 'Impossible de charger les audiences.');
      }
    })();
  }, []);

  const confirmSend = async () => {
    if (!selected) return;
    setSending(true);
    setStep('sending');
    try {
      const body = { confirm: true };
      if (selected.kind === 'segment') {
        body.segment_id = selected.segment_id;
        body.force = selected.estimated_count > 5000;  // tolerate large systems
      } else if (selected.kind === 'batch') {
        body.segment_id = 'seg_migration_1to4';
        body.batch_key = selected.batch_key;
      }
      const r = await axios.post(
        `${API}/api/admin/messaging/templates/${template.template_id}/send-to-audience`,
        body,
        { withCredentials: true },
      );
      setResult(r.data);
      setStep('done');
      toast.success(`Campagne lancée (${r.data.enqueued} destinataires en file).`);
      onSent?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Erreur envoi.');
      setStep('preview');
    } finally {
      setSending(false);
    }
  };

  const recipientsCount = selected?.kind === 'segment'
    ? (selected.estimated_count ?? 0)
    : (selected?.size ?? 0);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-3"
      style={{ background: 'rgba(0,0,0,0.7)' }}
      onClick={onClose}
      data-testid="send-to-audience-modal"
    >
      <div
        className="w-full max-w-2xl max-h-[92vh] rounded-2xl overflow-hidden flex flex-col"
        style={{ background: 'var(--jp-surface)' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* ── Header ── */}
        <header
          className="px-5 py-3 flex items-center justify-between"
          style={{ borderBottom: '1px solid var(--jp-border)' }}
        >
          <div className="flex items-center gap-2">
            <PaperPlaneTilt size={20} weight="duotone" style={{ color: 'var(--jp-primary)' }} />
            <h2 className="font-['Outfit'] font-extrabold text-base">
              Envoyer à une audience
            </h2>
          </div>
          <button onClick={onClose} className="p-2 rounded-full hover:bg-white/10" data-testid="send-close">
            <X size={18} />
          </button>
        </header>

        {/* ── Body ── */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {/* Template summary (always visible) */}
          <div
            className="p-3 rounded-xl text-xs"
            style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}
          >
            <div className="text-[10px] uppercase font-bold mb-1" style={{ color: 'var(--jp-text-muted)' }}>
              Template
            </div>
            <div className="font-semibold text-sm">{template.name}</div>
            <div className="opacity-70 mt-0.5 truncate">{template.subject}</div>
          </div>

          {step === 'pick' && (
            <PickAudience opts={opts} selected={selected} onSelect={setSelected} />
          )}

          {step === 'preview' && selected && (
            <Preview selected={selected} template={template} count={recipientsCount} />
          )}

          {step === 'sending' && (
            <div className="text-center py-10 flex flex-col items-center gap-2">
              <Hourglass size={36} className="animate-spin" style={{ color: 'var(--jp-primary)' }} />
              <div className="text-sm font-semibold">Envoi en cours…</div>
              <div className="text-xs opacity-70">Les destinataires sont ajoutés à la file.</div>
            </div>
          )}

          {step === 'done' && result && (
            <div className="text-center py-6 flex flex-col items-center gap-2">
              <CheckCircle size={44} weight="fill" style={{ color: '#059669' }} />
              <div className="text-base font-bold">Campagne lancée</div>
              <div className="text-xs opacity-80">
                {result.enqueued} email(s) en file · {result.dropped_by_filter} filtré(s)
                {result.batch_key && (
                  <> · Batch {result.batch_index}/{result.batch_total}</>
                )}
              </div>
              <div className="text-[11px] opacity-60 mt-1">Campaign ID: {result.campaign_id}</div>
            </div>
          )}
        </div>

        {/* ── Footer ── */}
        <footer
          className="px-5 py-3 flex justify-between items-center gap-2"
          style={{ borderTop: '1px solid var(--jp-border)' }}
        >
          {step === 'pick' && (
            <>
              <span className="text-[11px] opacity-60">
                {selected ? `Audience sélectionnée: ${selected.name || selected.label}` : 'Choisis une audience'}
              </span>
              <button
                disabled={!selected}
                onClick={() => setStep('preview')}
                className="jp-btn jp-btn-primary text-xs disabled:opacity-40"
                data-testid="send-step-preview"
              >
                Aperçu
              </button>
            </>
          )}

          {step === 'preview' && (
            <>
              <button
                onClick={() => setStep('pick')}
                className="jp-btn jp-btn-ghost text-xs flex items-center gap-1"
                data-testid="send-back-to-pick"
              >
                <ArrowLeft size={12} /> Retour
              </button>
              <button
                onClick={confirmSend}
                disabled={sending}
                className="jp-btn jp-btn-primary text-xs flex items-center gap-1"
                style={{ background: '#E01C2E' }}
                data-testid="send-confirm"
              >
                <PaperPlaneTilt size={13} weight="bold" />
                Confirmer l'envoi à {recipientsCount} utilisateurs
              </button>
            </>
          )}

          {step === 'done' && (
            <button
              onClick={onClose}
              className="jp-btn jp-btn-primary text-xs ml-auto"
              data-testid="send-done-close"
            >
              Fermer
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════ */
function PickAudience({ opts, selected, onSelect }) {
  if (!opts) return <div className="text-xs opacity-60">Chargement des audiences…</div>;
  return (
    <div className="space-y-4">
      {/* Segments standards */}
      <section>
        <div className="text-[10px] uppercase font-bold mb-2" style={{ color: 'var(--jp-text-muted)' }}>
          Audiences standards
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2" data-testid="audience-segments-list">
          {opts.segments.map((s) => {
            const active = selected?.kind === 'segment' && selected.segment_id === s.segment_id;
            return (
              <button
                key={s.segment_id}
                onClick={() => onSelect({ kind: 'segment', ...s })}
                data-testid={`audience-opt-${s.segment_id}`}
                className="p-3 rounded-xl text-left transition-colors"
                style={{
                  background: active ? 'rgba(224,28,46,0.10)' : 'var(--jp-surface-secondary)',
                  border: active ? '1px solid #E01C2E' : '1px solid var(--jp-border)',
                }}
              >
                <div className="flex items-center gap-1.5">
                  <UsersThree size={14} weight="duotone" />
                  <span className="font-semibold text-xs truncate">{s.name}</span>
                </div>
                <div className="text-[10px] opacity-70 mt-0.5">
                  ~{s.estimated_count} destinataires
                </div>
                {s.description && (
                  <div className="text-[10px] opacity-60 mt-0.5 line-clamp-2">{s.description}</div>
                )}
              </button>
            );
          })}
        </div>
      </section>

      {/* Batches migration 1→4 */}
      {opts.migration_batches.length > 0 && (
        <section data-testid="migration-batches-section">
          <div
            className="p-2 rounded-lg mb-2 flex items-start gap-2 text-[11px]"
            style={{ background: 'rgba(247,147,26,0.10)', border: '1px solid #F7931A' }}
          >
            <Warning size={14} weight="fill" style={{ color: '#F7931A' }} className="shrink-0 mt-0.5" />
            <div>
              <strong>Migration JAPAP 1.0 → 4.0</strong> — envoi progressif par tranches de 5 000
              pour protéger la délivrabilité Resend. Sélectionnez un seul batch à la fois.
            </div>
          </div>
          <div className="space-y-1.5">
            {opts.migration_batches.map((b) => {
              const active = selected?.kind === 'batch' && selected.batch_key === b.batch_key;
              const s = BATCH_STATUS_STYLES[b.status] || BATCH_STATUS_STYLES.not_sent;
              const alreadySent = b.status !== 'not_sent' && b.status !== 'failed';
              return (
                <button
                  key={b.batch_key}
                  disabled={alreadySent}
                  onClick={() => onSelect({ kind: 'batch', ...b })}
                  data-testid={`audience-opt-${b.batch_key}`}
                  className="w-full p-2.5 rounded-xl flex items-center gap-3 transition-colors disabled:opacity-50 disabled:cursor-not-allowed text-left"
                  style={{
                    background: active ? 'rgba(224,28,46,0.10)' : 'var(--jp-surface-secondary)',
                    border: active ? '1px solid #E01C2E' : '1px solid var(--jp-border)',
                  }}
                >
                  <div className="flex-1 min-w-0">
                    <div className="font-semibold text-xs truncate">{b.label}</div>
                    <div className="text-[10px] opacity-60">
                      {b.size} destinataires
                      {b.sent_count > 0 && ` · ${b.sent_count} envoyés`}
                      {b.bounced_count > 0 && ` · ${b.bounced_count} bounces`}
                    </div>
                  </div>
                  <span
                    className="text-[9px] font-bold px-2 py-0.5 rounded-full uppercase shrink-0"
                    style={{ color: s.color, background: s.bg }}
                  >
                    {s.label}
                  </span>
                </button>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════ */
function Preview({ selected, template, count }) {
  const isLarge = count > 5000;
  return (
    <div className="space-y-3" data-testid="send-preview-view">
      <div
        className="p-4 rounded-xl"
        style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}
      >
        <Row label="Template" value={template.name} />
        <Row label="Sujet" value={template.subject} />
        <Row
          label="Audience"
          value={selected.kind === 'batch' ? selected.label : selected.name}
        />
        {selected.kind === 'batch' && (
          <Row label="Batch" value={`${selected.batch_index}/${selected.batch_total}`} />
        )}
        <Row label="Canal" value="Email (Resend)" />
        <Row
          label="Destinataires estimés"
          value={<span className="font-bold text-base">{count}</span>}
        />
      </div>

      {isLarge && (
        <div
          className="p-2.5 rounded-lg flex items-start gap-2 text-[11px]"
          style={{ background: 'rgba(224,28,46,0.10)', border: '1px solid #E01C2E' }}
        >
          <Warning size={14} weight="fill" style={{ color: '#E01C2E' }} className="shrink-0 mt-0.5" />
          <div>
            <strong>Volume élevé ({count} destinataires).</strong> L'envoi sera étalé par le worker
            (rate-limit global configurable dans l'onglet Batch & Safety).
          </div>
        </div>
      )}

      <div className="text-xs text-center font-semibold" style={{ color: 'var(--jp-text)' }}>
        Confirmez-vous l'envoi de ce template à <span style={{ color: '#E01C2E' }}>{count}</span> utilisateurs ?
      </div>
    </div>
  );
}

function Row({ label, value }) {
  return (
    <div className="flex items-center justify-between py-1.5 text-xs" style={{ borderBottom: '1px dashed var(--jp-border)' }}>
      <span className="opacity-60 uppercase text-[10px] font-bold">{label}</span>
      <span className="font-semibold truncate ml-3 max-w-[60%] text-right">{value}</span>
    </div>
  );
}
