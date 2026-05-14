// iter240e — Affiche le badge "Membre du Jury" + bouton "Télécharger mon
// certificat" sur la ProfilePage. Le composant ne rend rien si l'utilisateur
// n'est pas (ou plus) membre actif du jury.
import { useEffect, useState } from 'react';
import axios from 'axios';
import { useTranslation } from 'react-i18next';
import { Crown } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function ProfileJuryBadge() {
  const { t } = useTranslation();
  const [state, setState] = useState(null);

  useEffect(() => {
    let alive = true;
    axios.get(`${API}/api/crowdfunding/jury/me`, { withCredentials: true })
      .then(({ data }) => { if (alive) setState(data); })
      .catch(() => { if (alive) setState(null); });
    return () => { alive = false; };
  }, []);

  if (!state || !state.is_jury) return null;

  const userId = state?.memberships?.[0]?.user_id;
  const certHref = userId ? `${API}/api/crowdfunding/jury/certificate/${userId}.png` : null;
  const winsCount = state?.memberships?.length || 1;
  const voteWeight = state?.current_vote_weight;

  return (
    <div
      data-testid="profile-jury-badge-section"
      className="mt-4 rounded-2xl p-4 border border-amber-200"
      style={{ background: 'linear-gradient(135deg, rgba(26,26,110,0.06), rgba(233,30,99,0.06))' }}>
      <div className="flex items-center gap-3 mb-2">
        <div className="w-10 h-10 rounded-full flex items-center justify-center text-white shadow-sm"
             style={{ background: 'linear-gradient(135deg,#1a1a6e,#e91e63)' }}>
          <Crown size={20} weight="fill" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span
              data-testid="profile-jury-badge"
              className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-extrabold"
              style={{ background: '#ffd700', color: '#1a1a6e' }}>
              ⚖️ {t('crowdfunding.jury_badge', { defaultValue: 'Membre du Jury' })}
            </span>
            {voteWeight && voteWeight > 1 && (
              <span
                data-testid="profile-jury-vote-weight"
                className="text-xs font-bold text-rose-700">
                {t('crowdfunding.jury_vote_weight', {
                  defaultValue: 'Votre vote vaut {{weight}} votes',
                  weight: voteWeight,
                })}
              </span>
            )}
          </div>
          <div className="text-xs text-slate-600 mt-0.5">
            {t('crowdfunding.jury_wins_count', {
              defaultValue: '{{count}} victoire(s)',
              count: winsCount,
              defaultValue_plural: '{{count}} victoires',
            })}
          </div>
        </div>
      </div>
      {certHref && (
        <a
          href={certHref}
          target="_blank"
          rel="noopener noreferrer"
          download
          data-testid="profile-jury-certificate-btn"
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-bold no-underline"
          style={{ background: '#ffd700', color: '#1a1a6e' }}>
          📜 {t('crowdfunding.jury_certificate_download', { defaultValue: 'Télécharger mon certificat' })}
        </a>
      )}
    </div>
  );
}
