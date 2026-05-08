/**
 * MathCaptcha — iter141ter
 *
 * Replaces the Cloudflare Turnstile widget with a friendly arithmetic
 * challenge ("3 + 7 = ?") that humans answer in <2s. The backend signs a
 * stateless HMAC token containing the answer; the frontend just forwards
 * it back along the user's response.
 *
 * Props:
 *   - onChange({ captcha_id, captcha_answer })  fired on every change so the
 *                                                parent form stays in sync.
 *   - autoFocus  optional, focus the input on mount.
 *
 * Public ref API:
 *   ref.current.refresh()  — fetch a new question (call this after a 4xx
 *                            error so the user gets a fresh problem).
 */
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import axios from 'axios';
import { ArrowsClockwise } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

const MathCaptcha = forwardRef(function MathCaptcha(
  { onChange, autoFocus = false, label = 'Vérification rapide', helper = 'Réponds au calcul pour continuer', theme = 'dark' },
  ref,
) {
  const [captchaId, setCaptchaId] = useState('');
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [loading, setLoading] = useState(false);
  const [silent, setSilent] = useState(false);  // iter141quater — skip render if returning human
  // iter237 — Track whether the captcha endpoint is unreachable so we can
  // show a friendly inline message instead of the bare "Indisponible".
  const [unreachable, setUnreachable] = useState(false);
  const inputRef = useRef(null);
  const isLight = theme === 'light';

  const fetchCaptcha = async (attempt = 0) => {
    setLoading(true);
    setAnswer('');
    try {
      const { data } = await axios.get(`${API}/api/auth/captcha`, { withCredentials: true });
      setCaptchaId(data.captcha_id || '');
      setQuestion(data.question || '');
      setUnreachable(false);
      // iter141quater — server tells us a captcha is not required when
      // the device already presents a valid `japap_human` cookie. We
      // still keep the captcha_id loaded as fallback if the server
      // changes its mind (e.g., cookie expires between fetch & submit).
      // iter237b — if `disabled: true`, captcha is globally OFF (kill switch);
      // we signal silent so the form skips the challenge entirely.
      const isSilent = (data.required === false) || data.disabled === true;
      setSilent(isSilent);
      onChange?.({
        captcha_id: data.captcha_id || '',
        captcha_answer: isSilent ? 'silent' : '',  // sentinel — backend ignores when cookie is valid
      });
    } catch (_) {
      // iter237 — Silent retry up to 3 times with backoff before giving up.
      // Avoids flashing "Indisponible" on transient 5xx blips.
      if (attempt < 2) {
        setTimeout(() => { fetchCaptcha(attempt + 1); }, 1500 * (attempt + 1));
        return;
      }
      // iter237b — After 3 attempts, render a friendly placeholder AND tell
      // the parent form to allow submission (server will reject if it really
      // needs a captcha; but the backend kill switch + cookie path will pass).
      setCaptchaId('');
      setQuestion('');
      setSilent(false);
      setUnreachable(true);
      onChange?.({ captcha_id: '', captcha_answer: 'unreachable' });
    } finally { setLoading(false); }
  };

  useEffect(() => { fetchCaptcha(); /* eslint-disable-next-line */ }, []);

  useImperativeHandle(ref, () => ({
    refresh: fetchCaptcha,
  }));

  const onAnswerChange = (e) => {
    // Numbers + minus only — strip everything else for forgiveness.
    const v = e.target.value.replace(/[^\d-]/g, '').slice(0, 4);
    setAnswer(v);
    onChange?.({ captcha_id: captchaId, captcha_answer: v });
  };

  // Theme-aware classes/styles
  const labelColor   = isLight ? 'text-slate-600' : 'text-white/70';
  const refreshColor = isLight ? 'text-slate-500 hover:text-slate-700' : 'text-white/70 hover:text-white';
  const helperColor  = isLight ? 'text-slate-500' : 'text-white/50';
  const boxBg = isLight
    ? { background: 'rgba(15,5,107,0.04)', border: '1px solid rgba(15,5,107,0.18)' }
    : { background: 'rgba(255,255,255,0.15)', backdropFilter: 'blur(8px)', border: '1px solid rgba(255,255,255,0.25)' };
  const questionColor = isLight ? 'text-[#0F056B]' : 'text-white';

  return (
    <div className="w-full" data-testid="math-captcha">
      {silent ? (
        // iter141quater — caller is a known human (japap_human cookie),
        // so we render a tiny "trusted" badge instead of the challenge.
        <div className="flex items-center gap-2 px-3 py-2 rounded-full"
             data-testid="math-captcha-trusted"
             style={{
               background: isLight ? 'rgba(34,197,94,0.08)' : 'rgba(34,197,94,0.15)',
               border: `1px solid ${isLight ? 'rgba(34,197,94,0.30)' : 'rgba(34,197,94,0.40)'}`,
             }}>
          <span style={{ fontSize: 14 }}>✅</span>
          <span className={`text-[11px] font-['Manrope'] ${isLight ? 'text-emerald-700' : 'text-emerald-200'}`}>
            Appareil reconnu — vérification accélérée
          </span>
        </div>
      ) : unreachable ? (
        // iter237 — Friendly fallback when /api/auth/captcha is unreachable
        // (transient 5xx, network blip…). NOT a hard error — the user can
        // try again with the refresh button below.
        <div className="w-full" data-testid="math-captcha-unreachable">
          <div className="flex items-center justify-between mb-1">
            <span className={`text-[11px] font-['Manrope'] ${labelColor}`}>{label}</span>
            <button type="button"
                    onClick={() => fetchCaptcha(0)}
                    disabled={loading}
                    data-testid="math-captcha-retry"
                    className={`text-[11px] flex items-center gap-1 disabled:opacity-50 ${refreshColor}`}>
              <ArrowsClockwise size={12} weight="bold" />
              Réessayer
            </button>
          </div>
          <div className="rounded-2xl px-4 py-3 text-xs font-['Manrope']"
               style={{ background: isLight ? 'rgba(245, 158, 11, 0.08)' : 'rgba(245, 158, 11, 0.15)',
                        border: `1px solid ${isLight ? 'rgba(245, 158, 11, 0.30)' : 'rgba(245, 158, 11, 0.40)'}`,
                        color: isLight ? '#92400E' : '#FCD34D' }}>
            Vérification temporairement indisponible. Réessaie dans quelques secondes ou utilise le bouton « Réessayer » ci-dessus.
          </div>
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between mb-1">
            <span className={`text-[11px] font-['Manrope'] ${labelColor}`}>{label}</span>
            <button type="button"
                    onClick={fetchCaptcha}
                    disabled={loading}
                    data-testid="math-captcha-refresh"
                    className={`text-[11px] flex items-center gap-1 disabled:opacity-50 ${refreshColor}`}>
              <ArrowsClockwise size={12} weight="bold" />
              Nouvelle question
            </button>
          </div>
          <div className="flex items-center gap-2 rounded-full px-4 py-3" style={boxBg}>
            <div className={`flex-1 font-['Outfit'] text-base font-bold ${questionColor}`}
                 data-testid="math-captcha-question"
                 aria-label="Question">
              {loading ? '…' : (question ? `${question} =` : 'Préparation…')}
            </div>
            <input ref={inputRef}
                   data-testid="math-captcha-answer"
                   type="text"
                   inputMode="numeric"
                   pattern="-?[0-9]*"
                   autoComplete="off"
                   autoCorrect="off"
                   autoFocus={autoFocus}
                   value={answer}
                   onChange={onAnswerChange}
                   placeholder="?"
                   aria-label="Votre réponse"
                   className="w-16 px-2 py-1 rounded-md text-center font-['Outfit'] text-base font-bold text-[#0F056B] outline-none"
                   style={{ background: '#FFD700' }} />
          </div>
          <p className={`text-[10px] mt-1 ml-1 ${helperColor}`}>{helper}</p>
        </>
      )}
    </div>
  );
});

export default MathCaptcha;
