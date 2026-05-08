import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useAuth } from '@/context/AuthContext';
import { useCurrency, formatMoney } from '@/utils/currency';
import { Star, Check, Crown, Lightning, Sparkle, Gift, Clock, X, XCircle, WifiHigh, TrendUp } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
const API = process.env.REACT_APP_BACKEND_URL;

const PLAN_STYLE = {
  starter: { icon: Star, color: '#4A90E2', gradient: 'linear-gradient(135deg, #4A90E2, #2563EB)' },
  creator: { icon: Lightning, color: '#E01C2E', gradient: 'linear-gradient(135deg, #E01C2E, #BE123C)' },
  business: { icon: Crown, color: '#F7931A', gradient: 'linear-gradient(135deg, #F7931A, #C2410C)' },
};

const DURATION_LABELS = { '1m': '1 mois', '3m': '3 mois', '12m': '12 mois' };

export default function ProPage() {
  const { t } = useTranslation();
  const { refreshUser } = useAuth();
  const [data, setData] = useState(null);
  const [status, setStatus] = useState(null);
  const [socialProof, setSocialProof] = useState(null);
  const [topHosts, setTopHosts] = useState([]);
  const [duration, setDuration] = useState('1m');
  const [subscribing, setSubscribing] = useState(null);
  const currency = useCurrency();

  const load = useCallback(async () => {
    try {
      const [p, s, sp, th] = await Promise.all([
        axios.get(`${API}/api/pro/plans`, { withCredentials: true }),
        axios.get(`${API}/api/pro/status`, { withCredentials: true }),
        axios.get(`${API}/api/pro/social-proof`, { withCredentials: true }).catch(() => null),
        axios.get(`${API}/api/pro/top-hosts?limit=3`, { withCredentials: true }).catch(() => null),
      ]);
      setData(p.data); setStatus(s.data);
      if (sp?.data) setSocialProof(sp.data);
      if (th?.data) setTopHosts(th.data);
    } catch {}
  }, []);
  useEffect(() => { load(); }, [load]);

  const subscribe = async (planId, useTrial = false) => {
    setSubscribing(planId + (useTrial ? ':trial' : ''));
    try {
      const { data: r } = await axios.post(`${API}/api/pro/subscribe`,
        { plan_id: planId, duration, use_trial: useTrial },
        { withCredentials: true }
      );
      toast.success(r.is_trial ? `Essai gratuit ${r.plan} activé !` : `${r.plan} activé — $${r.paid_usd}`);
      await load(); await refreshUser();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
    finally { setSubscribing(null); }
  };

  const cancel = async (immediate = false) => {
    if (!window.confirm(immediate ? t('pro.annuler_immediatement') : t('pro.annuler_a_la_fin_de_la_periode'))) return;
    try {
      const { data: r } = await axios.post(`${API}/api/pro/cancel`, { immediate }, { withCredentials: true });
      toast.success(r.message); load(); refreshUser();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  if (!data) return <div className="p-6 text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>;

  if (!data.pro_enabled) {
    return (
      <div className="p-6 max-w-3xl mx-auto jp-animate-fadeIn" data-testid="pro-disabled">
        <div className="jp-alert jp-alert-warning">
          <strong>{t('pro.japap_pro_est_temporairement_indisp')}</strong> Revenez bientôt !
        </div>
      </div>
    );
  }

  const { plans, trial, discounts, durations_enabled } = data;
  const activeSub = status?.is_pro ? status : null;

  return (
    <div className="p-6 max-w-6xl mx-auto pb-24 jp-animate-fadeIn" data-testid="pro-page">
      {/* Header */}
      <div className="text-center mb-8">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-[11px] font-bold uppercase tracking-wider mb-2"
          style={{ background: 'var(--jp-primary-subtle)', color: 'var(--jp-primary)' }}>
          <Crown size={14} weight="duotone" /> JAPAP PRO
        </div>
        <h1 className="font-['Outfit'] text-4xl sm:text-5xl font-extrabold mb-2" style={{ color: 'var(--jp-text)' }}>
          Passez en Pro
        </h1>
        <p className="text-base" style={{ color: 'var(--jp-text-secondary)' }}>
          Boost feed & reels, analytics, 0% commission sur tips, et bien plus.
        </p>
      </div>

      {/* Urgency banner (admin-driven) */}
      {data?.urgency?.enabled && (
        <UrgencyBanner urgency={data.urgency} first1000={data.first_1000} />
      )}

      {/* Social Proof — JAPAP Connect revshare (live) */}
      {socialProof?.enabled && socialProof?.pct > 0 &&
       (socialProof.last_30d.owner_count > 0 || socialProof.all_time.owner_count > 0) && (
        <div className="relative overflow-hidden rounded-2xl p-5 mb-6 jp-animate-scaleIn" data-testid="pro-social-proof"
          style={{
            background: 'linear-gradient(135deg, #064E3B 0%, #10B981 55%, #34D399 100%)',
            color: 'white',
          }}>
          <div className="absolute -right-4 -top-4 opacity-25">
            <WifiHigh size={130} weight="duotone" />
          </div>
          <div className="relative flex flex-col sm:flex-row items-start sm:items-center gap-4">
            <div className="flex-1 min-w-0 pr-0 sm:pr-4">
              <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-widest"
                style={{ background: 'rgba(255,255,255,0.22)' }}>
                <TrendUp size={10} weight="bold" /> Live · JAPAP Connect
              </div>
              <h3 className="font-['Outfit'] text-lg sm:text-2xl font-black mt-1.5 leading-tight" data-testid="sp-headline">
                {socialProof.last_30d.owner_count > 0
                  ? <>
                      <span data-testid="sp-owners">{socialProof.last_30d.owner_count}</span> hôte(s) Connect ont touché{' '}
                      <span data-testid="sp-total">{formatMoney(socialProof.last_30d.total_usd, currency, { short: true })}</span>
                      {' '}ce mois-ci
                    </>
                  : <>
                      Les hôtes JAPAP Connect ont déjà touché{' '}
                      {formatMoney(socialProof.all_time.total_usd, currency, { short: true })}
                    </>
                }
              </h3>
              <p className="text-sm opacity-90 mt-1">
                Part <strong data-testid="sp-pct">{socialProof.pct}%</strong> des abonnements Pro reversée aux propriétaires de hotspots.
                Passez <strong>{t('pro.business')}</strong> pour partager votre WiFi et rejoindre le réseau.
              </p>
            </div>
            <div className="flex items-center gap-2 sm:gap-3 shrink-0 self-start sm:self-auto">
              <div className="text-left sm:text-right">
                <div className="text-[10px] uppercase tracking-widest opacity-85 font-bold">Total reversé</div>
                <div className="font-['Outfit'] text-xl font-black leading-tight">
                  {formatMoney(socialProof.all_time.total_usd, currency, { short: true })}
                </div>
                <div className="text-[10px] opacity-85">toutes périodes</div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Top 3 hosts of the month — viral social proof */}
      {topHosts.length > 0 && (
        <div className="mb-6 jp-animate-fadeIn" data-testid="pro-top-hosts">
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-['Outfit'] text-lg font-black inline-flex items-center gap-2" style={{ color: 'var(--jp-text)' }}>
              🏆 Top {topHosts.length} hôte{topHosts.length > 1 ? 's' : ''} du mois
            </h3>
            <span className="text-[11px] font-bold uppercase tracking-widest" style={{ color: 'var(--jp-text-muted)' }}>
              via Part {socialProof?.pct || 2}%
            </span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {topHosts.map((h, i) => (
              <div key={h.user_id} className="jp-card p-4 flex items-center gap-3 jp-card-hover relative overflow-hidden"
                data-testid={`top-host-${h.rank}`}>
                <div className="absolute top-2 right-2 text-2xl">
                  {h.rank === 1 ? '🥇' : h.rank === 2 ? '🥈' : '🥉'}
                </div>                <div className="w-12 h-12 rounded-2xl flex items-center justify-center font-['Outfit'] font-black text-lg shrink-0"
                  style={{
                    background: h.rank === 1
                      ? 'linear-gradient(135deg, #F59E0B, #F97316)'
                      : h.rank === 2
                        ? 'linear-gradient(135deg, #9CA3AF, #6B7280)'
                        : 'linear-gradient(135deg, #CD7F32, #B45309)',
                    color: 'white',
                  }}>
                  {h.avatar ? <img src={h.avatar} alt={h.name} className="w-full h-full object-cover rounded-2xl" /> : (h.name?.[0] || '?').toUpperCase()}
                </div>
                <div className="flex-1 min-w-0 pr-6">
                  <div className="font-['Outfit'] font-bold truncate flex items-center gap-1">
                    {h.name}
                    {h.is_pro && <Crown size={12} weight="fill" style={{ color: '#F7931A' }} />}
                  </div>
                  {h.country && <div className="text-[10px] font-bold uppercase tracking-wider" style={{ color: 'var(--jp-text-muted)' }}>{h.country}</div>}
                  <div className="font-['Outfit'] font-black text-sm mt-0.5" style={{ color: '#10B981' }}>
                    +{formatMoney(h.earned_usd, currency, { short: true })}
                  </div>
                  <div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>{h.credits} crédit(s)</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Active subscription banner */}
      {activeSub && (
        <div className="jp-card-elevated p-5 mb-6 flex items-center gap-4" data-testid="active-sub-banner"
          style={{ borderLeft: '4px solid var(--jp-success)' }}>
          <Sparkle size={24} weight="fill" style={{ color: 'var(--jp-success)' }} />
          <div className="flex-1">
            <div className="font-['Outfit'] font-bold">
              {activeSub.plan_name} actif
              {activeSub.is_trial && <span className="ml-2 jp-badge jp-badge-warning">Essai</span>}
            </div>
            <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
              Jusqu'au {new Date(activeSub.expires_at).toLocaleDateString('fr-FR')} · {activeSub.days_remaining} jour(s) restant(s)
              {activeSub.cancel_at_period_end && ' · Sera annulé à la fin de la période'}
            </div>
          </div>
          {!activeSub.cancel_at_period_end && (
            <button onClick={() => cancel(false)} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="cancel-period">
              <Clock size={14} /> Annuler à la fin
            </button>
          )}
          <button onClick={() => cancel(true)} className="jp-btn jp-btn-sm" data-testid="cancel-immediate"
            style={{ background: 'var(--jp-error)', color: 'white' }}>
            <XCircle size={14} /> Annuler maintenant
          </button>
        </div>
      )}

      {/* Duration selector */}
      {!activeSub && (
        <div className="flex items-center justify-center gap-2 mb-6" data-testid="duration-selector">
          {['1m', '3m', '12m'].map(d => {
            if (!durations_enabled[d]) return null;
            const active = duration === d;
            const discountPct = d === '3m' ? discounts.m3 : d === '12m' ? discounts.m12 : 0;
            return (
              <button key={d} onClick={() => setDuration(d)} data-testid={`duration-${d}`}
                className="px-4 py-2 rounded-xl font-['Manrope'] text-sm font-semibold transition-all relative"
                style={{
                  background: active ? 'var(--jp-primary)' : 'var(--jp-surface-secondary)',
                  color: active ? 'white' : 'var(--jp-text)',
                  border: `2px solid ${active ? 'var(--jp-primary)' : 'transparent'}`,
                }}>
                {DURATION_LABELS[d]}
                {discountPct > 0 && (
                  <span className="absolute -top-2 -right-2 px-1.5 py-0.5 rounded-full text-[10px] font-bold"
                    style={{ background: '#10B981', color: 'white' }}>
                    −{discountPct}%
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}

      {/* Plans grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {plans.map((p, i) => {
          const style = PLAN_STYLE[p.plan_id] || PLAN_STYLE.starter;
          const Icon = style.icon;
          const quote = p.quotes[duration];
          const featured = p.plan_id === 'creator'; // highlight middle plan
          const canTrial = !activeSub && trial.enabled && p.trial_eligible;
          return (
            <div key={p.plan_id} className="jp-card-elevated p-6 relative flex flex-col" data-testid={`plan-${p.plan_id}`}
              style={{ border: featured ? `2px solid ${style.color}` : '1px solid var(--jp-border)', transform: featured ? 'translateY(-8px)' : undefined }}>
              {featured && (
                <div className="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider"
                  style={{ background: style.gradient, color: 'white' }}>{t('pro.populaire')}</div>
              )}
              <div className="w-12 h-12 rounded-2xl flex items-center justify-center mb-3" style={{ background: style.gradient }}>
                <Icon size={24} weight="fill" style={{ color: 'white' }} />
              </div>
              <h3 className="font-['Outfit'] text-xl font-extrabold" style={{ color: 'var(--jp-text)' }}>{p.name}</h3>
              {p.tagline && <p className="text-xs mb-3" style={{ color: 'var(--jp-text-muted)' }}>{p.tagline}</p>}

              {/* Price */}
              <div className="mb-4">
                {p.ab_discount_pct > 0 && (
                  <div className="inline-flex items-center gap-1 mb-1" data-testid={`ab-badge-${p.plan_id}`}>
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-bold"
                      style={{ background: '#EC4899', color: 'white' }}>
                      −{p.ab_discount_pct}% campagne
                    </span>
                    <span className="text-xs line-through" style={{ color: 'var(--jp-text-muted)' }}>
                      ${(parseFloat(p.original_price_usd) * quote.months).toFixed(2)}
                    </span>
                  </div>
                )}
                <div className="flex items-baseline gap-1">
                  <span className="font-['Outfit'] text-4xl font-extrabold" style={{ color: style.color }}>
                    ${quote.total_usd}
                  </span>
                  <span className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
                    /{duration === '1m' ? 'mois' : duration === '3m' ? '3 mois' : '12 mois'}
                  </span>
                </div>
                {quote.discount_pct > 0 && (
                  <div className="mt-1 text-xs" data-testid={`discount-${p.plan_id}`}>
                    <span className="line-through" style={{ color: 'var(--jp-text-muted)' }}>${quote.subtotal_usd}</span>
                    <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] font-bold"
                      style={{ background: '#10B981', color: 'white' }}>
                      −{quote.discount_pct}%
                    </span>
                    <span className="ml-2 font-semibold" style={{ color: '#10B981' }}>
                      Économisez ${quote.discount_usd}
                    </span>
                  </div>
                )}
                <div className="mt-1 text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
                  Soit ${(parseFloat(quote.total_usd) / quote.months).toFixed(2)}/mois
                </div>
              </div>

              {/* Features */}
              <ul className="space-y-1.5 mb-5 flex-1">
                {(p.features || []).map((f, j) => (
                  <li key={j} className="flex items-start gap-2 text-sm" style={{ color: 'var(--jp-text-secondary)' }}>
                    <Check size={16} weight="bold" style={{ color: style.color, marginTop: 2, flexShrink: 0 }} />
                    <span>{f}</span>
                  </li>
                ))}
              </ul>

              {/* CTAs */}
              {activeSub ? (
                <button disabled className="jp-btn jp-btn-ghost w-full" data-testid={`plan-${p.plan_id}-active`}>
                  {activeSub.plan_id === p.plan_id ? 'Plan actuel' : 'Annulez d\'abord votre plan actuel'}
                </button>
              ) : (
                <>
                  <button onClick={() => subscribe(p.plan_id, false)} disabled={subscribing !== null}
                    className="jp-btn w-full" data-testid={`subscribe-${p.plan_id}`}
                    style={{ background: style.gradient, color: 'white' }}>
                    {subscribing === p.plan_id ? 'Activation…' : `S'abonner · $${quote.total_usd}`}
                  </button>
                  {canTrial && (
                    <button onClick={() => subscribe(p.plan_id, true)} disabled={subscribing !== null}
                      className="jp-btn jp-btn-ghost w-full mt-2 text-sm" data-testid={`trial-${p.plan_id}`}
                      style={{ border: `1px dashed ${style.color}`, color: style.color }}>
                      <Gift size={14} weight="duotone" /> Essai gratuit {trial.days} jours
                    </button>
                  )}
                </>
              )}
            </div>
          );
        })}
      </div>

      {trial.already_used && !activeSub && (
        <p className="text-center text-xs mt-4" style={{ color: 'var(--jp-text-muted)' }}>
          Vous avez déjà utilisé votre essai gratuit.
        </p>
      )}
    </div>
  );
}

function UrgencyBanner({ urgency, first1000 }) {
  const { t } = useTranslation();
  const [timeLeft, setTimeLeft] = useState(null);
  const showFirst1000 = first1000?.enabled && first1000?.remaining > 0;

  useEffect(() => {
    if (!urgency?.ends_at) return;
    const target = new Date(urgency.ends_at).getTime();
    const tick = () => {
      const diff = target - Date.now();
      if (diff <= 0) { setTimeLeft({ expired: true }); return; }
      setTimeLeft({
        days: Math.floor(diff / 86400000),
        hours: Math.floor((diff % 86400000) / 3600000),
        mins: Math.floor((diff % 3600000) / 60000),
        secs: Math.floor((diff % 60000) / 1000),
      });
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [urgency?.ends_at]);

  if (timeLeft?.expired) return null;

  return (
    <div className="relative overflow-hidden rounded-2xl p-4 sm:p-5 mb-6 jp-animate-scaleIn"
      data-testid="pro-urgency-banner"
      style={{
        background: 'linear-gradient(135deg, #7F1D1D 0%, #DC2626 45%, #F59E0B 100%)',
        color: 'white',
      }}>
      <div className="absolute -right-4 -top-4 opacity-20 text-6xl">🔥</div>
      <div className="relative flex flex-col sm:flex-row sm:items-center gap-3">
        <div className="flex-1 min-w-0">
          <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-widest mb-1"
            style={{ background: 'rgba(255,255,255,0.22)' }}>
            ⚡ {showFirst1000 ? t('pro.edition_founders') : t('pro.offre_limitee')}
          </div>
          <h3 className="font-['Outfit'] text-xl font-black leading-tight" data-testid="urgency-title">
            {urgency.title || 'Offre limitée JAPAP Pro'}
          </h3>
          {urgency.subtitle && (
            <p className="text-sm opacity-90 mt-0.5">{urgency.subtitle}</p>
          )}
          {showFirst1000 && (
            <div className="mt-3" data-testid="first-1000-progress">
              <div className="h-2 rounded-full overflow-hidden" style={{ background: 'rgba(0,0,0,0.25)' }}>
                <div className="h-full rounded-full transition-all" style={{
                  width: `${first1000.progress_pct}%`,
                  background: 'linear-gradient(90deg, #FDE68A, #FBBF24)',
                }} />
              </div>
              <div className="flex justify-between text-[11px] mt-1 opacity-90 font-bold">
                <span>{first1000.claimed} / {first1000.cap} places prises</span>
                <span data-testid="first-1000-remaining">Plus que {first1000.remaining} ✦</span>
              </div>
            </div>
          )}
        </div>
        {timeLeft && !timeLeft.expired && !showFirst1000 && (
          <div className="flex gap-1 shrink-0" data-testid="urgency-countdown">
            {[
              { v: timeLeft.days, l: 'J' },
              { v: timeLeft.hours, l: 'H' },
              { v: timeLeft.mins, l: 'M' },
              { v: timeLeft.secs, l: 'S' },
            ].map((x, i) => (
              <div key={i} className="rounded-lg px-2 py-1 min-w-[40px] text-center"
                style={{ background: 'rgba(0,0,0,0.25)', backdropFilter: 'blur(8px)' }}>
                <div className="font-['Outfit'] text-lg font-black tabular-nums">{String(x.v).padStart(2, '0')}</div>
                <div className="text-[9px] opacity-80 font-bold">{x.l}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

