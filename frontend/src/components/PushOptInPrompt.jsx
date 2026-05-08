/**
 * iter140 — Contextualized OneSignal opt-in prompt shown ONCE after the
 * user's first authenticated session. Replaces the dry generic browser
 * permission popup with a value-driven JAPAP-themed modal.
 *
 * Strategy:
 *   • Wait until auth is settled and user is logged in.
 *   • Skip if push is unsupported, already granted, already denied, or
 *     the user has already seen / dismissed the prompt (localStorage).
 *   • Display a benefit-driven modal explaining WHY they should subscribe,
 *     with a primary CTA "Activer" → calls subscribePush() (which opens
 *     the browser permission dialog in a context the user already opted-in).
 *   • Track dismissals so we never spam (a user who clicked "Plus tard"
 *     gets re-prompted at most once per 14 days).
 *
 * Mounted once at app shell level (App.js).
 */
import { useEffect, useState } from 'react';
import { useAuth } from '@/context/AuthContext';
import { isPushSupported, currentPermission, subscribePush } from '@/services/webpush';
import { Bell, BellRinging, X, Sparkle, Crown, Wallet, ChatCircleDots, Lightning } from '@phosphor-icons/react';
import { toast } from 'sonner';

const STORAGE_KEY = 'japap.push_prompt_state.v1';
const RE_PROMPT_DAYS = 14;
const FIRST_DELAY_MS = 4_500;   // let the user see the home screen first

function _readState() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); }
  catch { return {}; }
}
function _writeState(patch) {
  try {
    const merged = { ..._readState(), ...patch, updated_at: Date.now() };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(merged));
  } catch { /* quota / private mode */ }
}

export default function PushOptInPrompt() {
  const { user, loading: authLoading } = useAuth();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (authLoading || !user?.user_id) return;
    if (!isPushSupported()) return;
    const perm = currentPermission();
    if (perm === 'granted' || perm === 'denied') return;  // browser already settled

    const state = _readState();
    // Honour "decided" (subscribed or hard-skipped).
    if (state.decided) return;
    // Soft-skip cooldown.
    if (state.last_skipped_at
        && (Date.now() - state.last_skipped_at) < RE_PROMPT_DAYS * 86_400_000) {
      return;
    }
    const t = setTimeout(() => setOpen(true), FIRST_DELAY_MS);
    return () => clearTimeout(t);
  }, [user?.user_id, authLoading]);

  const onActivate = async () => {
    setBusy(true);
    try {
      const r = await subscribePush();
      if (r.ok) {
        _writeState({ decided: true, decision: 'granted' });
        toast.success('Notifications activées 🔔');
        setOpen(false);
      } else if (r.reason === 'denied') {
        _writeState({ decided: true, decision: 'denied' });
        toast.error('Notifications refusées dans le navigateur. Activez-les depuis les paramètres du site.');
        setOpen(false);
      } else if (r.reason === 'unsupported' || r.reason === 'sdk_unavailable') {
        _writeState({ decided: true, decision: r.reason });
        toast.info('Notifications non disponibles sur ce navigateur.');
        setOpen(false);
      } else {
        toast.error('Impossible d\'activer les notifications. Réessayez.');
      }
    } finally { setBusy(false); }
  };

  const onSkip = (hard = false) => {
    _writeState(hard
      ? { decided: true, decision: 'skipped' }
      : { last_skipped_at: Date.now() });
    setOpen(false);
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[10000] flex items-end sm:items-center justify-center"
         data-testid="push-prompt-overlay"
         style={{ background: 'rgba(15,5,107,0.55)', backdropFilter: 'blur(6px)' }}
         onClick={() => onSkip(false)}>
      <div className="w-full sm:max-w-md text-white"
           data-testid="push-prompt-modal"
           onClick={(e) => e.stopPropagation()}
           style={{
             background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 55%, #2a0e95 100%)',
             borderTopLeftRadius: 24, borderTopRightRadius: 24,
             borderBottomLeftRadius: 'inherit', borderBottomRightRadius: 'inherit',
             boxShadow: '0 -16px 64px rgba(0,0,0,0.55)',
             padding: '28px 22px 28px',
           }}>
        <div className="flex items-start justify-between mb-4">
          <div className="w-14 h-14 rounded-2xl flex items-center justify-center"
               style={{ background: 'linear-gradient(135deg, #FFD700, #F7931A)',
                        boxShadow: '0 12px 36px rgba(255,215,0,0.45)' }}>
            <BellRinging size={28} weight="fill" color="#111" />
          </div>
          <button onClick={() => onSkip(false)}
                  data-testid="push-prompt-close"
                  className="p-1.5 rounded-full hover:bg-white/10 transition-colors">
            <X size={18} />
          </button>
        </div>

        <h2 className="font-['Outfit'] text-2xl font-extrabold mb-2 leading-tight">
          Sois notifié de tout ce qui compte
        </h2>
        <p className="text-white/70 text-sm font-['Manrope'] mb-5">
          Active les notifications JAPAP pour ne rien manquer — tu peux les désactiver à tout moment dans ton profil.
        </p>

        <div className="space-y-2.5 mb-6">
          <Benefit icon={<ChatCircleDots size={20} weight="fill" color="#22D3EE" />}
                   title="Messages & appels"
                   text="Sois alerté instantanément des nouveaux messages, mentions et appels." />
          <Benefit icon={<Wallet size={20} weight="fill" color="#10B981" />}
                   title="Paiements & wallet"
                   text="Confirme tes transactions et reçois tes pourboires en temps réel." />
          <Benefit icon={<Crown size={20} weight="fill" color="#FFD700" />}
                   title="Duels relevés & défis"
                   text="Sache dès qu'un ami a joué ton défi et qui a gagné le duel." />
          <Benefit icon={<Lightning size={20} weight="fill" color="#FF6B00" />}
                   title="Défi quotidien & séries"
                   text="Rappel chaque jour pour entretenir ta série Quiz et tes points cycle." />
          <Benefit icon={<Sparkle size={20} weight="fill" color="#A78BFA" />}
                   title="Roue de la fortune"
                   text="Boosts spéciaux, jackpots et offres flash réservées aux abonnés." />
        </div>

        <button onClick={onActivate} disabled={busy}
                data-testid="push-prompt-activate"
                className="w-full py-3.5 rounded-full font-bold text-base mb-2.5 disabled:opacity-60 active:scale-[0.97] transition-transform flex items-center justify-center gap-2"
                style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111',
                         boxShadow: '0 16px 44px rgba(255,215,0,0.5)' }}>
          <Bell size={18} weight="fill" />
          {busy ? 'Activation…' : 'Activer les notifications'}
        </button>
        <button onClick={() => onSkip(false)} disabled={busy}
                data-testid="push-prompt-later"
                className="w-full py-2.5 rounded-full font-medium text-sm text-white/70 hover:text-white">
          Plus tard
        </button>
      </div>
    </div>
  );
}

function Benefit({ icon, title, text }) {
  return (
    <div className="flex items-start gap-3 p-2.5 rounded-xl"
         style={{ background: 'rgba(255,255,255,0.06)',
                  border: '1px solid rgba(255,255,255,0.08)' }}>
      <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
           style={{ background: 'rgba(255,255,255,0.07)' }}>
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <div className="font-bold text-[13px] leading-tight mb-0.5">{title}</div>
        <div className="text-[11.5px] text-white/65 leading-snug">{text}</div>
      </div>
    </div>
  );
}
