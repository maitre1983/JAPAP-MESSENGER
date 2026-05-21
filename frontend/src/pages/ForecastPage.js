/**
 * iter241a — Prédictions (Forecast Markets) — page utilisateur.
 *
 * - Liste des marchés actifs, filtrée par catégorie.
 * - Modal de mise avec vérification solde wallet + calcul gain potentiel.
 * - Onglet "Mes paris" avec historique + statut (placé/gagné/perdu/remboursé).
 *
 * Bilingue 5 langues via i18next — aucune string hardcodée.
 * 100% additif : pas de modif d'aucun composant existant.
 */
import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import axios from 'axios';
import { useAuth } from '@/context/AuthContext';

const API = process.env.REACT_APP_BACKEND_URL;

const CATEGORIES = ['politics', 'economy', 'crypto', 'culture', 'world', 'sport'];

function fmtUSD(n) {
  if (n === null || n === undefined || isNaN(Number(n))) return '—';
  return `${Number(n).toFixed(2)} USD`;
}

function fmtCountdown(iso, t) {
  if (!iso) return '—';
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return t('forecast.market_closed');
  const d = Math.floor(ms / 86400000);
  const h = Math.floor((ms % 86400000) / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  if (d > 0) return `${d}j ${h}h`;
  if (h > 0) return `${h}h ${m}min`;
  return `${m}min`;
}

export default function ForecastPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [searchParams] = useSearchParams();
  const location = useLocation();

  const [view, setView] = useState('markets'); // markets | my-bets
  const [category, setCategory] = useState(null);
  const [markets, setMarkets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [betModal, setBetModal] = useState(null); // { market, option }
  const [shareModal, setShareModal] = useState(null); // { market }
  const [referralPct, setReferralPct] = useState(10);

  // iter241a-share — Capture ?ref=<user_id> on mount and stash in localStorage
  // with a 30-day TTL. The bet payload picks it up when the user actually
  // places a bet, so commission goes to the person who shared the link.
  useEffect(() => {
    const ref = searchParams.get('ref');
    if (ref && user?.user_id && ref !== user.user_id) {
      try {
        localStorage.setItem('forecast_ref', JSON.stringify({
          ref, expires: Date.now() + 30 * 24 * 3600 * 1000,
        }));
      } catch (_) {}
    }
    // Pull the public referral % so the share message stays in sync.
    axios.get(`${API}/api/forecast/settings/public`)
      .then(r => setReferralPct(Number(r.data?.referral_commission_percent || 10)))
      .catch(() => {});
    // eslint-disable-next-line
  }, [location.search]);

  useEffect(() => { loadMarkets(); /* eslint-disable-next-line */ }, [category]);

  const loadMarkets = async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/api/forecast/markets`, {
        params: { status: 'active', ...(category ? { category } : {}) },
        withCredentials: true,
      });
      setMarkets(data.markets || []);
    } catch (e) {
      if (e?.response?.status === 503) {
        toast.error(t('forecast.module_disabled', { defaultValue: 'Module Prédictions désactivé.' }));
        navigate('/games');
      }
    } finally { setLoading(false); }
  };

  return (
    <div className="max-w-3xl mx-auto px-4 py-6" data-testid="forecast-page">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2" style={{ color: 'var(--jp-text)' }}>
            <span>🔮</span> {t('forecast.title')}
          </h1>
          <p className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>{t('forecast.subtitle')}</p>
        </div>
        <div className="flex gap-1 p-0.5 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }}>
          <button onClick={() => setView('markets')}
            className={`px-3 py-1.5 rounded-md text-xs font-bold ${view === 'markets' ? 'jp-btn-primary' : ''}`}
            data-testid="forecast-tab-markets">
            {t('forecast.markets_tab')}
          </button>
          <button onClick={() => setView('my-bets')}
            className={`px-3 py-1.5 rounded-md text-xs font-bold ${view === 'my-bets' ? 'jp-btn-primary' : ''}`}
            data-testid="forecast-tab-my-bets">
            {t('forecast.my_bets')}
          </button>
        </div>
      </div>

      {view === 'markets' && (
        <>
          <CategoryFilter t={t} value={category} onChange={setCategory} />
          {loading ? (
            <div className="text-center py-12" style={{ color: 'var(--jp-text-muted)' }}>
              {t('forecast.loading', { defaultValue: 'Chargement…' })}
            </div>
          ) : markets.length === 0 ? (
            <div className="jp-card p-8 text-center" style={{ color: 'var(--jp-text-muted)' }}>
              {t('forecast.no_markets', { defaultValue: 'Aucun marché disponible.' })}
            </div>
          ) : (
            markets.map(m => (
              <MarketCard key={m.market_id} t={t} market={m}
                referralPct={referralPct}
                onBet={(opt) => setBetModal({ market: m, option: opt })}
                onShare={() => setShareModal({ market: m })} />
            ))
          )}
        </>
      )}

      {view === 'my-bets' && <MyBetsList t={t} />}

      {betModal && (
        <BetModal t={t} navigate={navigate}
          market={betModal.market} option={betModal.option}
          referralPct={referralPct}
          onClose={() => setBetModal(null)}
          onSuccess={() => { setBetModal(null); loadMarkets(); }} />
      )}

      {shareModal && (
        <ShareModal t={t} user={user}
          market={shareModal.market}
          referralPct={referralPct}
          onClose={() => setShareModal(null)} />
      )}
    </div>
  );
}

function CategoryFilter({ t, value, onChange }) {
  return (
    <div className="flex gap-2 mb-4 overflow-x-auto pb-2" data-testid="forecast-cat-filter">
      <button onClick={() => onChange(null)}
        className={`px-3 py-1.5 rounded-full text-xs font-bold whitespace-nowrap ${!value ? 'jp-btn-primary' : 'jp-btn-ghost'}`}
        data-testid="forecast-cat-all">
        {t('forecast.cat_all', { defaultValue: 'Tous' })}
      </button>
      {CATEGORIES.map(cat => (
        <button key={cat} onClick={() => onChange(cat)}
          className={`px-3 py-1.5 rounded-full text-xs font-bold whitespace-nowrap ${value === cat ? 'jp-btn-primary' : 'jp-btn-ghost'}`}
          data-testid={`forecast-cat-${cat}`}>
          {t(`forecast.cat_${cat}`)}
        </button>
      ))}
    </div>
  );
}

function MarketCard({ t, market, referralPct, onBet, onShare }) {
  const closed = market.closes_at && new Date(market.closes_at).getTime() <= Date.now();
  return (
    <div className="jp-card-elevated p-4 mb-3" data-testid={`forecast-market-${market.market_id}`}>
      <div className="flex justify-between items-start gap-3 mb-2">
        <div className="flex-1">
          <div className="text-[10px] uppercase tracking-wider font-bold mb-1" style={{ color: 'var(--jp-text-muted)' }}>
            {t(`forecast.cat_${market.category}`, { defaultValue: market.category })}
          </div>
          <h3 className="font-bold text-base leading-tight" style={{ color: 'var(--jp-text)' }}>{market.title}</h3>
          {market.description && (
            <p className="text-xs mt-1 line-clamp-2" style={{ color: 'var(--jp-text-muted)' }}>{market.description}</p>
          )}
        </div>
        <span className="text-[11px] font-bold whitespace-nowrap px-2 py-1 rounded-full"
              style={{ background: closed ? 'var(--jp-error-subtle, #fee)' : 'var(--jp-primary-subtle)',
                       color: closed ? 'var(--jp-error)' : 'var(--jp-primary)' }}>
          {t('forecast.closes_in')} {fmtCountdown(market.closes_at, t)}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 mt-3">
        {(market.options || []).map(o => (
          <button key={o.option_id} disabled={closed} onClick={() => onBet(o)}
            className="jp-btn-secondary p-3 text-left disabled:opacity-50 disabled:cursor-not-allowed"
            data-testid={`forecast-option-${o.option_id}`}>
            <div className="font-bold text-sm" style={{ color: 'var(--jp-text)' }}>{o.label}</div>
            <div className="text-[11px] mt-0.5" style={{ color: 'var(--jp-text-muted)' }}>
              ×{Number(o.multiplier).toFixed(2)} · {o.bets_count || 0} {t('forecast.bets', { defaultValue: 'paris' })}
            </div>
          </button>
        ))}
      </div>

      <div className="flex justify-between text-[11px] mt-3 pt-2 border-t" style={{ color: 'var(--jp-text-muted)', borderColor: 'var(--jp-border)' }}>
        <span>👥 {market.unique_bettors || 0} {t('forecast.bettors', { defaultValue: 'parieurs' })}</span>
        <span>💰 {fmtUSD(market.total_staked)} {t('forecast.staked', { defaultValue: 'misés' })}</span>
      </div>

      {/* iter241a-share — Share CTA + earn-banner. The banner only shows on
          active markets (no point sharing a closed one), and the button is
          always clickable while the market exists. */}
      {!closed && (
        <>
          <div className="mt-3 px-3 py-2 rounded-lg flex items-center gap-2"
               style={{ background: 'linear-gradient(135deg, rgba(124,58,237,0.10), rgba(15,5,107,0.06))',
                        border: '1px dashed var(--jp-primary, #7c3aed)' }}
               data-testid={`forecast-share-banner-${market.market_id}`}>
            <span className="text-lg">🎁</span>
            <div className="flex-1 text-[11px] leading-tight" style={{ color: 'var(--jp-text)' }}>
              {t('forecast.share_earn_banner', {
                defaultValue: 'Partage cette prédiction et gagne {{pct}}% de chaque mise gagnante de tes followers !',
                pct: referralPct,
              })}
            </div>
            <button onClick={onShare}
                    className="jp-btn-primary jp-btn-sm whitespace-nowrap"
                    data-testid={`forecast-share-btn-${market.market_id}`}>
              📢 {t('forecast.share', { defaultValue: 'Partager' })}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/** iter241a-share — Native share modal: Twitter, WhatsApp, Telegram, FB,
 * Copy link. Builds a personalised URL that the receiver lands on; once
 * they place a bet, the upstream user earns referral_commission_percent
 * of every winning stake.
 */
function ShareModal({ t, user, market, referralPct, onClose }) {
  const myRef = user?.user_id || '';
  const path = `/games/forecast?market=${market.market_id}&ref=${myRef}`;
  const url = (typeof window !== 'undefined' ? window.location.origin : '') + path;
  const shareMessage = t('forecast.share_message', {
    defaultValue: '🔮 Je viens de parier sur "{{title}}" sur JAPAP. Rejoins-moi et gagne aussi 👇',
    title: market.title,
  });
  const composed = `${shareMessage}\n${url}`;

  const targets = [
    { id: 'whatsapp', label: 'WhatsApp', emoji: '🟢',
      href: `https://wa.me/?text=${encodeURIComponent(composed)}` },
    { id: 'telegram', label: 'Telegram', emoji: '🔵',
      href: `https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(shareMessage)}` },
    { id: 'twitter',  label: 'X / Twitter', emoji: '⚫',
      href: `https://twitter.com/intent/tweet?text=${encodeURIComponent(shareMessage)}&url=${encodeURIComponent(url)}` },
    { id: 'facebook', label: 'Facebook', emoji: '🔷',
      href: `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(url)}` },
  ];

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      toast.success(t('forecast.link_copied', { defaultValue: '✓ Lien copié' }));
    } catch (_) {
      toast.error(t('forecast.copy_failed', { defaultValue: 'Copie impossible' }));
    }
  };

  const nativeShare = async () => {
    if (navigator.share) {
      try {
        await navigator.share({ title: market.title, text: shareMessage, url });
      } catch (_) {}
    } else {
      copy();
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-4"
         style={{ background: 'rgba(0,0,0,0.6)' }} onClick={onClose} data-testid="forecast-share-modal">
      <div className="jp-card-elevated p-5 w-full max-w-md" onClick={e => e.stopPropagation()}>
        <h3 className="font-bold text-lg mb-1">📢 {t('forecast.share_modal_title', { defaultValue: 'Partager & gagner' })}</h3>
        <p className="text-sm mb-3" style={{ color: 'var(--jp-text-muted)' }}>
          {t('forecast.share_modal_subtitle', {
            defaultValue: 'Chaque follower qui parie via ton lien ET gagne te rapporte {{pct}}% de sa mise.',
            pct: referralPct,
          })}
        </p>
        <div className="grid grid-cols-2 gap-2 mb-3">
          {targets.map(s => (
            <a key={s.id} href={s.href} target="_blank" rel="noopener noreferrer"
               className="jp-btn-secondary jp-btn-sm flex items-center justify-center gap-2"
               data-testid={`forecast-share-target-${s.id}`}>
              <span>{s.emoji}</span> {s.label}
            </a>
          ))}
        </div>
        <div className="flex gap-2 mb-3">
          <input className="jp-input flex-1 text-xs" value={url} readOnly
                 data-testid="forecast-share-url" />
          <button onClick={copy} className="jp-btn-secondary jp-btn-sm whitespace-nowrap"
                  data-testid="forecast-share-copy">
            📋 {t('forecast.copy', { defaultValue: 'Copier' })}
          </button>
        </div>
        {typeof navigator !== 'undefined' && navigator.share && (
          <button onClick={nativeShare} className="jp-btn-primary w-full mb-2"
                  data-testid="forecast-share-native">
            {t('forecast.native_share', { defaultValue: 'Partager via…' })}
          </button>
        )}
        <button onClick={onClose} className="jp-btn-ghost w-full">{t('forecast.cancel', { defaultValue: 'Annuler' })}</button>
      </div>
    </div>
  );
}

function BetModal({ t, navigate, market, option, referralPct, onClose, onSuccess }) {
  const [stake, setStake] = useState('');
  const [balance, setBalance] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    axios.get(`${API}/api/wallet/balance`, { withCredentials: true })
      .then(r => setBalance(Number(r.data?.balance || 0)))
      .catch(() => setBalance(0));
  }, []);

  // iter241a-share — pull the captured referrer (if any & not expired).
  const getReferrer = () => {
    try {
      const raw = localStorage.getItem('forecast_ref');
      if (!raw) return null;
      const { ref, expires } = JSON.parse(raw);
      if (!ref || !expires || expires < Date.now()) {
        localStorage.removeItem('forecast_ref'); return null;
      }
      return ref;
    } catch (_) { return null; }
  };

  const stakeNum = Number(stake) || 0;
  const minBet = Number(market.min_bet || 1);
  const maxBet = Number(market.max_bet || 10000);
  const feePct = Number(market.platform_fee_percent || 0);
  const fee = stakeNum * feePct / 100;
  const netStake = stakeNum - fee;
  const mult = Number(option.multiplier);
  const potential = netStake * mult;
  const canBet = stakeNum >= minBet && stakeNum <= maxBet && balance !== null && balance >= stakeNum;
  const referrer = getReferrer();

  const submit = async () => {
    if (!canBet) return;
    setBusy(true);
    try {
      const body = { option_id: option.option_id, token_type: 'usd', stake_amount: stakeNum };
      if (referrer) body.referrer_id = referrer;
      const { data } = await axios.post(`${API}/api/forecast/markets/${market.market_id}/bet`,
        body, { withCredentials: true });
      toast.success(`${t('forecast.bet_placed')} ${fmtUSD(data.potential_payout)}`);
      onSuccess();
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || '';
      toast.error(detail || t('forecast.bet_failed', { defaultValue: 'Échec du pari' }));
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-4"
         style={{ background: 'rgba(0,0,0,0.5)' }} onClick={onClose} data-testid="forecast-bet-modal">
      <div className="jp-card-elevated p-5 w-full max-w-md" onClick={(e) => e.stopPropagation()}>
        <h2 className="font-bold text-lg mb-1" style={{ color: 'var(--jp-text)' }}>{market.title}</h2>
        <p className="text-sm mb-4" style={{ color: 'var(--jp-text-muted)' }}>
          <strong>{option.label}</strong> · ×{mult.toFixed(2)}
        </p>

        <label className="block text-xs font-bold mb-1" style={{ color: 'var(--jp-text-muted)' }}>
          {t('forecast.stake')} (USD)
        </label>
        <input type="number" step="0.01" min={minBet} max={maxBet}
          value={stake} onChange={e => setStake(e.target.value)}
          placeholder={`${minBet} – ${maxBet}`}
          className="jp-input w-full mb-2" data-testid="forecast-bet-stake" autoFocus />

        <div className="text-xs mb-3" style={{ color: 'var(--jp-text-muted)' }}>
          {t('forecast.balance', { defaultValue: 'Solde' })}: <strong>{balance === null ? '…' : fmtUSD(balance)}</strong>
        </div>

        {balance !== null && balance <= 0 && (
          <div className="rounded-lg p-3 mb-3 text-xs" data-testid="forecast-no-balance"
               style={{ background: 'var(--jp-warning-subtle, #fff7ed)', color: 'var(--jp-warning, #b45309)' }}>
            {t('forecast.no_balance')}{' '}
            <button onClick={() => navigate('/wallet')} className="underline font-bold">
              {t('forecast.go_to_wallet')}
            </button>
          </div>
        )}

        <div className="space-y-1 text-xs mb-4 p-3 rounded-lg"
             style={{ background: 'var(--jp-surface-secondary)' }}>
          <Row label={t('forecast.fee', { defaultValue: 'Frais' }) + ` (${feePct}%)`} value={fmtUSD(fee)} />
          <Row label={t('forecast.net_stake', { defaultValue: 'Mise nette' })} value={fmtUSD(netStake)} />
          <Row label={t('forecast.potential_win')} value={<strong style={{ color: 'var(--jp-primary)' }}>{fmtUSD(potential)}</strong>} />
        </div>

        {referrer && (
          <div className="text-[11px] mb-3 p-2 rounded" data-testid="forecast-bet-ref-notice"
               style={{ background: 'rgba(124,58,237,0.08)', color: 'var(--jp-primary)' }}>
            🎁 {t('forecast.bet_via_friend', {
              defaultValue: 'Tu paries via le lien d\'un ami. Il gagnera {{pct}}% si tu gagnes.',
              pct: referralPct,
            })}
          </div>
        )}

        <div className="flex gap-2">
          <button onClick={onClose} className="jp-btn-ghost flex-1" data-testid="forecast-bet-cancel">
            {t('forecast.cancel', { defaultValue: 'Annuler' })}
          </button>
          <button onClick={submit} disabled={!canBet || busy}
                  className="jp-btn-primary flex-1 disabled:opacity-50" data-testid="forecast-bet-confirm">
            {busy ? '…' : t('forecast.confirm_bet', { defaultValue: 'Confirmer' })}
          </button>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }) {
  return (
    <div className="flex justify-between items-center">
      <span>{label}</span><span>{value}</span>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="p-2 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }}>
      <div className="text-[10px] uppercase font-bold" style={{ color: 'var(--jp-text-muted)' }}>{label}</div>
      <div className="text-lg font-bold mt-0.5" style={{ color: 'var(--jp-text)' }}>{value ?? '—'}</div>
    </div>
  );
}

function MyBetsList({ t }) {
  const [bets, setBets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [earnings, setEarnings] = useState(null);
  useEffect(() => {
    Promise.all([
      axios.get(`${API}/api/forecast/my-bets`, { withCredentials: true })
        .then(r => setBets(r.data?.bets || []))
        .catch(() => setBets([])),
      axios.get(`${API}/api/forecast/my-referrals`, { withCredentials: true })
        .then(r => setEarnings(r.data))
        .catch(() => {}),
    ]).finally(() => setLoading(false));
  }, []);
  if (loading) return <div className="text-center py-12" style={{ color: 'var(--jp-text-muted)' }}>…</div>;
  if (bets.length === 0) return (
    <div className="jp-card p-8 text-center" style={{ color: 'var(--jp-text-muted)' }}>
      {t('forecast.no_bets', { defaultValue: 'Aucun pari pour le moment.' })}
    </div>
  );
  const statusLabel = {
    placed:    { text: t('forecast.status_placed', { defaultValue: 'En cours' }),    color: 'var(--jp-primary)' },
    won:       { text: t('forecast.won'),     color: 'var(--jp-success, #15803d)' },
    lost:      { text: t('forecast.lost'),    color: 'var(--jp-text-muted)' },
    refunded:  { text: t('forecast.refunded'), color: 'var(--jp-warning, #b45309)' },
    cancelled: { text: t('forecast.cancelled', { defaultValue: 'Annulé' }), color: 'var(--jp-text-muted)' },
  };
  return (
    <div data-testid="forecast-my-bets-list">
      {/* iter241a-share — Referral earnings summary, shown above the bet history. */}
      {earnings && earnings.referrals_total > 0 && (
        <div className="jp-card-elevated p-4 mb-3" data-testid="forecast-my-referrals">
          <div className="text-xs font-bold uppercase mb-2" style={{ color: 'var(--jp-text-muted)' }}>
            🎁 {t('forecast.my_referrals_title', { defaultValue: 'Mes commissions de partage' })}
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <Stat label={t('forecast.followers', { defaultValue: 'Followers' })} value={earnings.unique_followers} />
            <Stat label={t('forecast.referrals_total', { defaultValue: 'Paris parrainés' })} value={earnings.referrals_total} />
            <Stat label={t('forecast.referrals_won', { defaultValue: 'Gagnés' })} value={earnings.referrals_won} />
            <Stat label={t('forecast.commission_earned', { defaultValue: 'Commissions' })}
                  value={<strong style={{ color: 'var(--jp-success, #15803d)' }}>{fmtUSD(earnings.commission_earned)}</strong>} />
          </div>
        </div>
      )}
      {bets.map(b => {
        const s = statusLabel[b.status] || statusLabel.placed;
        return (
          <div key={b.bet_id} className="jp-card p-3 mb-2" data-testid={`forecast-my-bet-${b.bet_id}`}>
            <div className="flex justify-between items-start gap-2">
              <div className="flex-1 min-w-0">
                <div className="text-[10px] uppercase font-bold" style={{ color: 'var(--jp-text-muted)' }}>
                  {t(`forecast.cat_${b.market_category}`, { defaultValue: b.market_category })}
                </div>
                <div className="text-sm font-bold truncate" style={{ color: 'var(--jp-text)' }}>{b.market_title}</div>
                <div className="text-xs mt-1" style={{ color: 'var(--jp-text-muted)' }}>
                  {b.option_label} · ×{Number(b.multiplier).toFixed(2)}
                </div>
              </div>
              <span className="text-[10px] font-bold px-2 py-0.5 rounded-full whitespace-nowrap"
                    style={{ color: s.color, background: 'var(--jp-surface-secondary)' }}>{s.text}</span>
            </div>
            <div className="flex justify-between text-xs mt-2 pt-2 border-t"
                 style={{ borderColor: 'var(--jp-border)', color: 'var(--jp-text-muted)' }}>
              <span>{t('forecast.stake')}: <strong>{fmtUSD(b.stake_amount)}</strong></span>
              {b.status === 'won' ? (
                <span>{t('forecast.won')}: <strong style={{ color: 'var(--jp-success, #15803d)' }}>+{fmtUSD(b.payout_amount)}</strong></span>
              ) : b.status === 'refunded' ? (
                <span>{t('forecast.refunded')}: <strong>{fmtUSD(b.payout_amount)}</strong></span>
              ) : (
                <span>{t('forecast.potential_win')}: <strong>{fmtUSD(b.potential_payout)}</strong></span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
