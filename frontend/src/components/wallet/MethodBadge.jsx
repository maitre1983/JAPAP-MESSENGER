/**
 * iter237g/h — MethodBadge : tag d'éligibilité pour chaque carte de paiement.
 * iter237h — Ajout bouton "Vérifier mon éligibilité" qui appelle
 *            /api/payment-methods/eligibility?method=...&flow=... et affiche
 *            un toast vert/rouge sans engager la saisie. Strictement additif.
 */
import { useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';

const API = process.env.REACT_APP_BACKEND_URL;

export default function MethodBadge({
  availability, countries, restricted, eligible,
  methodId, flow = 'deposit',  // iter237h
}) {
  const [checking, setChecking] = useState(false);
  const wrapperOpacity = eligible === false ? 0.78 : 1;

  const checkEligibility = async (e) => {
    e.stopPropagation();
    if (!methodId) return;
    setChecking(true);
    try {
      const { data } = await axios.get(
        `${API}/api/payment-methods/eligibility`,
        { params: { method: methodId, flow }, withCredentials: true },
      );
      if (data.eligible) {
        toast.success(`✅ Tu es éligible à cette méthode !`);
      } else {
        const cc = data.user_country || '∅';
        toast.error(
          `❌ Méthode non disponible pour ton pays (${cc}). ${data.suggestion || ''}`,
          { duration: 6000 },
        );
      }
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Vérification impossible.');
    } finally { setChecking(false); }
  };

  return (
    <div
      data-testid={`method-badge-${restricted ? 'restricted' : 'global'}`}
      className="px-3 py-2 rounded-t-2xl text-[11px] font-['Manrope'] flex items-center gap-2 -mb-2 flex-wrap"
      style={{
        background: restricted ? 'rgba(245, 158, 11, 0.10)'
                                : 'rgba(16, 185, 129, 0.08)',
        color: restricted ? '#92400E' : '#065F46',
        border: '1px dashed',
        borderColor: restricted ? 'rgba(245, 158, 11, 0.35)'
                                : 'rgba(16, 185, 129, 0.30)',
        opacity: wrapperOpacity,
      }}
    >
      <span>{restricted ? '📍' : '🌍'}</span>
      <span className="flex-1 truncate">{availability}</span>
      {restricted && countries && (
        <span className="px-1.5 py-0.5 rounded font-bold tracking-wider"
              style={{ background: 'rgba(245, 158, 11, 0.20)', fontSize: 10 }}>
          {countries}
        </span>
      )}
      {restricted && methodId && (
        <button
          type="button"
          onClick={checkEligibility}
          disabled={checking}
          data-testid={`method-badge-check-${methodId}-${flow}`}
          className="ml-auto text-[11px] underline whitespace-nowrap disabled:opacity-50"
          style={{ color: '#D97706' }}
        >
          {checking ? 'Vérification…' : 'Vérifier mon éligibilité →'}
        </button>
      )}
    </div>
  );
}
