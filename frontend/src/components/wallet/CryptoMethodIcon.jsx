/**
 * CryptoMethodIcon — iter 82
 * Renders a payment method's icon :
 *  - If `iconUrl` is provided (crypto token logo), show it with an optional
 *    chain badge (e.g. TRON, BSC) overlaid on the bottom-right corner.
 *  - Otherwise fall back to the plain character icon (e.g. 💳 for Hubtel).
 *
 * Keeps the wallet UI pixel-aligned regardless of icon source.
 */
import * as WS from '../../utils/walletSecurity';

export default function CryptoMethodIcon({
  iconUrl, chainIconUrl, icon, alt = '', size = 28,
}) {
  if (!iconUrl || !WS.isSafeUrl(iconUrl)) {
    return <span className="text-lg" aria-hidden="true">{icon || ''}</span>;
  }
  const badge = Math.round(size * 0.55);
  return (
    <span
      className="relative inline-block align-middle"
      style={{ width: size, height: size }}
      data-testid="crypto-method-icon">
      <img
        src={iconUrl}
        alt={alt}
        width={size}
        height={size}
        className="rounded-full"
        style={{ display: 'block' }}
      />
      {chainIconUrl && WS.isSafeUrl(chainIconUrl) && (
        <img
          src={chainIconUrl}
          alt=""
          width={badge}
          height={badge}
          className="absolute rounded-full"
          style={{
            right: -2,
            bottom: -2,
            background: 'var(--jp-surface, #fff)',
            padding: 1,
            boxShadow: '0 0 0 1.5px var(--jp-surface, #fff)',
          }}
        />
      )}
    </span>
  );
}
