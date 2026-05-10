/**
 * iter238 — Paystack admin settings card (STRICTLY ADDITIVE).
 *
 * Self-contained component:
 *   • Reads + writes its OWN settings via /api/admin/settings (PUT bulk)
 *   • Does NOT interfere with the existing PaymentSettings save button
 *   • Includes the spec-required toggles:
 *       paystack_enabled / hubtel_card_enabled / nowpayments_enabled
 *   • Credentials masked by default (toggle visibility)
 *   • Live FX rate displayed read-only (from /api/paystack/limits)
 */
import { useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { CreditCard, Eye, EyeSlash, ArrowsClockwise } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

const PAYSTACK_KEYS = [
  'paystack_secret_key',
  'paystack_public_key',
  'paystack_enabled',
  'paystack_deposit_min',
  'paystack_deposit_max',
  'paystack_usd_ghs_rate',
  'paystack_usd_ghs_fallback_rate',
  // iter239a3 — Global USD→GHS rate (overrides ALL method-specific rates).
  'usd_ghs_rate',
  'usd_ghs_fallback_rate',
  // External toggles (controlled here too).
  'hubtel_card_enabled',
  'nowpayments_enabled',
];

const MASK_PREFIX = '••••••••';

function isMaskedValue(v) {
  return typeof v === 'string' && v.startsWith(MASK_PREFIX);
}

export default function PaystackSettingsCard() {
  const [values, setValues] = useState({});
  const [configured, setConfigured] = useState({});
  const [showSecret, setShowSecret] = useState(false);
  const [editingSecret, setEditingSecret] = useState(false);
  const [loading, setLoading] = useState(false);
  const [fx, setFx] = useState(null);

  const load = async () => {
    try {
      const { data } = await axios.get(`${API}/api/admin/settings`,
        { withCredentials: true });
      const out = {};
      for (const k of PAYSTACK_KEYS) {
        if (data.settings && k in data.settings) out[k] = data.settings[k];
      }
      setValues(out);
      setConfigured(data.secret_configured || {});
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to load settings');
    }
  };

  const loadFx = async () => {
    try {
      const { data } = await axios.get(`${API}/api/paystack/limits`,
        { withCredentials: true });
      setFx(data?.fx || null);
    } catch (_) { setFx(null); }
  };

  useEffect(() => { load(); loadFx(); }, []);

  const setKey = (k, v) => setValues(prev => ({ ...prev, [k]: v }));

  const save = async () => {
    setLoading(true);
    try {
      // Build the bulk payload. Drop masked echoes — backend also filters.
      const payload = {};
      for (const k of PAYSTACK_KEYS) {
        const v = values[k];
        if (v === undefined) continue;
        if (k === 'paystack_secret_key' && isMaskedValue(v)) continue;
        // Coerce explicit booleans for toggles.
        if (k.endsWith('_enabled')) {
          payload[k] = (v === true || v === 'true') ? 'true' : 'false';
        } else {
          payload[k] = v;
        }
      }
      await axios.put(`${API}/api/admin/settings`,
        { settings: payload }, { withCredentials: true });
      toast.success('Paystack settings saved');
      setEditingSecret(false);
      await load();
      await loadFx();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to save settings');
    } finally { setLoading(false); }
  };

  const Toggle = ({ k, label, hint }) => {
    const on = values[k] === 'true' || values[k] === true;
    return (
      <div className="flex items-start justify-between gap-3 py-2.5">
        <div className="flex-1">
          <div className="text-sm font-['Manrope'] font-semibold"
               style={{ color: 'var(--jp-text)' }}>{label}</div>
          {hint && <div className="text-[11px] mt-0.5"
                        style={{ color: 'var(--jp-text-muted)' }}>{hint}</div>}
        </div>
        <button type="button"
          onClick={() => setKey(k, on ? 'false' : 'true')}
          className="relative w-12 h-6 rounded-full transition-colors shrink-0 mt-0.5"
          data-testid={`paystack-toggle-${k}`}
          style={{ background: on ? 'var(--jp-success)' : '#D1D5DB' }}>
          <div className="absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform"
            style={{ transform: on ? 'translateX(24px)' : 'translateX(2px)' }} />
        </button>
      </div>
    );
  };

  const TextField = ({ k, label, placeholder, hint, type = 'text' }) => (
    <div className="py-2">
      <label className="jp-label">{label}</label>
      <input type={type}
        value={values[k] || ''}
        onChange={e => setKey(k, e.target.value)}
        placeholder={placeholder}
        className="jp-input text-sm"
        data-testid={`paystack-field-${k}`} />
      {hint && <div className="text-[10px] mt-1"
                    style={{ color: 'var(--jp-text-muted)' }}>{hint}</div>}
    </div>
  );

  const secretValue = values.paystack_secret_key || '';
  const secretIsMasked = isMaskedValue(secretValue) && !editingSecret;

  return (
    <div className="jp-card-elevated p-5"
         data-testid="paystack-settings-card">
      <h3 className="font-['Outfit'] text-lg font-bold mb-1 flex items-center gap-2">
        <CreditCard size={18} weight="duotone" style={{ color: '#0FA958' }} />
        Configuration Paystack 🇬🇭
      </h3>
      <p className="text-xs mb-3" style={{ color: 'var(--jp-text-muted)' }}>
        Dépôts via carte bancaire & Mobile Money Ghana en USD (conversion live USD→GHS).
        Webhook : <code>https://japapmessenger.com/api/paystack/webhook</code>
      </p>

      {/* Credentials */}
      <div>
        <div className="py-2">
          <label className="jp-label flex items-center gap-2">
            Secret key
            <button type="button"
              onClick={() => setShowSecret(s => !s)}
              className="jp-btn jp-btn-ghost jp-btn-sm text-xs"
              data-testid="paystack-secret-toggle">
              {showSecret ? <EyeSlash size={12} /> : <Eye size={12} />}
              {showSecret ? ' Masquer' : ' Afficher'}
            </button>
          </label>
          <div className="flex gap-1.5">
            <input
              type={showSecret ? 'text' : 'password'}
              value={secretValue}
              readOnly={secretIsMasked}
              onChange={e => setKey('paystack_secret_key', e.target.value)}
              placeholder="sk_live_..."
              className="jp-input text-xs font-mono flex-1"
              data-testid="paystack-secret-input" />
            {secretIsMasked && (
              <button type="button"
                onClick={() => { setEditingSecret(true); setKey('paystack_secret_key', ''); }}
                className="jp-btn jp-btn-ghost jp-btn-sm text-xs shrink-0"
                data-testid="paystack-secret-edit">
                Modifier
              </button>
            )}
          </div>
          <div className="text-[10px] mt-1" style={{ color: 'var(--jp-text-muted)' }}>
            Récupérable sur dashboard.paystack.com → Settings → API Keys
          </div>
        </div>
        <TextField k="paystack_public_key" label="Public key"
          placeholder="pk_live_..."
          hint="Affichable côté frontend (non sensible)." />
      </div>

      <div className="h-px my-3" style={{ background: 'var(--jp-border)' }} />

      {/* Toggles */}
      <div className="divide-y" style={{ borderColor: 'var(--jp-border)' }}>
        <Toggle k="paystack_enabled"
          label="Paystack — activé"
          hint="Si désactivé, /api/paystack/* renvoie 403 method_disabled." />
        <Toggle k="hubtel_card_enabled"
          label="Carte Hubtel (legacy)"
          hint="Désactivé : masqué du frontend + endpoints /api/payments/hubtel/* → 403." />
        <Toggle k="nowpayments_enabled"
          label="NowPayments (crypto)"
          hint="Désactivé : masqué du frontend + endpoints /api/wallet/nowpayments/* → 403. Les webhooks restent acceptés." />
      </div>

      <div className="h-px my-3" style={{ background: 'var(--jp-border)' }} />

      {/* Limits */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <TextField k="paystack_deposit_min" label="Dépôt minimum (USD)"
          placeholder="1.00" type="number" />
        <TextField k="paystack_deposit_max" label="Dépôt maximum (USD)"
          placeholder="5000.00" type="number" />
      </div>

      <div className="h-px my-3" style={{ background: 'var(--jp-border)' }} />

      {/* iter239a3 — Global USD→GHS rate (overrides ALL Paystack/Hubtel rates). */}
      <div className="rounded-xl p-3 mb-3"
           style={{ background: 'rgba(15,5,107,0.06)', border: '1px solid var(--jp-primary)' }}>
        <div className="text-xs font-bold mb-2 flex items-center gap-2"
             style={{ color: 'var(--jp-primary)' }}>
          🌐 Taux de change global (USD → GHS)
        </div>
        <p className="text-[10px] mb-2" style={{ color: 'var(--jp-text-secondary)' }}>
          Si renseigné, ce taux est utilisé par <strong>toutes</strong> les méthodes GHS
          (Paystack, Hubtel MoMo) — priorité absolue, ignore les taux spécifiques par méthode.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <TextField k="usd_ghs_rate"
            label="Taux global USD→GHS"
            placeholder="Laissez vide pour utiliser les taux spécifiques / live"
            hint="Affecte Paystack ET Hubtel MoMo immédiatement." />
          <TextField k="usd_ghs_fallback_rate"
            label="Fallback global USD→GHS"
            placeholder="Ex: 13.50"
            hint="Si l'API live échoue et qu'aucun taux manuel n'est défini." />
        </div>
      </div>

      <div className="h-px my-3" style={{ background: 'var(--jp-border)' }} />

      {/* FX */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <TextField k="paystack_usd_ghs_rate"
          label="Taux Paystack (legacy, optionnel)"
          placeholder="Laissez vide — préférer le taux global"
          hint="Conservé pour rétro-compat. Le taux global le prend en priorité." />
        <TextField k="paystack_usd_ghs_fallback_rate"
          label="Fallback Paystack (legacy)"
          placeholder="Vide → utilise fallback global"
          hint="Conservé pour rétro-compat." />
      </div>
      <div className="mt-2 p-3 rounded-xl text-xs flex items-center gap-2"
           style={{ background: 'rgba(15,5,107,0.04)', color: 'var(--jp-text-secondary)' }}
           data-testid="paystack-fx-live">
        <ArrowsClockwise size={14} />
        Taux live actuel :
        {fx?.rate
          ? <strong className="ml-1">1 USD = {Number(fx.rate).toFixed(4)} GHS</strong>
          : <span className="ml-1 opacity-60">— indisponible</span>}
        {fx?.source && <span className="ml-2 opacity-60">({fx.source})</span>}
        <button type="button"
          onClick={loadFx}
          className="jp-btn jp-btn-ghost jp-btn-sm text-xs ml-auto"
          data-testid="paystack-fx-refresh">
          Actualiser
        </button>
      </div>

      <button disabled={loading} onClick={save}
        className="jp-btn jp-btn-primary mt-4"
        data-testid="paystack-save-btn">
        {loading ? 'Enregistrement…' : 'Enregistrer la configuration Paystack'}
      </button>
    </div>
  );
}
