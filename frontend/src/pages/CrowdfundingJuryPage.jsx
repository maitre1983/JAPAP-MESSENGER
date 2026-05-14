// iter240l — Page publique dédiée /crowdfunding/jury
// Réutilise le composant existant JuryHallOfFame inchangé.
import Layout from '@/components/layout/Layout';
import JuryHallOfFame from '@/components/JuryHallOfFame';
import { useTranslation } from 'react-i18next';

export default function CrowdfundingJuryPage() {
  const { t } = useTranslation();
  return (
    <Layout>
      <div className="max-w-5xl mx-auto px-4 py-6" data-testid="cf-jury-page">
        <header className="mb-6">
          <h1 className="text-2xl sm:text-3xl font-bold" data-testid="cf-jury-page-title">
            ⚖️ {t('crowdfunding.jury_hall_title', { defaultValue: 'Hall of Fame des Jurés' })}
          </h1>
          <p className="text-sm mt-1" style={{ color: 'var(--jp-text-muted)' }}>
            {t('crowdfunding.jury_hall_intro', { defaultValue: 'Les gagnants des cycles passés deviennent membres du jury et leurs votes comptent davantage.' })}
          </p>
        </header>
        <JuryHallOfFame />
      </div>
    </Layout>
  );
}
