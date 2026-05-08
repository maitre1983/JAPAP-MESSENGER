/**
 * SmartUpsell — Triggers a Pro conversion modal after N blocked actions
 * =====================================================================
 * Any component can call `trackProBlock('feature_name')` to signal that a
 * user just hit a Pro-gated wall. The hook stores counts in localStorage,
 * and when the admin-configured threshold is reached (default 3), a modal
 * pops up inviting them to upgrade with a direct CTA to /pro.
 */
import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';
import { Crown, Sparkle, X } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;
const STORAGE_KEY = 'jp_pro_blocks';
const DISMISSED_UNTIL_KEY = 'jp_pro_upsell_dismissed_until';

const Ctx = createContext({ trackProBlock: () => {} });

export const SmartUpsellProvider = ({ children }) => {
  const { t } = useTranslation();
  const [config, setConfig] = useState({ enabled: false, threshold: 3, ab_variant: 'A' });
  const [open, setOpen] = useState(false);
  const [trigger, setTrigger] = useState('');

  useEffect(() => {
    axios.get(`${API}/api/pro/upsell-config`, { withCredentials: true })
      .then(({ data }) => setConfig(data))
      .catch(() => {});
  }, []);

  const trackProBlock = useCallback((featureName = 'generic') => {
    if (!config.enabled) return;
    const until = parseInt(localStorage.getItem(DISMISSED_UNTIL_KEY) || '0', 10);
    if (until && Date.now() < until) return; // snoozed

    const raw = localStorage.getItem(STORAGE_KEY);
    const data = raw ? JSON.parse(raw) : { count: 0, features: {} };
    data.count = (data.count || 0) + 1;
    data.features[featureName] = (data.features[featureName] || 0) + 1;
    data.last_at = Date.now();
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));

    if (data.count >= (config.threshold || 3)) {
      setTrigger(featureName);
      setOpen(true);
      // reset so we don't spam
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ count: 0, features: {}, last_at: Date.now() }));
    }
  }, [config]);

  const snooze = (hours) => {
    localStorage.setItem(DISMISSED_UNTIL_KEY, String(Date.now() + hours * 3600000));
    setOpen(false);
  };

  return (
    <Ctx.Provider value={{ trackProBlock }}>
      {children}
      {open && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center p-4"
          style={{ background: 'rgba(0,0,0,0.7)' }} data-testid="smart-upsell-modal">
          <div className="jp-card-elevated max-w-md w-full p-0 jp-animate-scaleIn overflow-hidden">
            <div className="relative p-6" style={{
              background: 'linear-gradient(135deg, #F7931A 0%, #EC4899 55%, #8B5CF6 100%)',
              color: 'white',
            }}>
              <button onClick={() => snooze(24)} className="absolute top-3 right-3 opacity-80 hover:opacity-100"
                data-testid="upsell-close">
                <X size={18} weight="bold" />
              </button>
              <div className="text-4xl mb-2">👋</div>
              <h2 className="font-['Outfit'] text-2xl font-black">Vous adorez JAPAP, non ?</h2>
              <p className="text-sm opacity-90 mt-2">
                Vous avez essayé plusieurs fonctionnalités Pro ces derniers jours. <strong>{t('smart_upsell_context.passez_pro')}</strong>
                {trigger && trigger !== 'generic' && <> — notamment pour débloquer <code>{trigger}</code></>}
                {config.ab_variant === 'B' && <> et profitez de <strong>-20% à vie</strong></>}.
              </p>
            </div>
            <div className="p-6 space-y-3">
              <div className="flex items-start gap-3">
                <Sparkle size={16} weight="fill" style={{ color: 'var(--jp-primary)' }} className="mt-0.5 shrink-0" />
                <div className="text-sm" style={{ color: 'var(--jp-text-secondary)' }}>
                  Accès illimité WiFi JAPAP Connect, boost feed &amp; reels, analytics, 0% commission sur tips…
                </div>
              </div>
              <div className="flex flex-col gap-2">
                <Link to="/pro" onClick={() => setOpen(false)}
                  className="jp-btn jp-btn-primary jp-btn-lg jp-btn-full" data-testid="upsell-cta">
                  <Crown size={16} weight="fill" /> Voir les plans Pro
                </Link>
                <button onClick={() => snooze(72)} className="text-xs underline text-center"
                  style={{ color: 'var(--jp-text-muted)' }} data-testid="upsell-snooze">
                  Plus tard (me rappeler dans 3 jours)
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </Ctx.Provider>
  );
};

export const useSmartUpsell = () => useContext(Ctx);
