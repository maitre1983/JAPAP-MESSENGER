import { useEffect, useState } from 'react';
import axios from 'axios';
import { ArrowsLeftRight } from '@phosphor-icons/react';
import * as WS from '../../utils/walletSecurity';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * iter159 — Live conversion preview for the deposit form.
 *
 * Props:
 *   amount (number | string): USD amount typed by the user
 *   method (string): one of the deposit methods (hubtel_card, mobile_money,
 *                    nowpayments_usdttrc20, etc.)
 *
 * Debounced 400 ms — fetches /api/wallet/deposit/conversion-preview and
 * shows "≈ 155 GHS (1 USD = 15.5 GHS)" under the amount field so the
 * user sees exactly what the provider will debit before clicking pay.
 */
export default function DepositConversionPreview({ amount, method }) {
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const n = parseFloat(amount);
    if (!n || n <= 0 || !method) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    const handle = setTimeout(async () => {
      setLoading(true);
      try {
        const { data } = await axios.get(
          `${API}/api/wallet/deposit/conversion-preview`,
          {
            params: { amount: n, method },
            withCredentials: true,
          },
        );
        if (!WS.validateConversion(data, n)) {
          if (!cancelled) setPreview(null);
          return;
        }
        if (!cancelled) setPreview(data);
      } catch {
        if (!cancelled) setPreview(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 400);
    return () => { cancelled = true; clearTimeout(handle); };
  }, [amount, method]);

  if (!preview && !loading) return null;

  return (
    <div
      data-testid="deposit-conversion-preview"
      className="mt-2 px-3 py-2 rounded-lg text-xs font-['Manrope'] flex items-start gap-2"
      style={{
        background: 'rgba(15,5,107,0.06)',
        color: 'var(--jp-text)',
        border: '1px solid rgba(15,5,107,0.1)',
      }}
    >
      <ArrowsLeftRight size={14} weight="bold" style={{ marginTop: 2, opacity: 0.7 }} />
      {loading && !preview && (
        <span style={{ opacity: 0.6 }}>Calcul du taux…</span>
      )}
      {preview && (
        <div className="flex-1">
          {/* canonical + provider amount line */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-bold">{parseFloat(preview.amount_usd).toLocaleString('fr-FR', { minimumFractionDigits: 2 })} USD</span>
            <span style={{ opacity: 0.5 }}>≈</span>
            <span className="font-bold" style={{ color: 'var(--jp-primary)' }}>
              {parseFloat(preview.provider_amount).toLocaleString('fr-FR', { minimumFractionDigits: 2 })} {preview.provider_currency}
            </span>
            {preview.exchange_rate && preview.provider_currency !== 'USD' && preview.provider_currency !== 'USDT' && (
              <span style={{ opacity: 0.55, fontSize: 11 }}>
                · 1 USD = {parseFloat(preview.exchange_rate).toLocaleString('fr-FR', { maximumFractionDigits: 4 })} {preview.provider_currency}
              </span>
            )}
          </div>
          <div style={{ opacity: 0.7, fontSize: 11, marginTop: 2 }}>
            {preview.display_note}
          </div>
        </div>
      )}
    </div>
  );
}
