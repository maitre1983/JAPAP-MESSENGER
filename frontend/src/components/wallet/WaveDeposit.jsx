/**
 * iter235 — Wave (Afrique de l'Ouest) — Dépôt utilisateur.
 * iter237i — Wizard 2 étapes : (1) infos virement, (2) référence Wave.
 * Strictement additif. Numéro masqué si vide. Regex T_XXXXX-YYYYY
 * validée client + serveur (inchangée).
 */
import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { ArrowDown, ArrowLeft, X, Copy, CheckCircle } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;
const WAVE_REF_REGEX = /^T_[A-Z0-9]+-[A-Z0-9]+$/;

export default function WaveDeposit({ onSuccess }) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(1);  // iter237i — 1 ou 2
  const [info, setInfo] = useState(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [montant, setMontant] = useState('');
  const [quote, setQuote] = useState(null);
  const [form, setForm] = useState({ numero_expediteur: '', nom_expediteur: '', date_tx: '', heure_tx: '', reference: '' });
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open || info) return;
    setLoading(true);
    axios.get(`${API}/api/deposits/wave/info`, { withCredentials: true })
      .then(r => setInfo(r.data))
      .catch(e => toast.error(e?.response?.data?.detail || 'Méthode indisponible.'))
      .finally(() => setLoading(false));
    // iter237i — track form_opened (best-effort, never blocks).
    axios.post(`${API}/api/payment-methods/track`,
               { method: 'wave', flow: 'deposit', action: 'form_opened' },
               { withCredentials: true }).catch(() => {});
  }, [open, info]);

  useEffect(() => {
    if (!open || step !== 1) return;
    const m = parseFloat(montant);
    if (!m || m <= 0) { setQuote(null); return; }
    const t = setTimeout(() => {
      axios.post(`${API}/api/deposits/wave/quote`, { montant_usd: m }, { withCredentials: true })
        .then(r => setQuote(r.data)).catch(() => setQuote(null));
    }, 350);
    return () => clearTimeout(t);
  }, [montant, open, step]);

  const refUpper = (form.reference || '').trim().toUpperCase();
  const refValid = !refUpper || WAVE_REF_REGEX.test(refUpper);

  // iter237i — Validation pour passage étape 1 → 2.
  const step1Valid = parseFloat(montant) > 0
    && !!form.date_tx && !!form.heure_tx
    && form.numero_expediteur.trim().length >= 8
    && form.nom_expediteur.trim().length >= 2;

  const reset = () => {
    setOpen(false); setStep(1); setMontant(''); setQuote(null);
    setForm({ numero_expediteur: '', nom_expediteur: '', date_tx: '', heure_tx: '', reference: '' });
  };

  const copy = async (txt) => {
    try { await navigator.clipboard.writeText(txt); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch {}
  };

  const goToStep2 = () => {
    if (!step1Valid) { toast.error('Tous les champs sont requis.'); return; }
    setStep(2);
  };

  const submit = async (e) => {
    e?.preventDefault?.();
    const m = parseFloat(montant);
    if (!m || m <= 0) { toast.error('Montant invalide.'); return; }
    if (!refUpper || !WAVE_REF_REGEX.test(refUpper)) {
      toast.error('Référence Wave invalide. Format : T_XXXXX-YYYYY'); return;
    }
    if (!form.numero_expediteur || !form.nom_expediteur || !form.date_tx || !form.heure_tx) {
      toast.error('Tous les champs sont requis.'); return;
    }
    setSubmitting(true);
    try {
      await axios.post(`${API}/api/deposits/wave/submit`, {
        montant_usd: m,
        numero_expediteur: form.numero_expediteur.trim(),
        nom_expediteur: form.nom_expediteur.trim(),
        date_tx: form.date_tx,
        heure_tx: form.heure_tx,
        reference: refUpper,
      }, { withCredentials: true });
      toast.success('Dépôt Wave soumis ! En attente de vérification.');
      // iter237i — track submitted (best-effort).
      axios.post(`${API}/api/payment-methods/track`,
                 { method: 'wave', flow: 'deposit', action: 'submitted' },
                 { withCredentials: true }).catch(() => {});
      reset();
      onSuccess?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Échec de la soumission.');
    } finally { setSubmitting(false); }
  };

  const today = useMemo(() => new Date().toISOString().slice(0, 10), []);
  const waveNumberDisplay = info?.receiver_number ? info.receiver_number : '••••••••';

  return (
    <div className="jp-card-elevated p-5 mb-4" data-testid="wave-deposit-card">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full flex items-center justify-center" style={{ background: '#1DC8FA', color: 'white', fontWeight: 800 }}>W</div>
          <div>
            <h4 className="font-['Outfit'] text-base font-bold" style={{ color: 'var(--jp-text)' }}>Dépôt Wave</h4>
            <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>Afrique de l'Ouest · Crédit après vérification</p>
          </div>
        </div>
        <button data-testid="wave-deposit-toggle" onClick={() => (open ? reset() : setOpen(true))}
          className="jp-btn jp-btn-sm" style={{ background: '#1DC8FA', color: 'white' }}>
          {open ? <X size={16} /> : <ArrowDown size={16} />}
          <span className="ml-1">{open ? 'Fermer' : 'Déposer'}</span>
        </button>
      </div>

      {open && (
        <div className="mt-4" data-testid="wave-deposit-form">
          {loading && <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>}

          {/* Header avec étape courante */}
          <div className="flex items-center justify-between mb-3" data-testid="wave-deposit-step-indicator">
            <h5 className="font-['Outfit'] text-sm font-bold" style={{ color: 'var(--jp-text)' }}>
              Dépôt Wave — Étape {step}/2
            </h5>
            <span className="text-[10px] px-2 py-0.5 rounded-full font-bold tracking-wider"
                  style={{ background: 'rgba(29,200,250,0.15)', color: '#0A6F8B' }}>
              {step === 1 ? '1️⃣ Saisie' : '2️⃣ Référence'}
            </span>
          </div>
          <p className="text-xs mb-3" style={{ color: 'var(--jp-text-muted)' }}>
            {step === 1
              ? 'Renseigne les informations de ton virement Wave.'
              : 'Confirme avec la référence reçue par SMS ou dans l\'app Wave.'}
          </p>

          {/* ====== ÉTAPE 1 — Infos virement ====== */}
          {step === 1 && (
            <div className="space-y-3" data-testid="wave-deposit-step-1">
              {info && (
                <div className="rounded-xl p-3 text-sm" style={{ background: '#E0F7FE', color: '#063D4F' }} data-testid="wave-receiver-info">
                  <div className="font-bold mb-1">📞 Envoyer le paiement à :</div>
                  <div>Nom : <strong>{info.receiver_name}</strong></div>
                  <div className="flex items-center gap-2 mt-1">
                    <span>Numéro :</span>
                    <strong data-testid="wave-receiver-number" style={{ fontFamily: 'monospace', letterSpacing: '0.05em' }}>
                      {waveNumberDisplay}
                    </strong>
                    {info.receiver_number && (
                      <button type="button" onClick={() => copy(info.receiver_number)} className="jp-btn jp-btn-ghost jp-btn-xs">
                        {copied ? <CheckCircle size={14} /> : <Copy size={14} />}
                      </button>
                    )}
                  </div>
                </div>
              )}
              <div>
                <label className="jp-label">Montant à créditer (USD)</label>
                <input data-testid="wave-deposit-amount" type="number" min="1" step="0.01" required
                  value={montant} onChange={e => setMontant(e.target.value)}
                  className="jp-input text-sm" placeholder="ex. 10" />
                {quote && (
                  <p className="text-sm mt-2 font-bold" data-testid="wave-deposit-quote" style={{ color: '#0A6F8B' }}>
                    Vous devez envoyer : <span style={{ color: '#1B9CFC' }}>{Number(quote.montant_xof).toLocaleString('fr-FR')} FCFA</span>
                  </p>
                )}
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="jp-label">Date</label>
                  <input data-testid="wave-deposit-date" type="date" required max={today}
                    value={form.date_tx} onChange={e => setForm(f => ({ ...f, date_tx: e.target.value }))}
                    className="jp-input text-sm" />
                </div>
                <div>
                  <label className="jp-label">Heure</label>
                  <input data-testid="wave-deposit-time" type="time" required
                    value={form.heure_tx} onChange={e => setForm(f => ({ ...f, heure_tx: e.target.value }))}
                    className="jp-input text-sm" />
                </div>
              </div>
              <div>
                <label className="jp-label">Numéro expéditeur (le tien)</label>
                <input data-testid="wave-deposit-sender" type="tel" required minLength="8" maxLength="20"
                  value={form.numero_expediteur} onChange={e => setForm(f => ({ ...f, numero_expediteur: e.target.value }))}
                  className="jp-input text-sm" placeholder="+221 7X XXX XX XX" />
              </div>
              <div>
                <label className="jp-label">Nom de l'expéditeur</label>
                <input data-testid="wave-deposit-sender-name" required minLength="2" maxLength="120"
                  value={form.nom_expediteur} onChange={e => setForm(f => ({ ...f, nom_expediteur: e.target.value }))}
                  className="jp-input text-sm" placeholder="Prénom Nom" />
              </div>
              <button type="button" onClick={goToStep2} disabled={!step1Valid} data-testid="wave-deposit-step1-next"
                className="jp-btn jp-btn-full" style={{ background: '#1DC8FA', color: 'white', opacity: step1Valid ? 1 : 0.5 }}>
                J'ai effectué le virement Wave →
              </button>
            </div>
          )}

          {/* ====== ÉTAPE 2 — Référence Wave ====== */}
          {step === 2 && (
            <div className="space-y-3" data-testid="wave-deposit-step-2">
              <div className="p-4 rounded-xl"
                   style={{ background: 'rgba(27,156,252,0.08)',
                            border: '1px solid rgba(27,156,252,0.3)' }}
                   data-testid="wave-ref-help">
                <p className="font-bold text-sm mb-2" style={{ color: '#0A6F8B' }}>
                  📲 Comment trouver ta référence Wave ?
                </p>
                <ul className="text-xs opacity-80 space-y-1" style={{ color: '#063D4F' }}>
                  <li>• Dans le <strong>SMS de confirmation</strong> reçu après le virement</li>
                  <li>• Dans ton <strong>application Wave</strong> → Historique → détails de la transaction</li>
                  <li>• Format : <code style={{ background: 'rgba(0,0,0,0.06)', padding: '1px 4px', borderRadius: 3 }}>T_XXXXX-YYYYY</code></li>
                </ul>
              </div>
              <div>
                <label className="jp-label">
                  Référence Wave
                  <span className="text-xs opacity-60 ml-1 font-normal">(reçue par SMS ou app Wave)</span>
                </label>
                <input data-testid="wave-deposit-reference" required minLength="4" maxLength="120"
                  value={form.reference} onChange={e => setForm(f => ({ ...f, reference: e.target.value }))}
                  className="jp-input text-sm uppercase" placeholder="T_ABC123-XYZ789"
                  pattern="T_[A-Z0-9]+-[A-Z0-9]+"
                  style={{ borderColor: refValid ? undefined : 'var(--jp-error)' }} />
                {!refValid && (
                  <p className="text-xs mt-1" style={{ color: 'var(--jp-error)' }} data-testid="wave-ref-error">
                    Format invalide. Attendu : T_XXXXX-YYYYY (lettres/chiffres en majuscules, séparés par un tiret).
                  </p>
                )}
                {refValid && (
                  <p className="text-xs mt-1.5" style={{ color: 'var(--jp-text-muted)' }}>
                    Cette référence nous permet de vérifier ton virement.
                    Sans elle, ton dépôt ne pourra pas être validé.
                  </p>
                )}
              </div>
              <button type="button" onClick={submit}
                disabled={submitting || !refUpper || !refValid}
                data-testid="wave-deposit-submit"
                className="jp-btn jp-btn-full"
                style={{ background: '#1DC8FA', color: 'white',
                         opacity: (submitting || !refUpper || !refValid) ? 0.6 : 1 }}>
                {submitting ? 'Envoi…' : '✅ Confirmer mon dépôt'}
              </button>
              <button type="button" onClick={() => setStep(1)} data-testid="wave-deposit-step2-back"
                className="text-xs opacity-60 underline mt-1 mx-auto block"
                style={{ color: 'var(--jp-text-muted)' }}>
                ← Modifier mes informations
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
