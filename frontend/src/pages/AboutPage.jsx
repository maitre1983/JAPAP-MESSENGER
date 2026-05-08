/**
 * iter237n — /about — À propos & Contact (page publique).
 *
 * Adresse complète + tous les emails officiels. Liens vers les pages
 * légales. Pas d'auth requise.
 */
import { Link } from 'react-router-dom';
import { COMPANY } from '@/legal/companyInfo';
import JapapFooter from '@/components/layout/JapapFooter';
import Seo from '@/components/Seo';
import { Envelope, MapPin, Globe, ShieldCheck, Scales, GameController, Headset, FileText } from '@phosphor-icons/react';

const EMAIL_LIST = [
  { key: 'contact', label: 'Contact général', icon: Envelope },
  { key: 'support', label: 'Support utilisateur', icon: Headset },
  { key: 'legal',   label: 'Affaires juridiques', icon: Scales },
  { key: 'privacy', label: 'Protection des données', icon: ShieldCheck },
  { key: 'dpo',     label: 'Délégué à la protection des données (DPO)', icon: ShieldCheck },
  { key: 'games',   label: 'Jeux payants — Modération & litiges', icon: GameController },
];

export default function AboutPage() {
  return (
    <div className="min-h-screen flex flex-col"
         style={{ background: 'var(--jp-bg, #F5F4FF)' }}
         data-testid="about-page">
      <Seo
        title={`À propos — ${COMPANY.name}`}
        description={`${COMPANY.name} — ${COMPANY.shortDescription}. Siège : ${COMPANY.address.line2}, ${COMPANY.address.line3}.`}
        url={`https://${COMPANY.domain}/about`}
        type="website"
      />
      <div className="max-w-3xl mx-auto w-full px-5 pt-10 pb-12 flex-1">
        <div className="mb-6">
          <Link to="/" className="text-xs font-bold underline opacity-70 hover:opacity-100"
                data-testid="about-back-home">← Retour à l'accueil</Link>
        </div>
        <header className="mb-8">
          <div className="text-[11px] uppercase tracking-widest font-bold opacity-60 mb-2">
            À propos
          </div>
          <h1 className="font-['Outfit'] text-3xl md:text-4xl font-extrabold mb-3"
              style={{ color: '#0F056B' }}>
            {COMPANY.name}
          </h1>
          <p className="text-sm leading-relaxed opacity-90">
            {COMPANY.shortDescription}.
          </p>
        </header>

        <section className="jp-card-elevated p-5 mb-5" data-testid="about-address-block">
          <h2 className="font-['Outfit'] text-lg font-bold flex items-center gap-2 mb-3"
              style={{ color: '#0F056B' }}>
            <MapPin size={18} weight="duotone" /> Siège social
          </h2>
          <address className="not-italic text-sm leading-relaxed">
            {COMPANY.address.line1}<br />
            {COMPANY.address.line2}<br />
            {COMPANY.address.line3}<br />
            <span className="inline-flex items-center gap-1 mt-2 opacity-80 text-xs">
              <Globe size={14} /> <a href={`https://${COMPANY.domain}`} className="underline">{COMPANY.domain}</a>
            </span>
          </address>
        </section>

        <section className="jp-card-elevated p-5 mb-5" data-testid="about-emails-block">
          <h2 className="font-['Outfit'] text-lg font-bold flex items-center gap-2 mb-3"
              style={{ color: '#0F056B' }}>
            <Envelope size={18} weight="duotone" /> Nous contacter
          </h2>
          <ul className="space-y-2">
            {EMAIL_LIST.map(({ key, label, icon: Icon }) => (
              <li key={key} className="flex items-start gap-3"
                  data-testid={`about-email-${key}`}>
                <Icon size={16} weight="duotone" className="mt-0.5 shrink-0"
                      style={{ color: '#0F056B' }} />
                <div className="flex-1 text-sm">
                  <div className="font-bold">{label}</div>
                  <a href={`mailto:${COMPANY.emails[key]}`}
                     className="opacity-80 hover:opacity-100 underline text-xs font-mono">
                    {COMPANY.emails[key]}
                  </a>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section className="jp-card-elevated p-5" data-testid="about-legal-links-block">
          <h2 className="font-['Outfit'] text-lg font-bold flex items-center gap-2 mb-3"
              style={{ color: '#0F056B' }}>
            <FileText size={18} weight="duotone" /> Documents légaux
          </h2>
          <ul className="space-y-2 text-sm">
            <li>
              <Link to="/legal/cgu" className="underline hover:opacity-80"
                    data-testid="about-link-cgu">
                Conditions Générales d'Utilisation
              </Link>
            </li>
            <li>
              <Link to="/legal/conditions-de-jeu" className="underline hover:opacity-80"
                    data-testid="about-link-cgj">
                Conditions Générales de Jeu (jeux payants)
              </Link>
            </li>
            <li>
              <Link to="/legal/confidentialite" className="underline hover:opacity-80"
                    data-testid="about-link-privacy">
                Politique de protection des données (RGPD)
              </Link>
            </li>
          </ul>
        </section>
      </div>
      <JapapFooter />
    </div>
  );
}
