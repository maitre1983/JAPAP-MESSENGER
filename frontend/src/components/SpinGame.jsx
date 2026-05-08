import { useEffect, useState, useRef } from 'react';
import axios from 'axios';
import { GameController, X, Spinner, Trophy } from '@phosphor-icons/react';
import { toast } from 'sonner';

const API = process.env.REACT_APP_BACKEND_URL;

/**
 * JAPAP Spin — user-facing game component.
 * Config (enabled, paid mode, cost, rewards) is fetched from
 * /api/games/spin/config. When paid, the wallet is debited server-side.
 */
export default function SpinGame({ onClose, onReward }) {
  const [cfg, setCfg] = useState(null);
  const [spinning, setSpinning] = useState(false);
  const [result, setResult] = useState(null);
  const [rotation, setRotation] = useState(0);
  const wheelRef = useRef(null);

  useEffect(() => {
    axios.get(`${API}/api/games/spin/config`, { withCredentials: true })
      .then(r => setCfg(r.data)).catch(() => setCfg({ enabled: false }));
  }, []);

  const play = async () => {
    setResult(null); setSpinning(true);
    try {
      const start = Date.now();
      const { data } = await axios.post(`${API}/api/games/spin`, {}, { withCredentials: true });
      const elapsed = Date.now() - start;
      const spins = 5 + Math.floor(Math.random() * 3);
      const slotCount = (cfg.rewards || []).length || 8;
      const matched = (cfg.rewards || []).findIndex(r => Number(r.amount) === Number(data.prize_slot));
      const targetSlot = matched >= 0 ? matched : 0;
      const degPer = 360 / slotCount;
      const finalDeg = spins * 360 + (slotCount - targetSlot) * degPer;
      setRotation(r => r + finalDeg);
      // Wait for animation before showing result
      const delay = Math.max(0, 2800 - elapsed);
      setTimeout(() => {
        setSpinning(false);
        setResult(data);
        if (Number(data.reward) > 0) {
          toast.success(`Vous avez gagné ${data.reward} XAF ! 🎉`);
          onReward && onReward();
        }
      }, delay);
    } catch (e) {
      setSpinning(false);
      const detail = e.response?.data?.detail || 'Erreur';
      if (String(detail).startsWith('KYC_REQUIRED')) {
        toast.error('Veuillez vérifier votre identité (KYC) avant de jouer à un mode payant.');
      } else {
        toast.error(detail);
      }
    }
  };

  if (!cfg) return null;
  if (!cfg.enabled) {
    return (
      <Shell onClose={onClose}>
        <p className="text-center text-sm" style={{ color: 'var(--jp-text-muted)' }}>JAPAP Spin est temporairement indisponible.</p>
      </Shell>
    );
  }

  const rewards = cfg.rewards || [];
  const slotCount = rewards.length || 8;
  const colors = ['#E01C2E', '#0F056B', '#10B981', '#F59E0B', '#8B5CF6', '#EC4899', '#06B6D4', '#EAB308'];

  return (
    <Shell onClose={onClose} wide>
      <div className="text-center mb-3">
        <div className="inline-flex items-center gap-2 text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--jp-text-muted)' }}>
          <GameController size={16} weight="duotone" /> JAPAP SPIN
        </div>
        <h3 className="font-['Outfit'] text-2xl font-extrabold mt-1" style={{ color: 'var(--jp-text)' }}>Tentez votre chance</h3>
        {cfg.is_paid && Number(cfg.cost_xaf) > 0 && (
          <p className="text-xs mt-1" style={{ color: 'var(--jp-text-muted)' }}>
            Coût par spin : <strong style={{ color: 'var(--jp-primary)' }}>{cfg.cost_xaf} XAF</strong> · Max {cfg.max_daily_plays}/jour
          </p>
        )}
      </div>

      <div className="relative w-64 h-64 mx-auto my-4">
        {/* Pointer */}
        <div className="absolute -top-1 left-1/2 -translate-x-1/2 z-10"
          style={{ width: 0, height: 0, borderLeft: '14px solid transparent', borderRight: '14px solid transparent', borderTop: '22px solid var(--jp-primary)' }} />
        <div ref={wheelRef} className="w-full h-full rounded-full overflow-hidden shadow-xl transition-transform"
          style={{ transform: `rotate(${rotation}deg)`, transitionDuration: spinning ? '2.8s' : '0s', transitionTimingFunction: 'cubic-bezier(0.12, 0.8, 0.28, 1)', background: `conic-gradient(${rewards.map((r, i) => `${colors[i % colors.length]} ${(i / slotCount) * 360}deg ${((i + 1) / slotCount) * 360}deg`).join(',')})` }}
          data-testid="spin-wheel">
          {rewards.map((r, i) => {
            const angle = (360 / slotCount) * i + (180 / slotCount);
            return (
              <div key={i} className="absolute top-1/2 left-1/2 text-white font-['Outfit'] font-bold text-sm"
                style={{ transform: `translate(-50%, -50%) rotate(${angle}deg) translateY(-85px)`, textShadow: '0 1px 3px rgba(0,0,0,0.6)' }}>
                {r.amount || 0}
              </div>
            );
          })}
        </div>
        {/* Center hub */}
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="w-14 h-14 rounded-full bg-white shadow-lg flex items-center justify-center">
            <Trophy size={22} weight="duotone" style={{ color: 'var(--jp-primary)' }} />
          </div>
        </div>
      </div>

      <button onClick={play} disabled={spinning} data-testid="spin-play-btn"
        className="jp-btn jp-btn-primary w-full mt-2"
        style={{ background: 'linear-gradient(135deg, var(--jp-primary), var(--jp-secondary))' }}>
        {spinning ? (<><Spinner size={16} className="animate-spin" /> Rotation…</>) : (
          cfg.is_paid && Number(cfg.cost_xaf) > 0 ? `Jouer (${cfg.cost_xaf} XAF)` : 'Jouer gratuitement'
        )}
      </button>

      {result && !spinning && (
        <div className="mt-4 p-3 rounded-lg text-center" style={{ background: Number(result.reward) > 0 ? 'var(--jp-success-light)' : 'var(--jp-surface-secondary)' }} data-testid="spin-result">
          <div className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--jp-text-muted)' }}>
            {Number(result.reward) > 0 ? '🎉 Gagné' : 'Pas cette fois'}
          </div>
          <div className="font-['Outfit'] text-2xl font-extrabold" style={{ color: Number(result.reward) > 0 ? 'var(--jp-success)' : 'var(--jp-text)' }}>
            {result.reward} XAF
          </div>
        </div>
      )}
    </Shell>
  );
}

function Shell({ children, onClose, wide }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.6)' }}>
      <div className={`jp-card-elevated ${wide ? 'max-w-sm' : 'max-w-xs'} w-full p-5 jp-animate-scaleIn relative`}>
        <button onClick={onClose} className="absolute top-3 right-3 p-1 rounded-lg" style={{ color: 'var(--jp-text-muted)' }} data-testid="spin-close"><X size={18} /></button>
        {children}
      </div>
    </div>
  );
}
