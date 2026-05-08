/**
 * QRScannerModal — iter106 (P0 fix multi-device).
 *
 * Replaces the old BarcodeDetector-only flow (which silently fails on iOS
 * Safari + older Android) with `html5-qrcode` — a battle-tested library
 * that works on every modern mobile browser AND desktops with a webcam.
 *
 * Three strategies, in order:
 *   1. CAMERA — html5-qrcode live scan (iOS Safari, Android Chrome,
 *      Android WebView, desktop with webcam).
 *   2. UPLOAD — pick a screenshot of a QR; html5-qrcode decodes the file.
 *   3. MANUAL — paste the JSON payload by hand (last-resort fallback).
 *
 * On success, calls onResolved({user_id, name, username, avatar, is_pro}).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import { Html5Qrcode } from 'html5-qrcode';
import { X, Camera, Upload, Keyboard } from '@phosphor-icons/react';
import * as WS from '../../utils/walletSecurity';

const API = process.env.REACT_APP_BACKEND_URL;
const SCANNER_DIV_ID = 'japap-qr-scanner-div';

export default function QRScannerModal({ onClose, onResolved }) {
  const scannerRef = useRef(null);
  const navigate = useNavigate();
  const [mode, setMode] = useState('camera'); // camera | upload | manual
  const [error, setError] = useState('');
  const [manual, setManual] = useState('');
  const [loading, setLoading] = useState(false);
  const [cameras, setCameras] = useState([]);
  const [activeCam, setActiveCam] = useState('');

  const stopCamera = useCallback(async () => {
    const sc = scannerRef.current;
    if (!sc) return;
    try {
      // html5-qrcode raises if scanner isn't currently scanning. Guard.
      if (sc.isScanning) await sc.stop();
      await sc.clear();
    } catch { /* ignore */ }
    scannerRef.current = null;
  }, []);

  const resolve = useCallback(async (decodedStr) => {
    setLoading(true); setError('');
    try {
      const raw = (decodedStr || '').trim();

      // iter141nineD — Payment Request QRs ("Demander à recevoir") embed
      // a plain URL like https://<host>/pay/pr_xxxxxx (compatible with
      // any phone camera / WhatsApp / Google Lens). Detect that pattern
      // FIRST and navigate straight to the public PayPage.
      if (/^https?:\/\//i.test(raw)) {
        let parsed;
        try { parsed = new URL(raw); } catch { parsed = null; }
        // iter221 — Gate every HTTPS branch behind the JAPAP origin allow-list
        // BEFORE pathname matching. Without this, an attacker-controlled
        // domain like japap.evil.com/pay/pr_123 would bypass the check below
        // and trigger window.location.href to the malicious origin.
        if (!parsed || !WS.isSafeJapapUrl(raw)) {
          const msg = "QR non-JAPAP. Scanne un QR de paiement ou de profil JAPAP.";
          setError(msg);
          toast.error(msg);
          return;
        }
        const m = parsed.pathname.match(/^\/pay\/(pr_[A-Za-z0-9]+)\/?$/);
        // iter141nineE — also accept the OG-preview URL (/api/og/pay/<id>)
      // since users may copy that URL from a WhatsApp share.
      const ogMatch = parsed.pathname.match(/^\/api\/og\/pay\/(pr_[A-Za-z0-9]+)\/?$/);
      if (m || ogMatch) {
        const requestId = (m || ogMatch)[1];
        await stopCamera();
        toast.success('Demande de paiement détectée — ouverture…');
        if (parsed.origin === window.location.origin) {
          navigate(`/pay/${requestId}`);
        } else {
          window.location.href = raw;
        }
        onClose?.();
        return;
      }
        // Other JAPAP URL (profile/duel/etc) → just open it.
        await stopCamera();
        if (parsed.origin === window.location.origin) {
          navigate(parsed.pathname + parsed.search);
        } else {
          window.location.href = raw;
        }
        onClose?.();
        return;
      }

      // Legacy / profile-QR path: japap.pay JSON payload (iter91+).
      const json = JSON.parse(raw);
      if (!WS.isValidQrPayload(json)) {
        const msg = 'QR code JAPAP invalide.';
        setError(msg);
        toast.error(msg);
        return;
      }
      const r = await axios.post(`${API}/api/users/resolve-qr`, json,
                                  { withCredentials: true });
      toast.success(`Contact : ${r.data.name || r.data.username || r.data.user_id}`);
      await stopCamera();
      onResolved?.(r.data);
    } catch (e) {
      const msg = e.response?.data?.detail || 'QR code JAPAP invalide.';
      setError(msg);
      toast.error(msg);
    } finally { setLoading(false); }
  }, [onResolved, onClose, navigate, stopCamera]);

  // ── Camera mode: html5-qrcode ──────────────────────────────────────────
  useEffect(() => {
    if (mode !== 'camera') return;
    let cancelled = false;
    setError('');
    (async () => {
      try {
        // Enumerate available cameras (mobile usually has 2 — front+back).
        const devs = await Html5Qrcode.getCameras();
        if (cancelled) return;
        if (!devs || devs.length === 0) {
          setError('Aucune caméra détectée. Utilisez Upload ou Manuel.');
          return;
        }
        // Prefer rear-facing camera by label heuristic.
        const rear = devs.find(d => /back|rear|environment/i.test(d.label || ''));
        const cam = rear || devs[devs.length - 1];
        setCameras(devs);
        setActiveCam(cam.id);
        const html5 = new Html5Qrcode(SCANNER_DIV_ID, /* verbose= */ false);
        scannerRef.current = html5;
        await html5.start(
          cam.id,
          { fps: 10, qrbox: { width: 240, height: 240 } },
          (decodedText) => { resolve(decodedText); },
          /* errorCallback ignored — it fires on every failed frame */ () => {},
        );
      } catch (e) {
        const reason = (e && e.message) ? e.message : String(e);
        // iter119 — Ship scanner failures to AI Error Monitor (best-effort).
        try {
          axios.post(`${API}/api/errors/report`, WS.sanitizeErrorReport({
            module: 'wallet.qr.scanner',
            severity: /permission|NotAllowed/i.test(reason) ? 'medium' : 'high',
            message: `QR scanner camera failed: ${reason}`,
            stack: String(e?.stack || ''),
          }), { withCredentials: true }).catch(() => {});
        } catch (_) { /* silent */ }
        // Most common failure: getUserMedia denied or HTTPS required.
        if (/permission/i.test(reason) || /NotAllowed/i.test(reason)) {
          setError("Accès caméra refusé. Autorisez l'accès puis réessayez, ou utilisez Upload / Manuel.");
        } else if (/secure|https/i.test(reason)) {
          setError('La caméra exige une connexion HTTPS. Utilisez Upload ou Manuel.');
        } else {
          setError('Caméra indisponible. Utilisez Upload ou Manuel.');
        }
      }
    })();
    return () => { cancelled = true; stopCamera(); };
  }, [mode, resolve, stopCamera]);

  // Switch active camera
  const switchCamera = async (camId) => {
    if (!camId) return;
    setActiveCam(camId);
    await stopCamera();
    setMode(''); setTimeout(() => setMode('camera'), 50);
  };

  useEffect(() => () => stopCamera(), [stopCamera]);

  const handleUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setLoading(true); setError('');
    try {
      const html5 = new Html5Qrcode(SCANNER_DIV_ID + '-upload', false);
      const decoded = await html5.scanFile(file, /* showImage= */ false);
      await resolve(decoded);
    } catch (err) {
      const msg = (err && err.message) || String(err);
      // iter119 — Ship decode failures to AI Error Monitor (best-effort).
      try {
        axios.post(`${API}/api/errors/report`, WS.sanitizeErrorReport({
          module: 'wallet.qr.scanner',
          severity: 'medium',
          message: `QR upload decode failed: ${msg}`,
        }), { withCredentials: true }).catch(() => {});
      } catch (_) { /* silent */ }
      if (/No.*found/i.test(msg)) {
        toast.error("Aucun QR détecté dans l'image. Essayez une photo plus nette.");
      } else {
        toast.error("Décodage impossible. Essayez le mode manuel.");
      }
    } finally {
      setLoading(false);
      // Reset input value so the same file can be re-selected.
      e.target.value = '';
    }
  };

  const handleManual = async () => {
    if (!manual.trim()) return;
    await resolve(manual.trim());
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         style={{ background: 'rgba(11,16,32,0.8)', backdropFilter: 'blur(6px)' }}
         data-testid="wallet-qr-scanner-modal">
      <div className="jp-card-elevated max-w-md w-full p-5 jp-animate-scaleIn">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-['Outfit'] text-lg font-bold flex items-center gap-2"
              style={{ color: 'var(--jp-text)' }}>
            <Camera size={20} weight="fill" /> Scanner un QR JAPAP
          </h3>
          <button onClick={() => { stopCamera(); onClose?.(); }}
                  className="p-1 rounded-lg"
                  data-testid="qr-scanner-close"
                  style={{ color: 'var(--jp-text-muted)' }}>
            <X size={18} />
          </button>
        </div>

        {/* Mode switcher */}
        <div className="jp-tabs mb-3 flex">
          <button onClick={() => setMode('camera')}
                  data-testid="qr-mode-camera"
                  className={`jp-tab ${mode === 'camera' ? 'jp-tab-active' : ''}`}>
            <Camera size={12} weight="bold" className="inline mr-1" /> Caméra
          </button>
          <button onClick={() => { stopCamera(); setMode('upload'); }}
                  data-testid="qr-mode-upload"
                  className={`jp-tab ${mode === 'upload' ? 'jp-tab-active' : ''}`}>
            <Upload size={12} weight="bold" className="inline mr-1" /> Upload
          </button>
          <button onClick={() => { stopCamera(); setMode('manual'); }}
                  data-testid="qr-mode-manual"
                  className={`jp-tab ${mode === 'manual' ? 'jp-tab-active' : ''}`}>
            <Keyboard size={12} weight="bold" className="inline mr-1" /> Manuel
          </button>
        </div>

        {error && (
          <div className="jp-alert jp-alert-warning mb-3 text-xs"
               data-testid="qr-scanner-error">
            {error}
          </div>
        )}

        {mode === 'camera' && (
          <>
            <div className="rounded-xl overflow-hidden"
                 style={{ background: '#000' }}>
              <div id={SCANNER_DIV_ID}
                   data-testid="qr-camera-area"
                   style={{ width: '100%', minHeight: 240 }} />
            </div>
            {cameras.length > 1 && (
              <select value={activeCam}
                      onChange={(e) => switchCamera(e.target.value)}
                      className="jp-input text-xs w-full mt-2"
                      data-testid="qr-camera-switch">
                {cameras.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.label || `Caméra ${c.id.slice(0, 8)}`}
                  </option>
                ))}
              </select>
            )}
          </>
        )}

        {mode === 'upload' && (
          <div className="flex flex-col items-center gap-3 py-4">
            <div id={SCANNER_DIV_ID + '-upload'} style={{ display: 'none' }} />
            <label className="jp-btn jp-btn-primary cursor-pointer"
                   data-testid="qr-upload-label">
              <Upload size={16} weight="bold" />
              Choisir une image
              <input type="file" accept="image/*"
                     onChange={handleUpload}
                     data-testid="qr-upload-input"
                     style={{ display: 'none' }} />
            </label>
            <p className="text-xs text-center"
               style={{ color: 'var(--jp-text-muted)' }}>
              Sélectionnez une capture du QR code.
            </p>
          </div>
        )}

        {mode === 'manual' && (
          <div className="flex flex-col gap-3 py-2">
            <label className="jp-label">Coller le contenu du QR code</label>
            <textarea value={manual}
                      onChange={(e) => setManual(e.target.value)}
                      placeholder='Colle un lien /pay/pr_xxx OU un payload {"t":"japap.pay",...}'
                      rows={4}
                      data-testid="qr-manual-input"
                      className="jp-input text-xs"
                      style={{ fontFamily: 'monospace' }} />
            <button onClick={handleManual}
                    disabled={loading || !manual.trim()}
                    data-testid="qr-manual-submit"
                    className="jp-btn jp-btn-primary">
              Valider
            </button>
          </div>
        )}

        {loading && (
          <div className="text-center text-xs mt-2"
               style={{ color: 'var(--jp-text-muted)' }}>
            Analyse en cours…
          </div>
        )}
      </div>
    </div>
  );
}
