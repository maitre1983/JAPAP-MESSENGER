/**
 * iter236 — Orange Money — Retrait utilisateur (wizard 5 étapes).
 * Réservé au Cameroun (+237). Recrédit auto si admin rejette.
 * Le taux (600) ne s'affiche jamais à l'utilisateur.
 */
import { useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { ArrowRight, ArrowLeft, CheckCircle, Spinner, X } from '@phosphor-icons/react';

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

export default function OrangeMoneyWithdraw({ onSuccess }) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(1);
  const [submitting, setSubmitting] = useState(false);

  const [montantUsd, setMontantUsd] = useState('');
  const [quote, setQuote] = useState(null); // { montant_xaf, min_usd }
  const [form, setForm] = useState({
    numero_om: '', numero_om_confirm: '', nom_titulaire: '',
  });
  const [balance, setBalance] = useState(null);

  useEffect(() => {
    if (!open) return;
    axios.get(`${API}/api/wallet/balance`, { withCredentials: true })
      .then(r => setBalance(parseFloat(r.data.balance_usd ?? r.data.balance ?? '0')))
      .catch(() => setBalance(null));
    // iter237i — track form_opened (best-effort).
    axios.post(`${API}/api/payment-methods/track`,
               { method: 'orange_money_cm', flow: 'withdraw', action: 'form_opened' },
               { withCredentials: true }).catch(() => {});
  }, [open]);

  useEffect(() => {
    if (!open || step !== 1) return;
    const m = parseFloat(montantUsd);
    if (!m || m <= 0) { setQuote(null); return; }
    const t = setTimeout(() => {
      axios.post(`${API}/api/withdrawals/orange-money/quote`, { montant_usd: m }, { withCredentials: true })
        .then(r => setQuote(r.data)).catch(() => setQuote(null));
    }, 350);
    return () => clearTimeout(t);
  }, [montantUsd, open, step]);

  const reset = () => {
    setOpen(false); setStep(1); setMontantUsd(''); setQuote(null);
    setForm({ numero_om: '', numero_om_confirm: '', nom_titulaire: '' });
  };

  const m = parseFloat(montantUsd);
  const minOk = quote && m >= Number(quote.min_usd);
  const balOk = balance == null || (m && m <= balance);
  const canNext1 = quote && minOk && balOk;

  const numbersMatch = form.numero_om && form.numero_om === form.numero_om_confirm;
  const canNext2 = numbersMatch && form.nom_titulaire.trim().length >= 2;

  const submit = async () => {
    if (!m || m <= 0) { toast.error('Montant invalide.'); return; }
    setSubmitting(true);
    setStep(4); // spinner
    try {
      await axios.post(`${API}/api/withdrawals/orange-money/submit`, {
        montant_usd: m,
        numero_om: form.numero_om.trim(),
        nom_titulaire: form.nom_titulaire.trim(),
      }, { withCredentials: true });
      setStep(5);
      // iter237i — track submitted (best-effort).
      axios.post(`${API}/api/payment-methods/track`,
                 { method: 'orange_money_cm', flow: 'withdraw', action: 'submitted' },
                 { withCredentials: true }).catch(() => {});
      onSuccess?.();
    } catch (e) {
      const status = e?.response?.status;
      const msg = e?.response?.data?.detail || 'Échec de la soumission.';
      if (status === 403) toast.error('Refusé : ' + msg);
      else if (status === 400) toast.error(msg);
      else if (status === 429) toast.error('Trop de demandes. Réessaie plus tard.');
      else toast.error(msg);
      setStep(3); // back to recap
    } finally { setSubmitting(false); }
  };

  return (
    <div className="jp-card-elevated p-5 mb-4" data-testid="om-withdraw-card">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full flex items-center justify-center" style={{ background: '#FF6600', color: 'white', fontWeight: 800 }}>OM</div>
          <div>
            <h4 className="font-['Outfit'] text-base font-bold" style={{ color: 'var(--jp-text)' }}>Retrait Orange Money</h4>
            <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>Cameroun · Numéro vérifié +237</p>
          </div>
        </div>
        <button data-testid="om-withdraw-toggle" onClick={() => (open ? reset() : setOpen(true))}
          className="jp-btn jp-btn-sm" style={{ background: 'var(--jp-secondary)', color: 'white' }}>
          {open ? <X size={16} /> : null}
          <span className={open ? 'ml-1' : ''}>{open ? 'Fermer' : 'Retirer'}</span>
        </button>
      </div>

      {open && (
        <div className="mt-4" data-testid="om-withdraw-form">
          <div className="flex items-center justify-between mb-4 px-1" data-testid="om-withdraw-stepper">
            <Step label="1. Montant" active={step === 1} done={step > 1} />
            <div className="flex-1 h-px mx-2" style={{ background: 'var(--jp-border)' }} />
            <Step label="2. Détails" active={step === 2} done={step > 2} />
            <div className="flex-1 h-px mx-2" style={{ background: 'var(--jp-border)' }} />
            <Step label="3. Récap" active={step === 3} done={step > 3} />
            <div className="flex-1 h-px mx-2" style={{ background: 'var(--jp-border)' }} />
            <Step label="4. Envoi" active={step === 4} done={step > 4} />
            <div className="flex-1 h-px mx-2" style={{ background: 'var(--jp-border)' }} />
            <Step label="5. Fin" active={step === 5} done={step > 5} />
          </div>

          {step === 1 && (
            <div className="space-y-3" data-testid="om-w-step-1">
              <div>
                <label className="jp-label">Montant à retirer (USD)</label>
                <input data-testid="om-withdraw-amount" type="number" min="1" step="0.01" required
                  value={montantUsd} onChange={e => setMontantUsd(e.target.value)}
                  className="jp-input text-sm" placeholder="ex. 30" autoFocus />
                {quote && (
                  <p className="text-sm mt-2 font-bold" data-testid="om-withdraw-quote" style={{ color: '#065F46' }}>
                    Vous recevrez : <span style={{ color: '#FF6600' }}>{Number(quote.montant_xaf).toLocaleString('fr-FR')} FCFA</span>
                  </p>
                )}
                {quote && !minOk && (
                  <p className="text-xs mt-1" style={{ color: 'var(--jp-error)' }} data-testid="om-w-min-error">
                    Montant minimum : {quote.min_usd} USD.
                  </p>
                )}
                {balance != null && m > balance && (
                  <p className="text-xs mt-1" style={{ color: 'var(--jp-error)' }} data-testid="om-w-balance-error">
                    Solde insuffisant ({balance.toFixed(2)} USD disponibles).
                  </p>
                )}
              </div>
              <button type="button" data-testid="om-w-step-1-next"
                disabled={!canNext1} onClick={() => setStep(2)}
                className="jp-btn jp-btn-full" style={{ background: '#FF6600', color: 'white', opacity: canNext1 ? 1 : 0.5 }}>
                Suivant <ArrowRight size={14} />
              </button>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-3" data-testid="om-w-step-2">
              <div>
                <label className="jp-label">Numéro Orange Money destinataire</label>
                <input data-testid="om-withdraw-number" type="tel" required minLength="8" maxLength="20"
                  value={form.numero_om} onChange={e => setForm(f => ({ ...f, numero_om: e.target.value }))}
                  className="jp-input text-sm" placeholder="+237 6XX XX XX XX" autoFocus />
              </div>
              <div>
                <label className="jp-label">Confirmer le numéro</label>
                <input data-testid="om-withdraw-number-confirm" type="tel" required
                  value={form.numero_om_confirm} onChange={e => setForm(f => ({ ...f, numero_om_confirm: e.target.value }))}
                  onPaste={e => e.preventDefault()}
                  className="jp-input text-sm" placeholder="Retaper le numéro (sans copier-coller)"
                  style={{ borderColor: form.numero_om_confirm && !numbersMatch ? 'var(--jp-error)' : undefined }} />
                {form.numero_om_confirm && !numbersMatch && (
                  <p className="text-xs mt-1" style={{ color: 'var(--jp-error)' }} data-testid="om-w-number-mismatch">
                    Les deux numéros ne correspondent pas.
                  </p>
                )}
              </div>
              <div>
                <label className="jp-label">Nom du titulaire</label>
                <input data-testid="om-withdraw-name" required minLength="2" maxLength="120"
                  value={form.nom_titulaire} onChange={e => setForm(f => ({ ...f, nom_titulaire: e.target.value }))}
                  className="jp-input text-sm" placeholder="Prénom Nom" />
                <p className="text-[11px] mt-1" style={{ color: 'var(--jp-text-muted)' }}>
                  Le nom doit correspondre à votre compte Japap Messenger.
                </p>
              </div>
              <div className="flex gap-2">
                <button type="button" onClick={() => setStep(1)} data-testid="om-w-step-2-back"
                  className="jp-btn jp-btn-ghost"><ArrowLeft size={14} /> Retour</button>
                <button type="button" onClick={() => setStep(3)} data-testid="om-w-step-2-next"
                  disabled={!canNext2}
                  className="jp-btn jp-btn-full" style={{ background: '#FF6600', color: 'white', opacity: canNext2 ? 1 : 0.5 }}>
                  Suivant <ArrowRight size={14} />
                </button>
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-3" data-testid="om-w-step-3">
              <div className="rounded-xl p-4" style={{ background: '#FFF4E6', color: '#7C3F00' }} data-testid="om-withdraw-recap">
                <div className="flex justify-between text-sm py-1"><span>Montant retiré</span><strong>{montantUsd} USD</strong></div>
                <div className="flex justify-between text-sm py-1"><span>Vous recevrez</span><strong>{quote ? Number(quote.montant_xaf).toLocaleString('fr-FR') : ''} FCFA</strong></div>
                <div className="flex justify-between text-sm py-1"><span>Numéro OM</span><strong data-testid="om-w-recap-number">{form.numero_om}</strong></div>
                <div className="flex justify-between text-sm py-1"><span>Nom</span><strong data-testid="om-w-recap-name">{form.nom_titulaire}</strong></div>
              </div>
              <div className="rounded-xl p-3 text-xs" style={{ background: '#FEF3C7', color: '#92400E' }}>
                Votre solde sera débité immédiatement. Traitement sous 24h ouvrées. Si l'admin rejette, le montant sera <strong>automatiquement recrédité</strong>.
              </div>
              <div className="flex gap-2">
                <button type="button" onClick={() => setStep(2)} data-testid="om-w-step-3-back"
                  className="jp-btn jp-btn-ghost"><ArrowLeft size={14} /> Modifier</button>
                <button type="button" onClick={submit} disabled={submitting} data-testid="om-withdraw-submit"
                  className="jp-btn jp-btn-full" style={{ background: '#FF6600', color: 'white', opacity: submitting ? 0.6 : 1 }}>
                  Confirmer le retrait
                </button>
              </div>
            </div>
          )}

          {step === 4 && (
            <div className="text-center py-8" data-testid="om-w-step-4">
              <Spinner size={48} className="animate-spin" style={{ color: '#FF6600', margin: '0 auto' }} />
              <p className="text-sm mt-3" style={{ color: 'var(--jp-text-muted)' }}>Traitement en cours…</p>
            </div>
          )}

          {step === 5 && (
            <div className="text-center py-6" data-testid="om-w-step-5">
              <CheckCircle size={56} weight="fill" style={{ color: '#10B981', margin: '0 auto' }} />
              <h4 className="font-['Outfit'] text-lg font-bold mt-3">Demande soumise !</h4>
              <p className="text-sm mt-1" style={{ color: 'var(--jp-text-muted)' }}>
                Votre solde a été débité. Vous recevrez un email dès que le virement sera effectué sur votre numéro Orange Money.
              </p>
              <button onClick={reset} className="jp-btn jp-btn-primary mt-4" data-testid="om-withdraw-close">Fermer</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
