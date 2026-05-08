/**
 * WheelFortuneCasino — iter83 premium engagement wheel (option b).
 *
 *   - 8 slots JAPAP (Jackpot / Perdu / +XX) — strict business logic unchanged
 *   - Backend-controlled landing (we only animate towards `landingSlot`)
 *   - Heavy 3D premium skin inspired by a physical casino wheel :
 *       • gold/chrome multi-layer rim with rivets
 *       • slot cells with vertical gradient (relief)
 *       • realistic 4-arm chrome spinner + dome central
 *       • specular highlight sweep during spin
 *       • layered drop-shadows + radial ambient glow
 *   - Brand palette respected (bleu marine #0F056B / or #FFD700 / orange #F7931A)
 *   - Pure SVG + CSS — zero new dependencies
 */
import { useEffect, useRef, useState, useId } from 'react';

const SIZE = 340;
const CENTER = SIZE / 2;
const OUTER_R = CENTER - 6;   // outermost rim
const RIM_R = OUTER_R - 14;   // inner edge of the gold rim
const SLOT_R = RIM_R - 4;     // outer radius of slot arcs
const INNER_R = 66;           // radius of the central dome

/* ── Synthetic tick sound generator (no deps, no asset download).
 * We schedule N short oscillator bursts whose density decays over the
 * spin duration so it *sounds* like a slowing ball — tick…tick..tick. */
function playSpinTicks(durationMs = 4600) {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return () => {};
    const ctx = new Ctx();
    if (ctx.state === 'suspended') ctx.resume();
    const start = ctx.currentTime;
    const end = start + durationMs / 1000;
    // Ease-out density : more ticks early, spaced out at the end
    let t = start;
    let gap = 0.05;
    while (t < end) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'square';
      osc.frequency.setValueAtTime(1500 + Math.random() * 400, t);
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.linearRampToValueAtTime(0.08, t + 0.005);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.04);
      osc.connect(gain).connect(ctx.destination);
      osc.start(t); osc.stop(t + 0.05);
      t += gap;
      // decelerate: increase gap exponentially toward the end
      gap = Math.min(0.5, gap * 1.06);
    }
    return () => { try { ctx.close(); } catch (_) {} };
  } catch { return () => {}; }
}
function playLandingThud(jackpot = false) {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const t = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = jackpot ? 'sine' : 'triangle';
    osc.frequency.setValueAtTime(jackpot ? 880 : 220, t);
    osc.frequency.exponentialRampToValueAtTime(jackpot ? 1320 : 110, t + 0.35);
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.linearRampToValueAtTime(jackpot ? 0.22 : 0.16, t + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.5);
    osc.connect(gain).connect(ctx.destination);
    osc.start(t); osc.stop(t + 0.55);
    setTimeout(() => { try { ctx.close(); } catch (_) {} }, 800);
  } catch { /* silent */ }
}

function polar(cx, cy, r, angleDeg) {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}
function arcPath(cx, cy, rOut, rIn, start, end) {
  const sOut = polar(cx, cy, rOut, end);
  const eOut = polar(cx, cy, rOut, start);
  const sIn = polar(cx, cy, rIn, start);
  const eIn = polar(cx, cy, rIn, end);
  const large = end - start <= 180 ? 0 : 1;
  return [
    `M ${sIn.x} ${sIn.y}`,
    `L ${eOut.x} ${eOut.y}`,
    `A ${rOut} ${rOut} 0 ${large} 0 ${sOut.x} ${sOut.y}`,
    `L ${eIn.x} ${eIn.y}`,
    `A ${rIn} ${rIn} 0 ${large} 1 ${sIn.x} ${sIn.y}`,
    'Z',
  ].join(' ');
}

export default function WheelFortuneCasino({
  slots = [], isSpinning = false, landingSlot = null,
  nearMiss = false, boostActive = false, onSpinEnd = () => {},
}) {
  const [angle, setAngle] = useState(0);
  const angleRef = useRef(0);
  const setAngleSafe = (v) => { angleRef.current = v; setAngle(v); };
  const [transition, setTransition] = useState('none');
  const [sweep, setSweep] = useState(false);
  const [confetti, setConfetti] = useState(false);
  const [glowSlot, setGlowSlot] = useState(null);
  const [muted, setMuted] = useState(
    typeof window !== 'undefined' && localStorage.getItem('wheel_muted') === '1'
  );
  const onEndRef = useRef(onSpinEnd);
  onEndRef.current = onSpinEnd;
  const timers = useRef([]);
  const audioCleanup = useRef(() => {});
  const uid = useId().replace(/:/g, '_');

  const n = slots.length || 8;
  const slice = 360 / n;

  const toggleMute = () => {
    setMuted((m) => {
      const next = !m;
      try { localStorage.setItem('wheel_muted', next ? '1' : '0'); } catch (_) {}
      return next;
    });
  };

  useEffect(() => () => {
    timers.current.forEach(clearTimeout);
    audioCleanup.current();
  }, []);

  useEffect(() => {
    if (!isSpinning || landingSlot === null) return;
    timers.current.forEach(clearTimeout);
    timers.current = [];
    audioCleanup.current();

    // CUMULATIVE ROTATION FIX (iter113):
    // The wheel angle is *additive*. We compute the smallest forward delta
    // (0..360) that lands the slice center under the top pointer, then add
    // 6 full turns. This guarantees the wheel ALWAYS spins forward, multiple
    // turns, regardless of where it was on the previous spin. Previously
    // `setAngle(360*6 - X)` made the 2nd spin barely move because the new
    // absolute angle was sometimes < current angle.
    const fullTurns = 6;
    const targetSliceAngle = -(landingSlot * slice + slice / 2); // negative => clockwise to slot
    // Normalise current angle to [0,360) and target to [0,360)
    const norm = (deg) => ((deg % 360) + 360) % 360;
    const currentAngle = angleRef.current;
    const currentMod = norm(currentAngle);
    const targetMod = norm(targetSliceAngle);
    const delta = norm(targetMod - currentMod); // forward distance only
    const baseAngle = currentAngle + fullTurns * 360 + delta;
    const landed = slots[landingSlot] || {};
    const isJackpot = !!landed.is_jackpot;

    setSweep(true);

    const hapticLand = () => {
      try {
        if (navigator.vibrate) {
          navigator.vibrate(isJackpot ? [60, 50, 120, 50, 220] : [35, 40, 70]);
        }
      } catch (_) { /* silent */ }
    };

    if (nearMiss) {
      // Near-miss: stop briefly on jackpot (slot 0) THEN nudge forward to the real landing slot.
      const targetJackpotMod = norm(-(0 * slice + slice / 2));
      const deltaJackpot = norm(targetJackpotMod - currentMod);
      const jackpotTarget = currentAngle + fullTurns * 360 + deltaJackpot - 6;
      const dur = 4300;
      setTransition(`transform ${dur / 1000}s cubic-bezier(0.17, 0.67, 0.35, 0.99)`);
      setAngleSafe(jackpotTarget);
      if (!muted) audioCleanup.current = playSpinTicks(dur + 1300);
      timers.current.push(setTimeout(() => {
        setTransition('transform 1.2s cubic-bezier(0.33, 1, 0.68, 1)');
        const finalMod = norm(-(landingSlot * slice + slice / 2));
        const nudgeDelta = norm(finalMod - norm(jackpotTarget));
        setAngleSafe(jackpotTarget + 360 + nudgeDelta);
      }, dur + 100));
      timers.current.push(setTimeout(() => {
        setSweep(false);
        setGlowSlot(landingSlot);
        setTimeout(() => setGlowSlot(null), 2200);
        hapticLand();
        if (!muted) playLandingThud(isJackpot);
        if (isJackpot) { setConfetti(true); setTimeout(() => setConfetti(false), 2400); }
        onEndRef.current();
      }, 5700));
    } else {
      const dur = 4600;
      setTransition(`transform ${dur / 1000}s cubic-bezier(0.17, 0.67, 0.35, 0.99)`);
      setAngleSafe(baseAngle);
      if (!muted) audioCleanup.current = playSpinTicks(dur);
      timers.current.push(setTimeout(() => {
        setSweep(false);
        // Glow + winning halo on the landed slot (won or perdu unified UX).
        setGlowSlot(landingSlot);
        setTimeout(() => setGlowSlot(null), 2200);
        hapticLand();
        if (!muted) playLandingThud(isJackpot);
        if (isJackpot) { setConfetti(true); setTimeout(() => setConfetti(false), 2400); }
        onEndRef.current();
      }, dur + 100));
    }
  }, [isSpinning, landingSlot, nearMiss, slice, slots, muted]);

  /* ── shared gradient ids (namespaced so several wheels can coexist) ── */
  const G = {
    gold:  `g-gold-${uid}`,
    chrome:`g-chrome-${uid}`,
    rim:   `g-rim-${uid}`,
    dome:  `g-dome-${uid}`,
    cellR: `g-cellR-${uid}`,   // red slot
    cellB: `g-cellB-${uid}`,   // dark slot
    cellJ: `g-cellJ-${uid}`,   // jackpot
    cellG: `g-cellG-${uid}`,   // green slot
    glow:  `g-glow-${uid}`,
    spec:  `g-spec-${uid}`,
  };

  return (
    <div className="relative select-none" style={{ width: SIZE, height: SIZE }}
         data-testid="wheel-fortune-casino">
      {/* Ambient radial glow under the wheel */}
      <div className="absolute inset-[-30px] rounded-full pointer-events-none"
           data-testid={boostActive ? 'wheel-boost-aura' : 'wheel-aura'}
           style={{
             background: boostActive
               ? 'radial-gradient(circle, rgba(255,215,0,0.55) 0%, rgba(247,147,26,0.30) 40%, rgba(224,28,46,0.10) 65%, transparent 75%)'
               : 'radial-gradient(circle, rgba(255,215,0,0.25) 0%, rgba(15,5,107,0.08) 45%, transparent 70%)',
             filter: boostActive ? 'blur(10px)' : 'blur(6px)',
             animation: boostActive ? 'wheelBoostAura 2.4s ease-in-out infinite' : undefined,
           }} />

      {/* Decorative pin ring (static — does not rotate with the wheel) */}
      <svg width={SIZE} height={SIZE} className="absolute inset-0 pointer-events-none">
        <defs>
          <radialGradient id={G.chrome} cx="30%" cy="30%" r="80%">
            <stop offset="0%" stopColor="#ffffff" />
            <stop offset="45%" stopColor="#e8e8ea" />
            <stop offset="100%" stopColor="#6b6f76" />
          </radialGradient>
        </defs>
        {Array.from({ length: 24 }).map((_, i) => {
          const a = (i / 24) * 360;
          const p = polar(CENTER, CENTER, OUTER_R - 4, a);
          return (
            <circle key={i} cx={p.x} cy={p.y} r="3.2"
                    fill={`url(#${G.chrome})`} stroke="#7a5a00" strokeWidth="0.6" />
          );
        })}
      </svg>

      {/* Rotating wheel group */}
      <svg
        width={SIZE} height={SIZE}
        style={{
          transformOrigin: '50% 50%',
          transform: `rotate(${angle}deg) translateZ(0)`,
          transition,
          // iter95-perf — promote to a GPU compositor layer so the long
          // 4.6s rotation runs on the compositor thread instead of the
          // main thread (which was repainting all gradients every frame
          // → visible "lag" on mid-range Android devices).
          willChange: 'transform',
          backfaceVisibility: 'hidden',
          filter: 'drop-shadow(0 18px 28px rgba(0,0,0,0.55)) drop-shadow(0 4px 8px rgba(0,0,0,0.35))',
        }}>
        <defs>
          {/* Outer rim: warm gold with chrome highlight */}
          <radialGradient id={G.rim} cx="50%" cy="42%" r="58%">
            <stop offset="0%"  stopColor="#fff6c4" />
            <stop offset="35%" stopColor="#ffd34d" />
            <stop offset="65%" stopColor="#b8860b" />
            <stop offset="100%" stopColor="#5a3f00" />
          </radialGradient>
          <radialGradient id={G.gold} cx="50%" cy="38%" r="60%">
            <stop offset="0%"  stopColor="#fff1a8" />
            <stop offset="50%" stopColor="#FFD700" />
            <stop offset="100%" stopColor="#a8740a" />
          </radialGradient>
          {/* Slot cells — vertical gradient from top (light) to bottom (dark) to suggest relief */}
          <linearGradient id={G.cellR} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"  stopColor="#F94A5C" />
            <stop offset="55%" stopColor="#E01C2E" />
            <stop offset="100%" stopColor="#8a0d1a" />
          </linearGradient>
          <linearGradient id={G.cellB} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"  stopColor="#2a2a2e" />
            <stop offset="55%" stopColor="#141417" />
            <stop offset="100%" stopColor="#050505" />
          </linearGradient>
          <linearGradient id={G.cellG} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"  stopColor="#34D399" />
            <stop offset="55%" stopColor="#10B981" />
            <stop offset="100%" stopColor="#04513d" />
          </linearGradient>
          <radialGradient id={G.cellJ} cx="50%" cy="30%" r="80%">
            <stop offset="0%"  stopColor="#fff2a8" />
            <stop offset="45%" stopColor="#FFD700" />
            <stop offset="100%" stopColor="#8a6100" />
          </radialGradient>
          {/* Central dome — amber shine like your reference */}
          <radialGradient id={G.dome} cx="42%" cy="32%" r="70%">
            <stop offset="0%"  stopColor="#fff6c4" />
            <stop offset="35%" stopColor="#F7931A" />
            <stop offset="100%" stopColor="#2a1a00" />
          </radialGradient>
          {/* Specular sweep during spin */}
          <linearGradient id={G.spec} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%"   stopColor="rgba(255,255,255,0)" />
            <stop offset="50%"  stopColor="rgba(255,255,255,0.55)" />
            <stop offset="100%" stopColor="rgba(255,255,255,0)" />
          </linearGradient>
        </defs>

        {/* Outer bezel ring (thick gold) */}
        <circle cx={CENTER} cy={CENTER} r={OUTER_R}
                fill={`url(#${G.rim})`}
                stroke="#3a2a00" strokeWidth="1.5" />
        {/* Inner rim highlight */}
        <circle cx={CENTER} cy={CENTER} r={RIM_R + 6}
                fill="none" stroke={`url(#${G.gold})`} strokeWidth="3" opacity="0.9" />

        {/* Slot cells */}
        {slots.map((s, i) => {
          const start = i * slice;
          const end = (i + 1) * slice;
          const mid = start + slice / 2;
          const fill = s.is_jackpot
            ? `url(#${G.cellJ})`
            : /(vert|green|10B981)/i.test(s.color || '')
            ? `url(#${G.cellG})`
            : /(E01|red|rouge|F94)/i.test(s.color || '')
            ? `url(#${G.cellR})`
            : `url(#${G.cellB})`;
          const labelPos = polar(CENTER, CENTER, SLOT_R * 0.7, mid);
          return (
            <g key={s.idx}>
              <path d={arcPath(CENTER, CENTER, SLOT_R, INNER_R + 2, start, end)}
                    fill={fill}
                    stroke="#FFD700" strokeWidth="1.4" />
              {/* Subtle inner separator line (gold) for depth */}
              <line
                x1={polar(CENTER, CENTER, INNER_R + 4, start).x}
                y1={polar(CENTER, CENTER, INNER_R + 4, start).y}
                x2={polar(CENTER, CENTER, SLOT_R - 2, start).x}
                y2={polar(CENTER, CENTER, SLOT_R - 2, start).y}
                stroke="rgba(255,215,0,0.55)" strokeWidth="0.8" />
              <text
                x={labelPos.x} y={labelPos.y}
                fill={s.is_jackpot ? '#3a1a00' : '#fff'}
                fontSize={s.is_jackpot ? 18 : 15}
                fontWeight={900}
                textAnchor="middle"
                dominantBaseline="central"
                transform={`rotate(${mid}, ${labelPos.x}, ${labelPos.y})`}
                style={{
                  fontFamily: 'Outfit, system-ui, sans-serif',
                  textShadow: s.is_jackpot
                    ? '0 1px 2px rgba(255,255,255,0.6)'
                    : '0 2px 4px rgba(0,0,0,0.85)',
                  letterSpacing: '0.5px',
                }}>
                {s.label}
              </text>
            </g>
          );
        })}

        {/* Glow halo on the landed slot (iter113 — wins + perdus get a clear visual landing) */}
        {glowSlot !== null && slots[glowSlot] && (() => {
          const start = glowSlot * slice;
          const end = (glowSlot + 1) * slice;
          const isWin = (slots[glowSlot].base_points || 0) > 0;
          const haloColor = isWin ? '#FFD700' : '#FF4D4D';
          return (
            <path d={arcPath(CENTER, CENTER, SLOT_R, INNER_R + 2, start, end)}
                  fill="none"
                  stroke={haloColor}
                  strokeWidth="6"
                  style={{
                    filter: `drop-shadow(0 0 12px ${haloColor}) drop-shadow(0 0 24px ${haloColor})`,
                    animation: 'wheelSlotGlow 1.6s ease-out 2',
                  }}
                  data-testid="wheel-slot-glow" />
          );
        })()}

        {/* Central amber dome (like the reference photo) */}
        <circle cx={CENTER} cy={CENTER} r={INNER_R}
                fill={`url(#${G.dome})`}
                stroke="#a06200" strokeWidth="1.5" />
        {/* Dome specular highlight */}
        <ellipse cx={CENTER - 14} cy={CENTER - 18} rx={INNER_R * 0.55} ry={INNER_R * 0.35}
                 fill="rgba(255,255,255,0.55)" />

        {/* Chrome 4-arm spinner (like the reference) */}
        <g>
          {[0, 90, 180, 270].map((a) => (
            <g key={a} transform={`rotate(${a} ${CENTER} ${CENTER})`}>
              <rect x={CENTER - 4} y={CENTER - INNER_R + 8} width={8} height={INNER_R - 24}
                    rx="3"
                    fill={`url(#${G.chrome})`}
                    stroke="#333" strokeWidth="0.6" />
              <circle cx={CENTER} cy={CENTER - INNER_R + 10} r="6"
                      fill={`url(#${G.chrome})`} stroke="#333" strokeWidth="0.7" />
            </g>
          ))}
          <circle cx={CENTER} cy={CENTER} r="10" fill={`url(#${G.chrome})`}
                  stroke="#333" strokeWidth="0.8" />
          <circle cx={CENTER - 3} cy={CENTER - 3} r="3" fill="rgba(255,255,255,0.85)" />
        </g>

        {/* Specular sweep visible only while spinning */}
        {sweep && (
          <ellipse cx={CENTER} cy={CENTER} rx={OUTER_R - 6} ry={18}
                   fill={`url(#${G.spec})`} opacity="0.55"
                   style={{
                     transformOrigin: '50% 50%',
                     animation: 'wheelSweep 1.4s ease-in-out infinite',
                   }} />
        )}
      </svg>

      {/* Fixed top pointer (chrome + gold) */}
      <div className="absolute left-1/2 -top-3 -translate-x-1/2 z-10"
           style={{
             width: 0, height: 0,
             borderLeft: '15px solid transparent',
             borderRight: '15px solid transparent',
             borderTop: '30px solid #FFD700',
             filter: 'drop-shadow(0 4px 6px rgba(0,0,0,0.6))',
           }} />
      <div className="absolute left-1/2 -top-8 -translate-x-1/2 z-10 rounded-full"
           style={{ width: 22, height: 22,
                    background: 'radial-gradient(circle at 35% 30%, #fff6c4, #FFD700 55%, #a06200)',
                    boxShadow: '0 0 12px rgba(255,215,0,0.85), inset 0 -3px 6px rgba(0,0,0,0.35)' }} />

      {/* Mute toggle (bottom right of the wheel container) */}
      <button onClick={toggleMute} data-testid="wheel-mute-toggle"
              aria-label={muted ? 'Activer le son' : 'Couper le son'}
              className="absolute bottom-1 right-1 z-20 w-9 h-9 rounded-full flex items-center justify-center transition-transform hover:scale-110 active:scale-95"
              style={{
                background: 'rgba(15,5,107,0.85)',
                border: '1.5px solid #FFD700',
                boxShadow: '0 4px 10px rgba(0,0,0,0.4)',
              }}>
        <span className="text-base" style={{ color: '#FFD700' }}>
          {muted ? '🔇' : '🔊'}
        </span>
      </button>

      {/* Gold confetti burst on Jackpot */}
      {confetti && (
        <div className="absolute inset-0 pointer-events-none overflow-visible z-[15]"
             data-testid="wheel-confetti">
          {Array.from({ length: 28 }).map((_, i) => {
            const angle = (Math.random() * 360);
            const dist = 120 + Math.random() * 140;
            const dx = Math.cos(angle * Math.PI / 180) * dist;
            const dy = Math.sin(angle * Math.PI / 180) * dist;
            const rot = Math.random() * 720 - 360;
            const size = 6 + Math.random() * 8;
            const delay = Math.random() * 0.15;
            const palette = ['#FFD700', '#F7931A', '#FFFFFF', '#FFC857', '#E01C2E'];
            const color = palette[i % palette.length];
            const isStar = i % 3 === 0;
            return (
              <span key={i}
                    className="absolute top-1/2 left-1/2"
                    style={{
                      width: size, height: isStar ? size : size * 0.45,
                      background: color,
                      borderRadius: isStar ? '50%' : '2px',
                      boxShadow: `0 0 ${size}px ${color}`,
                      animation: `wheelConfetti 2.4s ${delay}s cubic-bezier(0.2, 0.9, 0.3, 1) forwards`,
                      '--dx': `${dx}px`, '--dy': `${dy}px`, '--rot': `${rot}deg`,
                    }} />
            );
          })}
        </div>
      )}

      {/* Inline keyframes */}
      <style>{`
        @keyframes wheelSweep {
          0%   { transform: rotate(0deg)   scaleX(0.8); opacity: 0.1; }
          50%  { transform: rotate(180deg) scaleX(1.0); opacity: 0.65; }
          100% { transform: rotate(360deg) scaleX(0.8); opacity: 0.1; }
        }
        @keyframes wheelConfetti {
          0%   { transform: translate(-50%, -50%) rotate(0deg) scale(0.4); opacity: 1; }
          30%  { transform: translate(calc(-50% + var(--dx) * 0.6), calc(-50% + var(--dy) * 0.6)) rotate(calc(var(--rot) * 0.5)) scale(1.1); opacity: 1; }
          100% { transform: translate(calc(-50% + var(--dx)), calc(-50% + var(--dy) + 80px)) rotate(var(--rot)) scale(0.6); opacity: 0; }
        }
        @keyframes wheelSlotGlow {
          0%   { opacity: 0; stroke-width: 2; }
          25%  { opacity: 1; stroke-width: 7; }
          100% { opacity: 0; stroke-width: 2; }
        }
        @keyframes wheelBoostAura {
          0%, 100% { opacity: 0.85; transform: scale(1); }
          50%      { opacity: 1.00; transform: scale(1.04); }
        }
      `}</style>
    </div>
  );
}
