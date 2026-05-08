/**
 * iter237n — Page légale générique (CGU / CGJ / RGPD).
 *
 * Composant réutilisé pour les 3 documents. Reçoit `title` + `version` +
 * `sections[]` et rend une mise en forme propre, lisible, accessible.
 * Pas d'appel API — contenu statique versionné dans /legal/*Pages.
 */
import { Link } from 'react-router-dom';
import JapapFooter from '@/components/layout/JapapFooter';
import Seo from '@/components/Seo';
import { COMPANY, LEGAL_DATE, LEGAL_VERSION } from '@/legal/companyInfo';

export default function LegalPageLayout({ title, intro, sections, testId, seoPath }) {
  // iter237w — Pump per-page <title>, description, og:* via the existing
  // Seo helper. Each legal page becomes individually crawlable with
  // canonical URL derived from `seoPath` (e.g. "/legal/cgu").
  const seoDescription = (intro || `${title} de ${COMPANY.name}.`).slice(0, 160);
  return (
    <div className="min-h-screen flex flex-col"
         style={{ background: 'var(--jp-bg, #F5F4FF)' }}
         data-testid={testId}>
      <Seo
        title={`${title} — ${COMPANY.name}`}
        description={seoDescription}
        url={seoPath ? `https://${COMPANY.domain}${seoPath}` : undefined}
        type="article"
      />
      <div className="max-w-3xl mx-auto w-full px-5 pt-10 pb-12 flex-1">
        <div className="mb-6">
          <Link to="/" className="text-xs font-bold underline opacity-70 hover:opacity-100"
                data-testid="legal-back-home">← Retour à l'accueil</Link>
        </div>
        <header className="mb-8">
          <div className="text-[11px] uppercase tracking-widest font-bold opacity-60 mb-2">
            {COMPANY.name} — {COMPANY.domain}
          </div>
          <h1 className="font-['Outfit'] text-3xl md:text-4xl font-extrabold mb-2"
              style={{ color: '#0F056B' }}>
            {title}
          </h1>
          <p className="text-xs opacity-70">
            Version {LEGAL_VERSION} · Mis à jour le {LEGAL_DATE}
          </p>
          {intro && (
            <p className="mt-4 text-sm leading-relaxed" style={{ color: 'var(--jp-text)' }}>
              {intro}
            </p>
          )}
        </header>
        <article className="space-y-6 text-sm leading-relaxed"
                 style={{ color: 'var(--jp-text)' }}>
          {sections.map((s, i) => (
            <section key={i} data-testid={`legal-section-${i}`}>
              {s.heading && (
                <h2 className="font-['Outfit'] text-lg md:text-xl font-bold mb-2"
                    style={{ color: '#0F056B' }}>
                  {s.heading}
                </h2>
              )}
              {(s.paragraphs || []).map((p, j) => (
                <p key={j} className="mb-3 whitespace-pre-line">{p}</p>
              ))}
              {s.list && (
                <ul className="list-disc list-inside space-y-1 ml-2">
                  {s.list.map((it, k) => <li key={k}>{it}</li>)}
                </ul>
              )}
            </section>
          ))}
        </article>
      </div>
      <JapapFooter />
    </div>
  );
}
