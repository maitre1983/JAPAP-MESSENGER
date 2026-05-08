import { useEffect, useState, useCallback, useMemo } from 'react';
import axios from 'axios';
import QRCode from 'qrcode';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  Copy, Share, Gift, Users, Crown, WhatsappLogo, TelegramLogo, XLogo,
  Envelope, ChatCircleText, CheckCircle, Trophy, Coins, Confetti, Lightning,
  QrCode, Globe, MapPin, Download,
} from '@phosphor-icons/react';
import { useCurrency, formatMoney } from '@/utils/currency';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * Build a share URL with UTM tracking so we can attribute conversions to a
 * specific channel server-side later.
 */
function buildShareUrl(baseUrl, channel) {
  if (!baseUrl) return '';
  try {
    const u = new URL(baseUrl);
    u.searchParams.set('utm_source', channel);
    u.searchParams.set('utm_medium', 'referral');
    u.searchParams.set('utm_campaign', 'japap_invite');
    return u.toString();
  } catch {
    const sep = baseUrl.includes('?') ? '&' : '?';
    return `${baseUrl}${sep}utm_source=${channel}&utm_medium=referral&utm_campaign=japap_invite`;
  }
}

export default function ReferralPage() {
  const { t } = useTranslation();
  const [data, setData] = useState(null);
  const [list, setList] = useState([]);
  const [lb, setLb] = useState(null);
  const [window_, setWindow] = useState('weekly');
  const [scope, setScope] = useState('global');
  const [config, setConfig] = useState(null);
  const [claiming, setClaiming] = useState(null);
  const [copied, setCopied] = useState('');
  const [qrDataUrl, setQrDataUrl] = useState('');
  const [showQr, setShowQr] = useState(false);
  const currency = useCurrency();

  const fetchAll = useCallback(async () => {
    try {
      const [cfg, me, myList, leader] = await Promise.all([
        axios.get(`${API}/api/referrals/config`, { withCredentials: true }),
        axios.get(`${API}/api/referrals/me`, { withCredentials: true }),
        axios.get(`${API}/api/referrals/list?limit=50`, { withCredentials: true }),
        axios.get(`${API}/api/referrals/leaderboard?window=${window_}&scope=${scope}`, { withCredentials: true }),
      ]);
      setConfig(cfg.data); setData(me.data);
      setList(myList.data.referrals || []); setLb(leader.data);
    } catch { /* noop */ }
  }, [window_, scope]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  // Generate the QR PNG as a dataURL whenever the share_url changes.
  // We point the QR at /p/{code} (conversion landing) instead of /r/{code}
  // because the QR is mostly used in-person where context is missing —
  // the landing's "X t'invite sur JAPAP" hero is far more convincing.
  useEffect(() => {
    if (!data?.share_url) return;
    const qrTarget = data.share_url.replace('/r/', '/p/');
    QRCode.toDataURL(qrTarget, {
      errorCorrectionLevel: 'M',
      margin: 2,
      width: 320,
      color: { dark: '#0B0F1A', light: '#FFFFFF' },
    }).then(setQrDataUrl).catch(() => setQrDataUrl(''));
  }, [data?.share_url]);

  const myCountryCode = lb?.me?.country_code || '';

  const shareMsg = useMemo(() => {
    if (!data) return '';
    return `Rejoins-moi sur JAPAP 🚀 avec mon code ${data.referral_code}`;
  }, [data]);

  if (!data || !config) return <div className="p-6 text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>;
  if (!config.enabled) {
    return (
      <div className="p-6 max-w-2xl mx-auto" data-testid="referral-disabled">
        <div className="jp-alert jp-alert-warning"><strong>Parrainage temporairement indisponible.</strong></div>
      </div>
    );
  }

  const copyText = (text, key, msg) => {
    try {
      navigator.clipboard.writeText(text);
      setCopied(key); setTimeout(() => setCopied(''), 1500);
      toast.success(msg || 'Copié');
    } catch {
      toast.error('Copie impossible — copiez manuellement');
    }
  };

  const openShare = (url) => window.open(url, '_blank', 'noopener,noreferrer');

  const downloadQr = () => {
    if (!qrDataUrl) return;
    const a = document.createElement('a');
    a.href = qrDataUrl;
    a.download = `japap-parrainage-${data.referral_code}.png`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const claim = async (tierCount) => {
    setClaiming(tierCount);
    try {
      const { data: r } = await axios.post(`${API}/api/referrals/claim`, { tier_count: tierCount }, { withCredentials: true });
      toast.success(r.message);
      fetchAll();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
    finally { setClaiming(null); }
  };

  const { stats, tiers, next_tier, progress_to_next_pct, bonuses, badges } = data;
  const boost = config.boost || { active: false };
  const boostMult = Number(boost.multiplier || 1);
  const effRefBonus = boost.active && boost.applies_to_referrer ? bonuses.referrer_usd * boostMult : bonuses.referrer_usd;
  const effReeBonus = boost.active && boost.applies_to_referee ? bonuses.referee_usd * boostMult : bonuses.referee_usd;
  const referrerBonusLocal = formatMoney(effRefBonus, currency, { short: true });
  const refereeBonusLocal = formatMoney(effReeBonus, currency, { short: true });

  const baseShare = data.share_url;
  // /p/{code} = conversion landing (longer URL but stronger funnel).
  // /r/{code} (data.share_url) = direct redirect to /register.
  const baseShareUrl = baseShare;
  const previewUrl = baseShare ? baseShare.replace('/r/', '/p/') : '';
  const waUrl = `https://wa.me/?text=${encodeURIComponent(`${shareMsg} : ${buildShareUrl(previewUrl, 'whatsapp')}`)}`;
  const tgUrl = `https://t.me/share/url?url=${encodeURIComponent(buildShareUrl(previewUrl, 'telegram'))}&text=${encodeURIComponent(shareMsg)}`;
  const xUrl = `https://twitter.com/intent/tweet?text=${encodeURIComponent(shareMsg)}&url=${encodeURIComponent(buildShareUrl(previewUrl, 'x'))}`;
  const smsUrl = `sms:?&body=${encodeURIComponent(`${shareMsg} : ${buildShareUrl(previewUrl, 'sms')}`)}`;
  const emailUrl = `mailto:?subject=${encodeURIComponent('Rejoins JAPAP avec mon code')}&body=${encodeURIComponent(`${shareMsg} : ${buildShareUrl(previewUrl, 'email')}`)}`;

  return (
    <div className="p-6 max-w-5xl mx-auto pb-24 jp-animate-fadeIn" data-testid="referral-page">
      {/* Boost Event banner */}
      {boost.active && (
        <div className="mb-5 p-4 rounded-2xl flex items-center gap-3 jp-animate-pulse" data-testid="boost-banner"
          style={{ background: 'linear-gradient(135deg, #F59E0B, #EC4899)', color: 'white', boxShadow: '0 8px 32px rgba(245,158,11,0.35)' }}>
          <Lightning size={28} weight="fill" />
          <div className="flex-1">
            <div className="font-['Outfit'] text-lg font-extrabold">
              ⚡ {boost.name} · Bonus ×{boostMult}
            </div>
            <div className="text-xs opacity-90">
              {boost.applies_to_referrer && boost.applies_to_referee ? t('referral.parrain_filleul_boostes') : boost.applies_to_referrer ? t('referral.parrain_booste') : boost.applies_to_referee ? t('referral.filleul_booste') : ''}
              {boost.applies_to_tiers && ' · Paliers boostés'}
              {boost.ends_at && ` · Jusqu'au ${new Date(boost.ends_at).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}`}
            </div>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="text-center mb-6">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-[11px] font-bold uppercase tracking-wider mb-2"
          style={{ background: 'var(--jp-primary-subtle)', color: 'var(--jp-primary)' }}>
          <Gift size={14} weight="duotone" /> Parrainage JAPAP
        </div>
        <h1 className="font-['Outfit'] text-3xl sm:text-4xl font-extrabold" style={{ color: 'var(--jp-text)' }}>
          Invitez, gagnez, débloquez
        </h1>
        <p className="text-sm mt-1" style={{ color: 'var(--jp-text-secondary)' }}>
          {bonuses.referrer_usd > 0 && <>{t('referral.parrain')}<strong>{referrerBonusLocal}</strong> par filleul actif</>}
          {bonuses.referrer_usd > 0 && bonuses.referee_usd > 0 && ' · '}
          {bonuses.referee_usd > 0 && <>{t('referral.filleul')}<strong>{refereeBonusLocal}</strong> de bienvenue</>}
        </p>
      </div>

      {/* Referral code + share card */}
      <div className="jp-card-elevated p-5 mb-6" data-testid="code-card"
        style={{ background: 'linear-gradient(135deg, var(--jp-primary-subtle), var(--jp-secondary-subtle))' }}>
        <div className="text-xs font-bold uppercase tracking-wider mb-2" style={{ color: 'var(--jp-text-muted)' }}>Votre code</div>
        <div className="flex items-center gap-3">
          <div className="font-['Outfit'] text-3xl font-extrabold tracking-wider flex-1" style={{ color: 'var(--jp-primary)' }} data-testid="referral-code">
            {data.referral_code}
          </div>
          <button onClick={() => copyText(data.referral_code, 'code', 'Code copié')} className="jp-btn jp-btn-primary jp-btn-sm" data-testid="copy-code">
            {copied === 'code' ? <><CheckCircle size={16} /> Copié</> : <><Copy size={16} /> Code</>}
          </button>
        </div>

        {/* Short link row — iter109 */}
        <div className="mt-3 flex items-center gap-2 p-2.5 rounded-xl"
             style={{ background: 'rgba(255,255,255,0.55)', border: '1px solid var(--jp-border)' }}
             data-testid="referral-link-row">
          <div className="flex-1 min-w-0">
            <div className="text-[10px] uppercase font-bold opacity-60 tracking-wider">Lien court</div>
            <div className="text-xs font-mono truncate" data-testid="referral-share-url">{data.share_url}</div>
          </div>
          <button onClick={() => copyText(data.share_url, 'link', 'Lien copié')}
                  className="jp-btn jp-btn-ghost jp-btn-sm whitespace-nowrap" data-testid="copy-link">
            {copied === 'link' ? <><CheckCircle size={14} /> Copié</> : <><Copy size={14} /> Lien</>}
          </button>
          <button onClick={() => setShowQr(true)} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="show-qr-btn" title="Afficher le QR">
            <QrCode size={14} />
          </button>
        </div>

        {/* Share buttons */}
        <div className="flex items-center gap-1.5 mt-3 flex-wrap" data-testid="share-buttons">
          <button onClick={() => openShare(waUrl)} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="share-whatsapp" style={{ color: '#25D366' }}>
            <WhatsappLogo size={16} weight="fill" /> WhatsApp
          </button>
          <button onClick={() => openShare(tgUrl)} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="share-telegram" style={{ color: '#0088CC' }}>
            <TelegramLogo size={16} weight="fill" /> Telegram
          </button>
          <button onClick={() => openShare(xUrl)} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="share-x" style={{ color: 'var(--jp-text)' }}>
            <XLogo size={16} weight="fill" /> X
          </button>
          <a href={smsUrl} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="share-sms" style={{ color: '#10B981' }}>
            <ChatCircleText size={16} weight="fill" /> SMS
          </a>
          <a href={emailUrl} className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="share-email" style={{ color: '#6366F1' }}>
            <Envelope size={16} weight="fill" /> Email
          </a>
          {typeof navigator !== 'undefined' && navigator.share && (
            <button onClick={() => navigator.share({ title: 'JAPAP', text: shareMsg, url: buildShareUrl(previewUrl, 'native') }).catch(() => {})}
              className="jp-btn jp-btn-ghost jp-btn-sm" data-testid="share-native">
              <Share size={16} weight="fill" /> Partager
            </button>
          )}
        </div>
      </div>

      {/* QR modal */}
      {showQr && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4"
             style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
             onClick={() => setShowQr(false)}
             data-testid="referral-qr-modal">
          <div className="w-full max-w-sm rounded-2xl p-5 space-y-3"
               style={{ background: 'var(--jp-surface)' }}
               onClick={(e) => e.stopPropagation()}>
            <div className="text-center">
              <div className="text-xs uppercase tracking-wider font-bold opacity-60">Scannez & rejoignez</div>
              <h3 className="font-['Outfit'] text-xl font-bold mt-1">Code {data.referral_code}</h3>
            </div>
            {qrDataUrl ? (
              <img src={qrDataUrl} alt="QR code parrainage" className="w-full rounded-xl" data-testid="referral-qr-image" />
            ) : (
              <div className="w-full aspect-square flex items-center justify-center rounded-xl"
                   style={{ background: 'var(--jp-surface-secondary)' }}>
                <div className="text-xs opacity-60">Génération…</div>
              </div>
            )}
            <p className="text-[11px] text-center opacity-70 break-all">{previewUrl || data.share_url}</p>
            <div className="flex gap-2">
              <button onClick={downloadQr} disabled={!qrDataUrl}
                      className="jp-btn jp-btn-primary flex-1 text-xs" data-testid="referral-qr-download">
                <Download size={14} /> Télécharger
              </button>
              <button onClick={() => setShowQr(false)} className="jp-btn jp-btn-ghost text-xs" data-testid="referral-qr-close">
                Fermer
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Stats grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6" data-testid="stats-grid">
        <StatCard icon={Users} label="Filleuls" value={stats.total} color="var(--jp-primary)" />
        <StatCard icon={CheckCircle} label="Actifs" value={stats.active_count} color="var(--jp-success)" />
        <StatCard icon={Confetti} label="En attente" value={stats.pending} color="#F59E0B" />
        <StatCard icon={Coins} label="Gagné (USD)" value={`$${parseFloat(stats.total_earned_usd).toFixed(2)}`} color="#8B5CF6" />
      </div>

      {/* Gamification: progress + badges */}
      {config.gamification_enabled && (
        <div className="jp-card-elevated p-5 mb-6" data-testid="gamification">
          {next_tier ? (
            <>
              <div className="flex items-center justify-between mb-2">
                <div className="font-['Outfit'] font-bold">Prochain palier : {next_tier.label}</div>
                <div className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>{stats.active_count} / {next_tier.count}</div>
              </div>
              <div className="w-full h-3 rounded-full overflow-hidden" style={{ background: 'var(--jp-surface-secondary)' }}>
                <div className="h-full rounded-full transition-all duration-700"
                  style={{ width: `${progress_to_next_pct}%`, background: 'linear-gradient(90deg, var(--jp-primary), var(--jp-secondary))' }} />
              </div>
            </>
          ) : (
            <div className="text-sm font-['Manrope']" style={{ color: 'var(--jp-success)' }}>
              🏆 Vous avez débloqué tous les paliers !
            </div>
          )}
          {badges.length > 0 && (
            <div className="mt-4 flex flex-wrap gap-2">
              {badges.map((b, i) => (
                <span key={i} className="jp-badge jp-badge-primary" data-testid={`badge-${b.tier}`}>
                  <Trophy size={12} weight="fill" className="inline mr-1" /> {b.label}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Tiers */}
      <div className="mb-6">
        <h2 className="font-['Outfit'] text-lg font-bold mb-3" style={{ color: 'var(--jp-text)' }}>Paliers de récompenses</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {tiers.map((tier, i) => {
            const reached = stats.active_count >= tier.count;
            const claimable = reached && (stats.rewarded < stats.active_count); // still available
            const rewardLabel = tier.reward_type === 'wallet'
              ? formatMoney(tier.reward_value, currency)
              : `${tier.reward_value} jours Pro`;
            return (
              <div key={i} className="jp-card p-4" data-testid={`tier-${tier.count}`}
                style={{ border: `2px solid ${reached ? 'var(--jp-success)' : 'var(--jp-border)'}`, opacity: reached ? 1 : 0.9 }}>
                <div className="flex items-center justify-between mb-2">
                  <div className="text-xs uppercase font-bold tracking-wider" style={{ color: reached ? 'var(--jp-success)' : 'var(--jp-text-muted)' }}>
                    {tier.count} filleul{tier.count > 1 ? 's' : ''}
                  </div>
                  {reached && <CheckCircle size={18} weight="fill" style={{ color: 'var(--jp-success)' }} />}
                </div>
                <div className="font-['Outfit'] text-lg font-extrabold mb-1" style={{ color: 'var(--jp-text)' }}>
                  {tier.label || rewardLabel}
                </div>
                <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>{rewardLabel}</div>
                {claimable && (
                  <button onClick={() => claim(tier.count)} disabled={claiming === tier.count}
                    className="jp-btn jp-btn-primary jp-btn-sm w-full mt-3" data-testid={`claim-${tier.count}`}>
                    {claiming === tier.count ? t('referral.reclamation') : <><Gift size={14} /> Réclamer</>}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Leaderboard */}
      {config.leaderboard_enabled && (
        <div className="jp-card-elevated p-5 mb-6" data-testid="leaderboard">
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <h2 className="font-['Outfit'] text-lg font-bold">Top parrains</h2>
            <div className="flex gap-2 flex-wrap">
              {/* Scope toggle pays/global — iter109 */}
              {myCountryCode && (
                <div className="flex gap-1 p-1 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }} data-testid="scope-toggle">
                  <button onClick={() => setScope('global')} data-testid="scope-global"
                    className="px-3 py-1 text-xs font-semibold rounded-md transition-all flex items-center gap-1"
                    style={{ background: scope === 'global' ? 'white' : 'transparent', color: scope === 'global' ? 'var(--jp-primary)' : 'var(--jp-text-muted)' }}>
                    <Globe size={12} weight="bold" /> Mondial
                  </button>
                  <button onClick={() => setScope('country')} data-testid="scope-country"
                    className="px-3 py-1 text-xs font-semibold rounded-md transition-all flex items-center gap-1"
                    style={{ background: scope === 'country' ? 'white' : 'transparent', color: scope === 'country' ? 'var(--jp-primary)' : 'var(--jp-text-muted)' }}>
                    <MapPin size={12} weight="bold" /> {myCountryCode}
                  </button>
                </div>
              )}
              <div className="flex gap-1 p-1 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }}>
                {['weekly', 'all_time'].map(w => (
                  <button key={w} onClick={() => setWindow(w)} data-testid={`window-${w}`}
                    className="px-3 py-1 text-xs font-semibold rounded-md transition-all"
                    style={{ background: window_ === w ? 'white' : 'transparent', color: window_ === w ? 'var(--jp-primary)' : 'var(--jp-text-muted)' }}>
                    {w === 'weekly' ? 'Cette semaine' : 'Tout temps'}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* My ranks chips */}
          {(lb?.me?.rank_global || lb?.me?.rank_country) && (
            <div className="flex flex-wrap gap-2 mb-3" data-testid="my-ranks">
              {lb.me.rank_global && (
                <span className="jp-badge jp-badge-primary text-[11px]" data-testid="my-rank-global">
                  <Globe size={11} weight="bold" className="inline mr-1" />
                  Vous · #{lb.me.rank_global} mondial
                </span>
              )}
              {lb.me.rank_country && myCountryCode && (
                <span className="jp-badge jp-badge-success text-[11px]" data-testid="my-rank-country">
                  <MapPin size={11} weight="bold" className="inline mr-1" />
                  Vous · #{lb.me.rank_country} {myCountryCode}
                </span>
              )}
            </div>
          )}

          {lb?.leaders?.length === 0 ? (
            <div className="text-center text-sm py-6" style={{ color: 'var(--jp-text-muted)' }}>Aucun classement encore.</div>
          ) : (
            <div className="space-y-2">
              {lb?.leaders?.map(l => (
                <div key={l.user_id} className="flex items-center gap-3" data-testid={`leader-${l.rank}`}>
                  <div className="w-8 text-center font-['Outfit'] font-extrabold"
                    style={{ color: l.rank === 1 ? '#F59E0B' : l.rank === 2 ? '#9CA3AF' : l.rank === 3 ? '#CD7F32' : 'var(--jp-text-muted)' }}>
                    {l.rank === 1 ? '🥇' : l.rank === 2 ? '🥈' : l.rank === 3 ? '🥉' : `#${l.rank}`}
                  </div>
                  <div className="jp-avatar jp-avatar-sm jp-avatar-primary">{l.name?.[0] || '?'}</div>
                  <span className="flex-1 font-medium">
                    {l.name}
                    {l.country_code && <span className="ml-1 text-[10px] opacity-60">· {l.country_code}</span>}
                  </span>
                  <span className="jp-badge jp-badge-success">{l.active_count} actifs</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* My referrals list */}
      {list.length > 0 && (
        <div className="jp-card-elevated p-5" data-testid="my-referrals">
          <h2 className="font-['Outfit'] text-lg font-bold mb-3">Mes filleuls ({list.length})</h2>
          <div className="divide-y" style={{ borderColor: 'var(--jp-border)' }}>
            {list.map(r => (
              <div key={r.referral_id} className="flex items-center gap-3 py-3">
                <div className="jp-avatar jp-avatar-sm jp-avatar-primary">{r.friend.name?.[0] || '?'}</div>
                <div className="flex-1">
                  <div className="text-sm font-medium">{r.friend.name}</div>
                  <div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>{r.friend.email_masked}</div>
                </div>
                <span className={`jp-badge ${r.status === 'active' || r.status === 'rewarded' ? 'jp-badge-success' : r.blocked ? 'jp-badge-error' : 'jp-badge-warning'}`}>
                  {r.blocked ? t('referral.bloque') : r.status}
                </span>
                {parseFloat(r.referrer_bonus_usd) > 0 && (
                  <span className="text-xs font-semibold" style={{ color: 'var(--jp-success)' }}>
                    +{formatMoney(r.referrer_bonus_usd, currency, { short: true })}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ icon: Icon, label, value, color }) {
  return (
    <div className="jp-card p-4 jp-card-hover" data-testid={`stat-${label.toLowerCase()}`}>
      <div className="w-9 h-9 rounded-xl flex items-center justify-center mb-2" style={{ background: `${color}20` }}>
        <Icon size={18} weight="duotone" style={{ color }} />
      </div>
      <div className="text-[10px] uppercase tracking-wider font-bold" style={{ color: 'var(--jp-text-muted)' }}>{label}</div>
      <div className="font-['Outfit'] text-2xl font-extrabold" style={{ color: 'var(--jp-text)' }}>{value}</div>
    </div>
  );
}
