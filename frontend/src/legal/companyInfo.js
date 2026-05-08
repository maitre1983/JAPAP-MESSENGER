/**
 * iter237n — Contenu des documents légaux (CGU + CGJ + RGPD).
 *
 * Le texte est figé (version 1.0 — 7 mai 2026). Si une nouvelle version
 * est publiée, créer `vNNN_AAAA-MM-JJ` constants ici et faire un
 * release-note dans le PRD. Le tracking (`users.cgu_accepted_at` etc.)
 * conserve l'horodatage exact de l'acceptation par chaque utilisateur,
 * permettant de prouver le consentement.
 */
export const LEGAL_VERSION = '1.0';
export const LEGAL_DATE = '7 mai 2026';

export const COMPANY = {
  name: 'JAPAP TECHNOLOGIES PLC',
  domain: 'japapmessenger.com',
  shortDescription: "Plateforme africaine de messagerie sociale, e-commerce et jeux d'agilité",
  address: {
    line1: 'JAPAP TECHNOLOGIES PLC',
    line2: 'Bole Sub-City, Woreda 03',
    line3: 'Addis Ababa, Ethiopia',
  },
  emails: {
    contact:  'contact@japapmessenger.com',
    support:  'support@japapmessenger.com',
    legal:    'legal@japapmessenger.com',
    privacy:  'privacy@japapmessenger.com',
    dpo:      'dpo@japapmessenger.com',
    games:    'jeux@japapmessenger.com',
  },
};
