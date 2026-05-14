/**
 * iter239y — JuryHallOfFame
 * ----------------------------------------------------------------
 * Section "Hall of Fame des Jurés" affichée sur la page Crowdfunding.
 *
 * - Fetch GET /api/crowdfunding/jury/members (public)
 * - Affiche les jurés actifs (non révoqués, non expirés)
 * - Pour chaque juré : avatar, nom, drapeau pays, nb victoires,
 *   multiplicateur (+50, +100…), bouton télécharger certificat
 * - 5 langues (FR/EN/ES/AR/RU)
 * - Listen 'japap:refresh' pour re-fetch automatique
 *
 * Aucune dépendance à un endpoint backend nouveau : tout est déjà dispo.
 */
import { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

// Drapeaux émoji simples — additif (tableau lisible, pas hardcode métier).
const FLAG_BY_CC = {
  CI: '🇨🇮', CM: '🇨🇲', SN: '🇸🇳', BJ: '🇧🇯', TG: '🇹🇬',
  GA: '🇬🇦', CG: '🇨🇬', CD: '🇨🇩', RW: '🇷🇼', BF: '🇧🇫',
  ML: '🇲🇱', NE: '🇳🇪', GH: '🇬🇭', NG: '🇳🇬', KE: '🇰🇪',
  FR: '🇫🇷', BE: '🇧🇪', CA: '🇨🇦', US: '🇺🇸', GB: '🇬🇧',
};

export default function JuryHallOfFame() {
  const { t, i18n } = useTranslation();
  const [members, setMembers] = useState(null);  // null = loading, [] = empty
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/crowdfunding/jury/members`, {
        params: { limit: 24 },
      });
      setMembers(Array.isArray(data) ? data : []);
      setError(false);
    } catch (e) {
      setError(true);
      setMembers([]);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const h = () => load();
    window.addEventListener('japap:refresh', h);
    return () => window.removeEventListener('japap:refresh', h);
  }, [load]);

  // Hide section entirely while loading (avoid layout flash) ; if empty, show
  // a small encouraging empty-state so newcomers see this exists.
  if (members === null) return null;

  const downloadCert = (userId) => {
    // iter240l — Préférer le SVG premium (multilingue, R2 cached). Le PNG legacy
    // reste accessible pour rétrocompatibilité.
    const lang = (i18n.language || 'fr').slice(0, 2);
    const url = `${API}/api/crowdfunding/jury/certificate/${userId}.svg?lang=${lang}`;
    window.open(url, '_blank', 'noopener');
  };

  const fmtDate = (iso) => {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleDateString(i18n.language || 'fr-FR', {
        day: '2-digit', month: 'short', year: 'numeric',
      });
    } catch { return ''; }
  };

  if (members.length === 0) {
    return (
      <section
        data-testid="cf-jury-hall-section"
        className="mt-6 rounded-2xl bg-gradient-to-br from-amber-50 to-rose-50 border border-amber-100 p-5"
        aria-label={t('crowdfunding.jury_hall_title', { defaultValue: 'Hall of Fame des Jurés' })}>
        <div className="flex items-center gap-2 mb-2">
          <span aria-hidden style={{ fontSize: 22 }}>🏆</span>
          <h3 className="text-base font-bold text-slate-900">
            {t('crowdfunding.jury_hall_title', { defaultValue: 'Hall of Fame des Jurés' })}
          </h3>
        </div>
        <p className="text-sm text-slate-600" data-testid="cf-jury-hall-empty">
          {t('crowdfunding.jury_hall_empty', {
            defaultValue: 'Aucun juré pour l\'instant. Sois le premier à remporter un cycle pour rejoindre le jury !',
          })}
        </p>
      </section>
    );
  }

  return (
    <section
      data-testid="cf-jury-hall-section"
      className="mt-6 rounded-2xl bg-gradient-to-br from-amber-50 to-rose-50 border border-amber-100 p-4 sm:p-5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span aria-hidden style={{ fontSize: 22 }}>🏆</span>
          <h3 className="text-base font-bold text-slate-900">
            {t('crowdfunding.jury_hall_title', { defaultValue: 'Hall of Fame des Jurés' })}
          </h3>
          <span className="text-xs text-slate-500 font-medium"
                data-testid="cf-jury-hall-count">
            ({members.length})
          </span>
        </div>
        {error && (
          <button
            onClick={load}
            data-testid="cf-jury-hall-retry"
            className="text-xs text-rose-700 underline">
            {t('common.retry', { defaultValue: 'Réessayer' })}
          </button>
        )}
      </div>

      <p className="text-xs text-slate-600 mb-3" data-testid="cf-jury-hall-intro">
        {t('crowdfunding.jury_hall_intro', {
          defaultValue: 'Ces anciens gagnants forment le jury actif. Leur vote a un poids majoré.',
        })}
      </p>

      <div
        className="grid grid-cols-1 sm:grid-cols-2 gap-2.5"
        role="list">
        {members.map((m) => {
          const flag = FLAG_BY_CC[(m.country_code || '').toUpperCase()] || '';
          const wins = Math.max(1, Number(m.total_wins || 1));
          const weight = Math.max(1, Number(m.vote_weight || 1));
          return (
            <article
              role="listitem"
              key={m.user_id}
              data-testid={`cf-jury-card-${m.user_id}`}
              className="bg-white border border-slate-200 rounded-xl p-3 flex items-center gap-3 hover:shadow-md transition">
              <div className="w-12 h-12 rounded-full bg-gradient-to-br from-amber-300 to-rose-400 flex items-center justify-center flex-shrink-0 overflow-hidden">
                {m.avatar ? (
                  <img
                    src={m.avatar.startsWith('http') ? m.avatar : `${API}${m.avatar}`}
                    alt=""
                    onError={(e) => { e.currentTarget.style.display = 'none'; }}
                    className="w-12 h-12 rounded-full object-cover" />
                ) : (
                  <span className="text-white font-bold text-lg" aria-hidden>
                    {(m.name || '?').charAt(0).toUpperCase()}
                  </span>
                )}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  <span className="font-semibold text-sm text-slate-900 truncate"
                        data-testid={`cf-jury-name-${m.user_id}`}>
                    {m.name || '—'}
                  </span>
                  {flag && <span aria-label={m.country_code} title={m.country_code}>{flag}</span>}
                </div>
                <div className="flex items-center gap-1.5 flex-wrap mt-0.5">
                  <span
                    data-testid={`cf-jury-badge-${m.user_id}`}
                    className="inline-flex items-center gap-1 bg-amber-100 text-amber-800 px-1.5 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wide">
                    🎖️ {t('crowdfunding.jury_badge', { defaultValue: 'Membre du Jury' })}
                  </span>
                  <span className="text-[11px] text-slate-500">
                    {t('crowdfunding.jury_wins_count', {
                      count: wins,
                      defaultValue_one: '{{count}} victoire',
                      defaultValue_other: '{{count}} victoires',
                    })}
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-1.5 text-xs">
                  <span
                    data-testid={`cf-jury-weight-${m.user_id}`}
                    className="bg-rose-100 text-rose-700 px-2 py-0.5 rounded-full font-bold">
                    +{weight}
                  </span>
                  {m.granted_at && (
                    <span className="text-[10px] text-slate-400">
                      · {fmtDate(m.granted_at)}
                    </span>
                  )}
                </div>
              </div>
              <button
                onClick={() => downloadCert(m.user_id)}
                title={t('crowdfunding.jury_certificate_download', { defaultValue: 'Télécharger le certificat' })}
                data-testid={`cf-jury-cert-btn-${m.user_id}`}
                className="flex-shrink-0 bg-slate-900 hover:bg-slate-700 text-white rounded-full w-10 h-10 flex items-center justify-center transition"
                aria-label={t('crowdfunding.jury_certificate_download', { defaultValue: 'Télécharger le certificat' })}>
                <span aria-hidden style={{ fontSize: 16 }}>📜</span>
              </button>
            </article>
          );
        })}
      </div>
    </section>
  );
}
