/**
 * iter237n — Modal d'acceptation des Conditions Générales de Jeu (CGJ).
 *
 * Affiché lorsque l'utilisateur tente de miser pour la première fois et
 * que `users.cgje_accepted_at` est NULL → backend renvoie HTTP 451.
 *
 * Une fois accepté, le bonton appelle POST /api/legal/accept-cgje puis
 * `onAccepted()` (le parent retentera /paid/start).
 *
 * iter237o — Récap barème (gain/perte par score) chargé depuis
 * /api/quiz/daily-challenge/paid/config pour transparence avant acceptation.
 */
import { useState, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Link } from 'react-router-dom';
import { ShieldCheck, X } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function CgjAcceptanceModal({ open, onClose, onAccepted }) {
  const [checked, setChecked] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [bareme, setBareme] = useState(null);

  // iter237o — Charger le barème dynamique pour l'afficher avant acceptation.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    axios.get(`${API}/api/quiz/daily-challenge/paid/config`, { withCredentials: true })
      .then(r => { if (!cancelled) setBareme(r.data?.score_pct || null); })
      .catch(() => { /* fail silent — récap optionnel */ });
    return () => { cancelled = true; };
  }, [open]);

  if (!open) return null;

  const accept = async () => {
    if (!checked || submitting) return;
    setSubmitting(true);
    try {
      await axios.post(`${API}/api/legal/accept-cgje`, {}, { withCredentials: true });
      toast.success('Conditions de jeu acceptées.');
      onAccepted?.();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Acceptation refusée.');
    } finally { setSubmitting(false); }
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4"
         style={{ background: 'rgba(0,0,0,0.65)' }}
         onClick={onClose}>
      <div className="jp-card-elevated w-full max-w-md p-5 max-h-[90vh] overflow-y-auto"
           data-testid="cgj-acceptance-modal"
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-['Outfit'] text-lg font-extrabold flex items-center gap-2"
              style={{ color: '#0F056B' }}>
            <ShieldCheck size={20} weight="duotone" />
            Conditions Générales de Jeu
          </h3>
          <button onClick={onClose} className="p-1 rounded-full hover:bg-gray-100"
                  data-testid="cgj-modal-close">
            <X size={18} />
          </button>
        </div>

        <p className="text-sm leading-relaxed mb-3">
          Avant de miser pour la première fois, tu dois accepter les
          <strong> Conditions Générales de Jeu</strong> de JAPAP TECHNOLOGIES PLC.
        </p>

        <div className="rounded-xl p-3 text-xs leading-relaxed mb-4"
             style={{ background: 'rgba(15,5,107,0.05)',
                      border: '1px solid rgba(15,5,107,0.15)' }}>
          <p className="mb-2"><strong>Points essentiels :</strong></p>
          <ul className="list-disc list-inside space-y-1">
            <li>Les jeux payants s'adressent uniquement aux <strong>majeurs</strong> (18+).</li>
            <li>Les pertes sont <strong>réelles</strong> — débitées immédiatement de ton wallet.</li>
            <li>Le résultat dépend de tes connaissances : <strong>aucune garantie</strong> de gain.</li>
            <li>Une seule participation payante par jour, par compte.</li>
            <li>Les paris depuis des juridictions où le jeu en ligne est interdit
                sont exclus.</li>
          </ul>
          <p className="mt-2">
            Le texte intégral est consultable sur la page{' '}
            <Link to="/legal/conditions-de-jeu" target="_blank"
                  className="underline font-bold"
                  data-testid="cgj-modal-fulltext-link">
              Conditions Générales de Jeu
            </Link>.
          </p>
        </div>

        {/* iter237o — Récap dynamique du barème (transparence avant acceptation) */}
        {bareme && (
          <div className="rounded-xl p-3 mb-4"
               data-testid="cgj-modal-bareme"
               style={{ background: 'rgba(255,193,7,0.07)',
                        border: '1px solid rgba(255,193,7,0.25)' }}>
            <p className="text-xs font-bold mb-2" style={{ color: '#B8860B' }}>
              📊 Rappel du barème (sur ta mise)
            </p>
            <div className="text-xs space-y-1">
              <div className="flex justify-between" data-testid="cgj-bareme-5">
                <span>5/5 🏆</span>
                <span className="font-bold" style={{ color: '#10B981' }}>
                  {bareme['5'] >= 0 ? '+' : ''}{bareme['5']}%
                </span>
              </div>
              <div className="flex justify-between" data-testid="cgj-bareme-4">
                <span>4/5 😊</span>
                <span className="font-bold"
                      style={{ color: bareme['4'] >= 0 ? '#10B981' : '#E01C2E' }}>
                  {bareme['4'] >= 0 ? '+' : ''}{bareme['4']}%
                </span>
              </div>
              <div className="flex justify-between" data-testid="cgj-bareme-3">
                <span>3/5 😐</span>
                <span className="font-bold" style={{ color: '#E01C2E' }}>
                  {bareme['3']}%
                </span>
              </div>
              <div className="flex justify-between" data-testid="cgj-bareme-2">
                <span>2/5 😟</span>
                <span className="font-bold" style={{ color: '#E01C2E' }}>
                  {bareme['2']}%
                </span>
              </div>
              <div className="flex justify-between" data-testid="cgj-bareme-01">
                <span>0–1/5 💀</span>
                <span className="font-bold" style={{ color: '#991B1B' }}>
                  {bareme['0_1']}%
                </span>
              </div>
            </div>
          </div>
        )}

        <label className="flex items-start gap-2 cursor-pointer mb-4">
          <input type="checkbox" checked={checked}
                 onChange={() => setChecked(c => !c)}
                 className="mt-0.5 accent-[#0F056B]"
                 data-testid="cgj-modal-checkbox" />
          <span className="text-xs leading-relaxed">
            J'ai lu et j'accepte les Conditions Générales de Jeu de
            JAPAP TECHNOLOGIES PLC. Je certifie être <strong>majeur (18+)</strong>
            et jouer dans une juridiction où le jeu d'agilité payant est licite.
          </span>
        </label>

        <div className="flex gap-2">
          <button onClick={onClose}
                  data-testid="cgj-modal-cancel"
                  className="jp-btn flex-1 jp-btn-sm"
                  style={{ background: 'var(--jp-surface-secondary)' }}>
            Annuler
          </button>
          <button onClick={accept}
                  disabled={!checked || submitting}
                  data-testid="cgj-modal-accept"
                  className="jp-btn jp-btn-primary flex-1 jp-btn-sm"
                  style={{ opacity: checked && !submitting ? 1 : 0.5 }}>
            {submitting ? '...' : 'J\'accepte'}
          </button>
        </div>
      </div>
    </div>
  );
}
