/**
 * iter235 — Wave Afrique de l'Ouest — Retrait utilisateur.
 * Strictement additif. Recrédit auto en cas de rejet.
 */
import { useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { ArrowUp, X } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function WaveWithdraw({ onSuccess }) {
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [montant, setMontant] = useState('');
  const [quote, setQuote] = useState(null);
  const [form, setForm] = useState({ numero_wave: '', nom_titulaire: '' });

  useEffect(() => {
    if (!open) return;
    // iter237i — track form_opened (best-effort).
    axios.post(`${API}/api/payment-methods/track`,
               { method: 'wave', flow: 'withdraw', action: 'form_opened' },
               { withCredentials: true }).catch(() => {});
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const m = parseFloat(montant);
    if (!m || m <= 0) { setQuote(null); return; }
    const t = setTimeout(() => {
      axios.post(`${API}/api/withdrawals/wave/quote`, { montant_usd: m }, { withCredentials: true })
        .then(r => setQuote(r.data)).catch(() => setQuote(null));
    }, 350);
    return () => clearTimeout(t);
  }, [montant, open]);

  const submit = async (e) => {
    e.preventDefault();
    const m = parseFloat(montant);
    if (!m || m <= 0) { toast.error('Montant invalide.'); return; }
    if (!form.numero_wave || !form.nom_titulaire) { toast.error('Tous les champs sont requis.'); return; }
    setSubmitting(true);
    try {
      await axios.post(`${API}/api/withdrawals/wave/submit`, {
        montant_usd: m,
        numero_wave: form.numero_wave.trim(),
        nom_titulaire: form.nom_titulaire.trim(),
      }, { withCredentials: true });
      toast.success('Retrait Wave soumis !');
      // iter237i — track submitted (best-effort).
      axios.post(`${API}/api/payment-methods/track`,
                 { method: 'wave', flow: 'withdraw', action: 'submitted' },
                 { withCredentials: true }).catch(() => {});
      setOpen(false); setMontant(''); setForm({ numero_wave: '', nom_titulaire: '' });
      onSuccess?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Échec de la soumission.');
    } finally { setSubmitting(false); }
  };

  return (
    <div className="jp-card-elevated p-5 mb-4" data-testid="wave-withdraw-card">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full flex items-center justify-center" style={{ background: '#1DC8FA', color: 'white', fontWeight: 800 }}>W</div>
          <div>
            <h4 className="font-['Outfit'] text-base font-bold" style={{ color: 'var(--jp-text)' }}>Retrait Wave</h4>
            <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>Afrique de l'Ouest · Recrédit auto si rejeté</p>
          </div>
        </div>
        <button data-testid="wave-withdraw-toggle" onClick={() => setOpen(v => !v)}
          className="jp-btn jp-btn-sm" style={{ background: 'var(--jp-secondary)', color: 'white' }}>
          {open ? <X size={16} /> : <ArrowUp size={16} />}
          <span className="ml-1">{open ? 'Fermer' : 'Retirer'}</span>
        </button>
      </div>
      {open && (
        <form onSubmit={submit} className="mt-4 space-y-3" data-testid="wave-withdraw-form">
          <div>
            <label className="jp-label">Montant à retirer (USD)</label>
            <input data-testid="wave-withdraw-amount" type="number" min="1" step="0.01" required
              value={montant} onChange={e => setMontant(e.target.value)}
              className="jp-input text-sm" placeholder="ex. 30" />
            {quote && (
              <p className="text-sm mt-2 font-bold" data-testid="wave-withdraw-quote" style={{ color: '#0A6F8B' }}>
                Vous recevrez : <span style={{ color: '#1B9CFC' }}>{Number(quote.montant_xof).toLocaleString('fr-FR')} FCFA</span>
                <span className="block text-[10px] font-normal mt-0.5" style={{ color: 'var(--jp-text-muted)' }}>Min : {quote.min_usd} USD</span>
              </p>
            )}
          </div>
          <div>
            <label className="jp-label">Numéro Wave à créditer</label>
            <input data-testid="wave-withdraw-number" type="tel" required minLength="8" maxLength="20"
              value={form.numero_wave} onChange={e => setForm(f => ({ ...f, numero_wave: e.target.value }))}
              className="jp-input text-sm" placeholder="+221 7X XXX XX XX" />
          </div>
          <div>
            <label className="jp-label">Nom du titulaire (doit correspondre à ton compte)</label>
            <input data-testid="wave-withdraw-name" required minLength="2" maxLength="120"
              value={form.nom_titulaire} onChange={e => setForm(f => ({ ...f, nom_titulaire: e.target.value }))}
              className="jp-input text-sm" placeholder="Prénom Nom" />
          </div>
          <button type="submit" disabled={submitting} data-testid="wave-withdraw-submit"
            className="jp-btn jp-btn-full" style={{ background: 'var(--jp-secondary)', color: 'white', opacity: submitting ? 0.6 : 1 }}>
            {submitting ? 'Envoi…' : 'Soumettre le retrait'}
          </button>
        </form>
      )}
    </div>
  );
}
