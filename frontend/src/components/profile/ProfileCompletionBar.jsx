// iter240j — Barre de complétude profil. Reçoit `pct` (0-100), affiche
// progress bar gradient + message i18n contextuel.
import { useTranslation } from 'react-i18next';

export default function ProfileCompletionBar({ pct = 0 }) {
  const { t } = useTranslation();
  const safe = Math.max(0, Math.min(100, Math.round(pct)));
  return (
    <div data-testid="profile-completion-bar">
      <div className="flex items-center justify-between mb-1 text-xs">
        <span className="font-bold text-slate-700">
          {t('profile.completion', { defaultValue: 'Profil complété à {{pct}}%', pct: safe })}
        </span>
        <span className="text-slate-500" data-testid="profile-completion-pct">{safe}%</span>
      </div>
      <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{
            width: `${safe}%`,
            background: 'linear-gradient(90deg, #E01C2E 0%, #F59E0B 100%)',
          }}
        />
      </div>
    </div>
  );
}
