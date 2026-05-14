// iter240g — Composant universel d'affichage monétaire.
// Affiche un montant USD avec son équivalent dans la devise locale détectée
// (par géolocalisation IP + override préférences). Respecte le toggle
// utilisateur "Afficher l'équivalent local" stocké dans CurrencyContext.
//
// Usage :
//   <MoneyDisplay amountUsd={1} />
//     → "$1.00 (≈ 615 FCFA)" pour un utilisateur au Cameroun (XAF)
//     → "$1.00" pour un utilisateur aux États-Unis (USD)
//
//   <MoneyDisplay amountUsd={1} short />   // → "$1.00" (force court)
//
// Si une valeur n'est pas en USD côté backend (ex: ancien champ XAF), passer
// `legacyCurrency='XAF'` pour qu'on la convertisse d'abord en USD via
// rates[ legacyCurrency ] avant de re-projeter en local.
import { useCurrency, formatMoney } from '@/utils/currency';

export default function MoneyDisplay({
  amountUsd,
  amount,            // alias rétro-compatible
  legacyCurrency,    // ex: 'XAF' si l'amount n'est pas USD
  short = false,
  className = '',
  'data-testid': testId,
}) {
  const ctx = useCurrency();
  let usd = Number(amountUsd ?? amount ?? 0);
  if (legacyCurrency && legacyCurrency.toUpperCase() !== 'USD') {
    const rate = ctx?.rates?.[legacyCurrency.toUpperCase()];
    if (rate && rate > 0) usd = usd / rate;
  }
  const text = formatMoney(usd, ctx, {
    usdFirst: true,
    short,
    showLocalEquivalent: ctx?.showEquivalent !== false && ctx?.local !== 'USD',
  });
  return (
    <span className={className} data-testid={testId}>
      {text}
    </span>
  );
}
