/**
 * JAPAP — Display Currency Selector (iter162)
 *
 * Sits on the Profile page and lets the user pick the currency used to
 * render their wallet balance. The internal/canonical balance is ALWAYS
 * stored in USD (iter158). This selector only affects how the number is
 * displayed across the app.
 *
 * Options:
 *   • "Local (auto)" — server resolves from user.country → IP geoloc.
 *   • "USD"         — canonical. Always available.
 *   • 12 handpicked ISO4217 codes covering JAPAP's core markets + diaspora.
 *
 * Wired to:
 *   POST /api/wallet/display-currency  { display_currency: 'local' | 'USD' | 'XAF' | ... }
 *   GET  /api/wallet/balance            → refreshed after each change
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { CurrencyDollar, Check } from "@phosphor-icons/react";
import { useTranslation } from 'react-i18next';
const API = process.env.REACT_APP_BACKEND_URL;

// Ordered by JAPAP market priority:
//   1. Canonical (USD)
//   2. African markets where JAPAP operates (XAF, XOF, GHS, NGN, KES…)
//   3. Diaspora / trading pairs (EUR, GBP, CAD, ZAR, MAD, EGP)
const getCurrency_options = (t) => ([
  { code: "USD", label: t('display_currency_selector.dollar_americain'),    flag: "🇺🇸", symbol: "$" },
  { code: "XAF", label: "Franc CFA BEAC",      flag: "🌍", symbol: "FCFA", region: t('display_currency_selector.cameroun_gabon_congo_tchad_rca_guin') },
  { code: "XOF", label: "Franc CFA BCEAO",     flag: "🌍", symbol: "CFA",  region: t('display_currency_selector.senegal_cote_d_ivoire_mali_burkina') },
  { code: "GHS", label: t('display_currency_selector.cedi_ghaneen'),         flag: "🇬🇭", symbol: "GH₵" },
  { code: "NGN", label: t('display_currency_selector.naira_nigerian'),       flag: "🇳🇬", symbol: "₦"  },
  { code: "KES", label: t('display_currency_selector.shilling_kenyan'),      flag: "🇰🇪", symbol: "KSh" },
  { code: "UGX", label: "Shilling ougandais",   flag: "🇺🇬", symbol: "USh" },
  { code: "TZS", label: "Shilling tanzanien",   flag: "🇹🇿", symbol: "TSh" },
  { code: "ZAR", label: "Rand sud-africain",    flag: "🇿🇦", symbol: "R"  },
  { code: "MAD", label: "Dirham marocain",      flag: "🇲🇦", symbol: "DH" },
  { code: "EGP", label: t('display_currency_selector.livre_egyptienne'),     flag: "🇪🇬", symbol: "E£" },
  { code: "EUR", label: "Euro",                 flag: "🇪🇺", symbol: "€"  },
  { code: "GBP", label: "Livre sterling",       flag: "🇬🇧", symbol: "£"  },
  { code: "CAD", label: "Dollar canadien",      flag: "🇨🇦", symbol: "C$" },
]);

export default function DisplayCurrencySelector() {
  const { t } = useTranslation();
  const CURRENCY_OPTIONS = getCurrency_options(t);
  const [selected, setSelected] = useState("local"); // 'local' | 'USD' | 'XAF' | …
  const [resolvedCcy, setResolvedCcy] = useState("USD"); // what the API says currently
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);

  // Load current balance to know the active display currency
  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const { data } = await axios.get(`${API}/api/wallet/balance`, { withCredentials: true });
        if (cancel) return;
        setResolvedCcy(data.display_currency || "USD");
        // We don't know from the API alone whether the user picked "Local"
        // explicitly or an explicit currency. We read the user row instead.
        const { data: me } = await axios.get(`${API}/api/auth/me`, { withCredentials: true });
        if (cancel) return;
        const pref = (me?.display_currency || "").toUpperCase();
        setSelected(pref || "local");
      } catch (_) {
        /* silent — fallback defaults stay */
      } finally {
        if (!cancel) setLoading(false);
      }
    })();
    return () => { cancel = true; };
  }, []);

  const handleChange = async (e) => {
    const code = e.target.value;
    setSelected(code);
    setSaving(true);
    try {
      await axios.post(
        `${API}/api/wallet/display-currency`,
        { display_currency: code === "local" ? "local" : code },
        { withCredentials: true },
      );
      // Refresh the balance to confirm the resolved currency.
      const { data } = await axios.get(`${API}/api/wallet/balance`, { withCredentials: true });
      setResolvedCcy(data.display_currency || "USD");
      if (code === "local") {
        if (data.display_currency && data.display_currency !== "USD") {
          toast.success(`Devise locale détectée : ${data.display_currency}`);
        } else {
          toast.info("Devise locale indisponible — affichage en USD.");
        }
      } else {
        const opt = CURRENCY_OPTIONS.find((o) => o.code === code);
        toast.success(`Affichage en ${opt ? opt.label : code}`);
      }
    } catch (err) {
      toast.error("Impossible de changer la devise d'affichage.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      data-testid="display-currency-selector"
      className="p-4 rounded-xl"
      style={{
        background: "var(--jp-surface-secondary)",
        border: "1px solid var(--jp-border)",
      }}
    >
      <div className="flex items-center gap-3 mb-3">
        <CurrencyDollar size={22} weight="duotone" style={{ color: "var(--jp-primary)" }} />
        <div className="flex-1">
          <p className="text-sm font-semibold font-['Manrope']" style={{ color: "var(--jp-text)" }}>
            Devise d'affichage
          </p>
          <p className="text-[11px] font-['Manrope']" style={{ color: "var(--jp-text-muted)" }}>
            Ton solde reste toujours en USD. Choisis la devise utilisée pour l'affichage.
          </p>
        </div>
      </div>

      <div className="relative">
        <select
          data-testid="display-currency-select"
          value={selected}
          onChange={handleChange}
          disabled={loading || saving}
          className="w-full appearance-none cursor-pointer rounded-lg px-3 py-2.5 pr-9 text-sm font-['Manrope'] font-medium focus:outline-none focus:ring-2"
          style={{
            background: "var(--jp-surface)",
            color: "var(--jp-text)",
            border: "1px solid var(--jp-border)",
          }}
        >
          <option value="local">🌐 Local (auto)</option>
          <option disabled>──────────</option>
          {CURRENCY_OPTIONS.map((o) => (
            <option key={o.code} value={o.code}>
              {o.flag} {o.code} — {o.label}
            </option>
          ))}
        </select>
        {saving && (
          <span
            className="absolute right-3 top-1/2 -translate-y-1/2 text-[11px] font-['Manrope']"
            style={{ color: "var(--jp-text-muted)" }}
          >
            …
          </span>
        )}
      </div>

      <div className="flex items-center gap-1.5 mt-2">
        <Check size={12} weight="bold" style={{ color: "var(--jp-success, #16a34a)" }} />
        <p className="text-[11px] font-['Manrope']" style={{ color: "var(--jp-text-muted)" }}>
          Devise active : <strong style={{ color: "var(--jp-text)" }}>{resolvedCcy}</strong>
          {selected === "local" && resolvedCcy !== "USD" && (
            <span> (détectée automatiquement)</span>
          )}
        </p>
      </div>
    </div>
  );
}
