/**
 * iter237n — /legal/conditions-de-jeu — Conditions Générales de Jeu.
 * Pré-requis pour participer aux jeux payants. Acceptation tracée en base
 * via users.cgje_accepted_at.
 */
import LegalPageLayout from '@/components/legal/LegalPageLayout';
import cgjContent from '@/legal/cgjContent';

export default function CGJPage() {
  return (
    <LegalPageLayout
      title={cgjContent.title}
      intro={cgjContent.intro}
      sections={cgjContent.sections}
      testId="legal-cgj-page"
      seoPath="/legal/conditions-de-jeu"
    />
  );
}
