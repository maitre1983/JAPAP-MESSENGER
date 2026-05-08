/**
 * iter237n — /legal/confidentialite — Politique de protection des données (RGPD).
 */
import LegalPageLayout from '@/components/legal/LegalPageLayout';
import rgpdContent from '@/legal/rgpdContent';

export default function PrivacyPage() {
  return (
    <LegalPageLayout
      title={rgpdContent.title}
      intro={rgpdContent.intro}
      sections={rgpdContent.sections}
      testId="legal-privacy-page"
      seoPath="/legal/confidentialite"
    />
  );
}
