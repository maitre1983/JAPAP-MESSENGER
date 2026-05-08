/**
 * iter237n — Footer global JAPAP.
 *
 * Affiché sur toutes les pages (auth + non-auth) via App.js.
 * Liens légaux (CGU / CGJ / Confidentialité / Contact), siège social
 * et copyright. Strictement informatif — aucun appel API.
 */
import { Link } from 'react-router-dom';
import { COMPANY, LEGAL_DATE } from '@/legal/companyInfo';

export default function JapapFooter() {
  const year = new Date().getFullYear();
  return (
    <footer
      data-testid="japap-global-footer"
      className="w-full mt-8 px-4 py-6 text-xs"
      style={{
        background: 'var(--jp-surface, #0F056B)',
        color: 'rgba(255,255,255,0.85)',
        borderTop: '1px solid rgba(255,255,255,0.08)',
      }}
    >
      <div className="max-w-5xl mx-auto flex flex-col md:flex-row gap-4 items-start md:items-center md:justify-between">
        <div className="leading-relaxed">
          <div className="font-['Outfit'] font-extrabold text-sm mb-1">
            {COMPANY.name}
          </div>
          <div className="opacity-80">
            {COMPANY.address.line2}<br />
            {COMPANY.address.line3}<br />
            <a href={`mailto:${COMPANY.emails.contact}`}
               className="underline hover:opacity-100 opacity-90"
               data-testid="footer-email-contact">
              {COMPANY.emails.contact}
            </a>
          </div>
        </div>
        <nav className="flex flex-wrap gap-x-4 gap-y-2 font-['Manrope']" data-testid="footer-legal-links">
          <Link to="/legal/cgu" className="hover:underline" data-testid="footer-link-cgu">
            CGU
          </Link>
          <Link to="/legal/conditions-de-jeu" className="hover:underline" data-testid="footer-link-cgje">
            Conditions de jeu
          </Link>
          <Link to="/legal/confidentialite" className="hover:underline" data-testid="footer-link-privacy">
            Confidentialité
          </Link>
          <Link to="/about" className="hover:underline" data-testid="footer-link-about">
            À propos / Contact
          </Link>
        </nav>
      </div>
      <div className="max-w-5xl mx-auto mt-4 pt-3 text-[10px] opacity-60 leading-relaxed"
           style={{ borderTop: '1px solid rgba(255,255,255,0.08)' }}>
        © {year} {COMPANY.name}. Tous droits réservés.
        Documents légaux mis à jour le {LEGAL_DATE}.
      </div>
    </footer>
  );
}
