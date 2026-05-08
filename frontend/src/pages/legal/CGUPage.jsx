/**
 * iter237n — /legal/cgu — Conditions Générales d'Utilisation.
 * Contenu : voir /app/frontend/src/legal/cguContent.js (généré).
 */
import LegalPageLayout from '@/components/legal/LegalPageLayout';
import cguContent from '@/legal/cguContent';

export default function CGUPage() {
  return (
    <LegalPageLayout
      title={cguContent.title}
      intro={cguContent.intro}
      sections={cguContent.sections}
      testId="legal-cgu-page"
      seoPath="/legal/cgu"
    />
  );
}
