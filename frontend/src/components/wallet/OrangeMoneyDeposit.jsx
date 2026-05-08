/**
 * iter236 — Orange Money — Dépôt utilisateur (wizard 4 étapes).
 * Disponible partout SAUF Ghana (country !== 'GH'), gate serveur + client.
 * Le taux de conversion (605) N'APPARAÎT JAMAIS à l'utilisateur.
 */
import { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { ArrowRight, ArrowLeft, CheckCircle, Copy, X } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

const Step = ({ label, active, done }) => (
  <div className="flex items-center gap-1.5 text-xs font-bold"
    style={{ color: done ? '#10B981' : (active ? '#FF6600' : 'var(--jp-text-muted)') }}>
    <div className="w-5 h-5 rounded-full flex items-center justify-center text-[10px]"
      style={{ background: done ? '#10B981' : (active ? '#FF6600' : 'var(--jp-surface-secondary)'),
               color: (done || active) ? 'white' : 'var(--jp-text-muted)' }}>
      {done ? '✓' : ''}
    </div>
    {label}
  </div>
);

export default function OrangeMoneyDeposit({ onSuccess }) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(1);
  const [info, setInfo] = useState(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const [montantUsd, setMontantUsd] = useState('');
  const [quote, setQuote] = useState(null);           // { montant_xaf }
  const [montantXafEdit, setMontantXafEdit] = useState(''); // input modifiable
  const [form, setForm] = useState({
    numero_expediteur: '', date_tx: '', heure_tx: '', reference: '',
  });

  const today = useMemo(() => new Date().toISOString().slice(0, 10), []);

  // Load receiver info when opened
  useEffect(() => {
    if (!open || info) return;
    setLoading(true);
    axios.get(`${API}/api/deposits/orange-money/info`, { withCredentials: true })
      .then(r => setInfo(r.data))
      .catch(e => { toast.error(e?.response?.data?.detail || 'Méthode indisponible.'); setOpen(false); })
      .finally(() => setLoading(false));
    // iter237i — track form_opened (best-effort).
    axios.post(`${API}/api/payment-methods/track`,
               { method: 'orange_money_cm', flow: 'deposit', action: 'form_opened' },
               { withCredentials: true }).catch(() => {});
  }, [open, info]);

  // Quote debounced
  useEffect(() => {
    if (!open || step !== 1) return;
    const m = parseFloat(montantUsd);
    if (!m || m <= 0) { setQuote(null); setMontantXafEdit(''); return; }
    const t = setTimeout(() => {
      axios.post(`${API}/api/deposits/orange-money/quote`, { montant_usd: m }, { withCredentials: true })
        .then(r => {
          setQuote(r.data);
          setMontantXafEdit(String(r.data.montant_xaf));
        })
        .catch(() => setQuote(null));
    }, 350);
    return () => clearTimeout(t);
  }, [montantUsd, open, step]);

  const receiverClean = (info?.receiver_number || '').replace(/\D/g, ''); // strip +, spaces
  const ussdCode = quote && receiverClean
    ? `#150*14*556348*${receiverClean}*${quote.montant_xaf}*VOTRE CODE SECRET#`
    : '';

  const reset = () => {
    setOpen(false); setStep(1); setMontantUsd(''); setQuote(null);
    setMontantXafEdit(''); setForm({ numero_expediteur: '', date_tx: '', heure_tx: '', reference: '' });
  };
  const copy = async (txt) => {
    try { await navigator.clipboard.writeText(txt); toast.success('Copié !'); } catch {}
  };

  const submit = async () => {
    const m = parseFloat(montantUsd);
    if (!m || m <= 0) { toast.error('Montant invalide.'); return; }
    if (!form.numero_expediteur || !form.date_tx || !form.heure_tx || !form.reference) {
      toast.error('Tous les champs sont requis.'); return;
    }
    setSubmitting(true);
    try {
      await axios.post(`${API}/api/deposits/orange-money/submit`, {
        montant_usd: m,
        numero_expediteur: form.numero_expediteur.trim(),
        date_tx: form.date_tx,
        heure_tx: form.heure_tx,
        reference: form.reference.trim().toUpperCase(),
      }, { withCredentials: true });
      setStep(4);
      // iter237i — track submitted (best-effort).
      axios.post(`${API}/api/payment-methods/track`,
                 { method: 'orange_money_cm', flow: 'deposit', action: 'submitted' },
                 { withCredentials: true }).catch(() => {});
      onSuccess?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Échec de la soumission.');
    } finally { setSubmitting(false); }
  };

  return (
    <div className="jp-card-elevated p-5 mb-4" data-testid="om-deposit-card">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full flex items-center justify-center" style={{ background: '#FF6600', color: 'white', fontWeight: 800 }}>OM</div>
          <div>
            <h4 className="font-['Outfit'] text-base font-bold" style={{ color: 'var(--jp-text)' }}>Dépôt Orange Money</h4>
            <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>Crédit après vérification (≤ 30 min)</p>
          </div>
        </div>
        <button
          data-testid="om-deposit-toggle"
          onClick={() => (open ? reset() : setOpen(true))}
          className="jp-btn jp-btn-sm" style={{ background: '#FF6600', color: 'white' }}>
          {open ? <X size={16} /> : null}
          <span className={open ? 'ml-1' : ''}>{open ? 'Fermer' : 'Déposer'}</span>
        </button>
      </div>

      {open && (
        <div className="mt-4" data-testid="om-deposit-form">
          {/* stepper */}
          <div className="flex items-center justify-between mb-4 px-1" data-testid="om-deposit-stepper">
            <Step label="1. Montant" active={step === 1} done={step > 1} />
            <div className="flex-1 h-px mx-2" style={{ background: 'var(--jp-border)' }} />
            <Step label="2. SMS" active={step === 2} done={step > 2} />
            <div className="flex-1 h-px mx-2" style={{ background: 'var(--jp-border)' }} />
            <Step label="3. Confirmation" active={step === 3} done={step > 3} />
            <div className="flex-1 h-px mx-2" style={{ background: 'var(--jp-border)' }} />
            <Step label="4. Fin" active={step === 4} done={step > 4} />
          </div>

          {loading && <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>}

          {/* STEP 1 — Montant + USSD */}
          {step === 1 && info && (
            <div className="space-y-3" data-testid="om-step-1">
              <div>
                <label className="jp-label">Montant à créditer (USD)</label>
                <input data-testid="om-deposit-amount" type="number" min="1" step="0.01" required
                  value={montantUsd} onChange={e => setMontantUsd(e.target.value)}
                  className="jp-input text-sm" placeholder="ex. 10" autoFocus />
                {quote && (
                  <p className="text-sm mt-2 font-bold" data-testid="om-deposit-quote" style={{ color: '#7C3F00' }}>
                    Vous devez envoyer : <span style={{ color: '#FF6600' }}>{Number(quote.montant_xaf).toLocaleString('fr-FR')} FCFA</span>
                  </p>
                )}
              </div>

              {quote && receiverClean && (
                <>
                  <div className="rounded-xl p-3 text-sm" style={{ background: '#FFF4E6', color: '#7C3F00' }} data-testid="om-receiver-info">
                    <div className="font-bold mb-1">Bénéficiaire</div>
                    <div className="text-base font-bold">{info.receiver_name}</div>
                    <div className="text-xs mt-1">
                      Composez le code ci-dessous sur votre téléphone Orange.
                      Une fois le SMS de confirmation reçu, cliquez sur <strong>Suivant</strong>.
                    </div>
                  </div>
                  <div className="rounded-xl p-3" style={{ background: '#1A1A1A', color: '#FF9A3C' }} data-testid="om-ussd-block">
                    <div className="text-[10px] uppercase tracking-widest opacity-80 mb-1">Code USSD à composer</div>
                    <div className="flex items-center gap-2">
                      <code className="text-sm font-bold break-all" data-testid="om-ussd-code" style={{ fontFamily: 'monospace' }}>
                        {ussdCode}
                      </code>
                      <button type="button" onClick={() => copy(ussdCode)} className="jp-btn jp-btn-xs shrink-0"
                        style={{ background: 'rgba(255,255,255,0.1)', color: 'white' }} data-testid="om-ussd-copy">
                        <Copy size={12} />
                      </button>
                    </div>
                    <div className="text-[10px] mt-2 opacity-75">
                      Remplacez <strong>VOTRE CODE SECRET</strong> par votre code confidentiel Orange Money.
                    </div>
                  </div>
                </>
              )}

              <div className="flex gap-2 pt-1">
                <button type="button" data-testid="om-step-1-next"
                  disabled={!quote}
                  onClick={() => setStep(2)}
                  className="jp-btn jp-btn-full"
                  style={{ background: '#FF6600', color: 'white', opacity: quote ? 1 : 0.5 }}>
                  Suivant <ArrowRight size={14} />
                </button>
              </div>
            </div>
          )}

          {/* STEP 2 — Avertissement SMS */}
          {step === 2 && (
            <div className="space-y-3" data-testid="om-step-2">
              <div className="rounded-xl p-4" style={{ background: '#FEF3C7', color: '#92400E' }}>
                <div className="font-bold text-sm mb-1">⚠️ Confirmation requise</div>
                <p className="text-sm">
                  Ne continuez que si vous avez reçu le SMS de confirmation Orange Money indiquant que le paiement de <strong>{quote?.montant_xaf ? Number(quote.montant_xaf).toLocaleString('fr-FR') : ''} FCFA</strong> a bien été envoyé.
                </p>
              </div>
              <div className="flex gap-2">
                <button type="button" onClick={() => setStep(1)} data-testid="om-step-2-back"
                  className="jp-btn jp-btn-ghost"><ArrowLeft size={14} /> Retour</button>
                <button type="button" onClick={() => setStep(3)} data-testid="om-step-2-next"
                  className="jp-btn jp-btn-full" style={{ background: '#FF6600', color: 'white' }}>
                  J'ai reçu le SMS <ArrowRight size={14} />
                </button>
              </div>
            </div>
          )}

          {/* STEP 3 — Formulaire */}
          {step === 3 && (
            <div className="space-y-3" data-testid="om-step-3">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="jp-label">Date</label>
                  <input data-testid="om-deposit-date" type="date" required max={today}
                    value={form.date_tx} onChange={e => setForm(f => ({ ...f, date_tx: e.target.value }))}
                    className="jp-input text-sm" />
                </div>
                <div>
                  <label className="jp-label">Heure</label>
                  <input data-testid="om-deposit-time" type="time" required
                    value={form.heure_tx} onChange={e => setForm(f => ({ ...f, heure_tx: e.target.value }))}
                    className="jp-input text-sm" />
                </div>
              </div>
              <div>
                <label className="jp-label">Numéro expéditeur (le tien)</label>
                <input data-testid="om-deposit-sender" type="tel" required minLength="8" maxLength="20"
                  value={form.numero_expediteur} onChange={e => setForm(f => ({ ...f, numero_expediteur: e.target.value }))}
                  className="jp-input text-sm" placeholder="+237 6XX XX XX XX" />
              </div>
              <div>
                <label className="jp-label">Référence Orange Money (SMS)</label>
                <input data-testid="om-deposit-reference" required minLength="4" maxLength="120"
                  value={form.reference} onChange={e => setForm(f => ({ ...f, reference: e.target.value }))}
                  className="jp-input text-sm uppercase" placeholder="ex. CI220331.1234.A12345" />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="jp-label">Montant envoyé (FCFA)</label>
                  <input data-testid="om-deposit-xaf" type="number" required min="1"
                    value={montantXafEdit} onChange={e => setMontantXafEdit(e.target.value)}
                    className="jp-input text-sm" />
                </div>
                <div>
                  <label className="jp-label">Équivalent (USD)</label>
                  <input data-testid="om-deposit-usd-readonly" readOnly value={montantUsd}
                    className="jp-input text-sm" style={{ background: 'var(--jp-surface-secondary)' }} />
                </div>
              </div>
              <div className="flex gap-2">
                <button type="button" onClick={() => setStep(2)} data-testid="om-step-3-back"
                  className="jp-btn jp-btn-ghost"><ArrowLeft size={14} /> Retour</button>
                <button type="button" onClick={submit} disabled={submitting} data-testid="om-deposit-submit"
                  className="jp-btn jp-btn-full" style={{ background: '#FF6600', color: 'white', opacity: submitting ? 0.6 : 1 }}>
                  {submitting ? 'Envoi…' : 'Soumettre le dépôt'}
                </button>
              </div>
            </div>
          )}

          {/* STEP 4 — Succès */}
          {step === 4 && (
            <div className="text-center py-6" data-testid="om-step-4">
              <CheckCircle size={56} weight="fill" style={{ color: '#10B981', margin: '0 auto' }} />
              <h4 className="font-['Outfit'] text-lg font-bold mt-3">Demande soumise !</h4>
              <p className="text-sm mt-1" style={{ color: 'var(--jp-text-muted)' }}>
                Votre demande a été soumise à Japap Messenger. Elle sera traitée après vérification.
                <br />Vous recevrez un email dès que votre compte sera crédité.
              </p>
              <button onClick={reset} className="jp-btn jp-btn-primary mt-4" data-testid="om-deposit-close">Fermer</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
