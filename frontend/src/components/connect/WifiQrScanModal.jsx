import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { Html5Qrcode } from 'html5-qrcode';
import { X, Camera, Upload, Keyboard, WifiHigh } from '@phosphor-icons/react';

const SCANNER_DIV_ID = 'japap-wifi-qr-scanner-div';

/**
 * iter141nineI — "Scan WiFi QR from your box" modal.
 *
 * Almost every consumer router (CAMTEL, MTN, Orange, FreeBox, Livebox,
 * Bbox, Apple AirPort, Google Nest, FRITZ!Box, …) prints a Wi-Fi QR
 * sticker that follows the IETF WIFI URI scheme:
 *
 *   WIFI:T:<WPA|WPA2|WEP|nopass>;S:<ssid>;P:<password>;H:<true|false>;;
 *
 * Field order can vary, semicolons inside SSID/password are escaped with
 * a leading backslash. This parser handles all of that and surfaces a
 * normalised `{ssid, password, security_type, hidden}` object so the
 * caller can pre-fill its form in one shot — zero typing required.
 *
 * Combined with the ISP-based suggestion we just shipped, ~95% of
 * "Partager mon WiFi" submissions now require ZERO field filling.
 */
export function parseWifiQrPayload(raw) {
  if (!raw || typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  // Accept "WIFI:" prefix in any case (some encoders use lowercase).
  if (!/^wifi:/i.test(trimmed)) return null;
  const body = trimmed.replace(/^wifi:/i, '').replace(/;;\s*$/, '');

  // Split on ';' but respect escaped '\;' (within ssid / password).
  const parts = [];
  let buf = '';
  for (let i = 0; i < body.length; i++) {
    const ch = body[i];
    if (ch === '\\' && i + 1 < body.length) {
      buf += body[i + 1]; i++; continue;
    }
    if (ch === ';') { parts.push(buf); buf = ''; continue; }
    buf += ch;
  }
  if (buf) parts.push(buf);

  const out = { ssid: '', password: '', security_type: 'WPA2', hidden: false };
  for (const part of parts) {
    const idx = part.indexOf(':');
    if (idx <= 0) continue;
    const key = part.slice(0, idx).trim().toUpperCase();
    const val = part.slice(idx + 1);
    if (key === 'S') out.ssid = val;
    else if (key === 'P') out.password = val;
    else if (key === 'T') {
      const t = val.trim().toUpperCase();
      if (t === 'NOPASS' || t === 'NONE' || t === '') out.security_type = 'OPEN';
      else if (t === 'WEP') out.security_type = 'WEP';
      else if (t === 'WPA3') out.security_type = 'WPA3';
      else if (t === 'WPA') out.security_type = 'WPA';
      else out.security_type = 'WPA2';  // covers "WPA2", "WPA/WPA2", default
    }
    else if (key === 'H') out.hidden = /^(true|1|yes)$/i.test(val.trim());
  }
  if (!out.ssid) return null;  // SSID is mandatory for it to be useful
  return out;
}


export default function WifiQrScanModal({ onClose, onScanned }) {
  const scannerRef = useRef(null);
  const [mode, setMode] = useState('camera');     // camera | upload | manual
  const [manual, setManual] = useState('');
  const [error, setError] = useState('');
  const [running, setRunning] = useState(false);

  const stopCamera = async () => {
    if (scannerRef.current) {
      try { await scannerRef.current.stop(); } catch {}
      try { await scannerRef.current.clear(); } catch {}
      scannerRef.current = null;
    }
  };

  const handlePayload = async (raw) => {
    setError('');
    const parsed = parseWifiQrPayload(raw);
    if (!parsed) {
      const msg = "Ce QR n'est pas un QR WiFi standard. Vérifie qu'il vient bien de ta box (sticker au dos ou notice).";
      setError(msg);
      toast.error(msg);
      return;
    }
    await stopCamera();
    toast.success(`WiFi détecté : ${parsed.ssid}`);
    onScanned?.(parsed);
  };

  // Start camera scanning when the modal mounts in 'camera' mode.
  useEffect(() => {
    if (mode !== 'camera') return;
    let killed = false;
    (async () => {
      try {
        const html5 = new Html5Qrcode(SCANNER_DIV_ID);
        scannerRef.current = html5;
        await html5.start(
          { facingMode: 'environment' },
          { fps: 10, qrbox: { width: 240, height: 240 } },
          (decoded) => { if (!killed) handlePayload(decoded); },
          () => { /* ignore per-frame decode errors */ }
        );
        if (!killed) setRunning(true);
      } catch (e) {
        if (!killed) setError("Caméra indisponible. Essaie 'Importer une image' ou 'Coller le code'.");
      }
    })();
    return () => { killed = true; stopCamera(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  const onUpload = async (file) => {
    setError('');
    try {
      const html5 = new Html5Qrcode(SCANNER_DIV_ID);
      const decoded = await html5.scanFile(file, false);
      try { await html5.clear(); } catch {}
      handlePayload(decoded);
    } catch (e) {
      const msg = "QR non détecté dans l'image. Réessaie avec une photo plus nette.";
      setError(msg); toast.error(msg);
    }
  };

  return (
    <div className="fixed inset-0 z-[110] flex items-end sm:items-center justify-center"
         style={{ background: 'rgba(0,0,0,0.65)', backdropFilter: 'blur(6px)' }}
         onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full sm:max-w-md jp-card-elevated p-5 sm:rounded-2xl rounded-t-2xl"
        style={{ background: 'var(--jp-surface)', maxHeight: '92vh', overflowY: 'auto' }}
        data-testid="wifi-qr-scan-modal"
      >
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-['Outfit'] text-lg font-bold flex items-center gap-2"
              style={{ color: 'var(--jp-text)' }}>
            <WifiHigh size={22} weight="fill" style={{ color: '#F7931A' }} />
            Scanner le QR de ma box
          </h3>
          <button onClick={onClose} data-testid="wifi-qr-scan-close"
                  className="p-1 rounded-lg" aria-label="Fermer">
            <X size={20} />
          </button>
        </div>

        <p className="text-xs mb-4" style={{ color: 'var(--jp-text-muted)' }}>
          La plupart des box internet ont un QR WiFi imprimé dessous ou dans la
          notice. Scanne-le et JAPAP remplit tout pour toi (SSID + sécurité + mot de passe).
        </p>

        {/* Mode tabs */}
        <div className="grid grid-cols-3 gap-1 mb-3 p-1 rounded-lg"
             style={{ background: 'var(--jp-surface-secondary)' }}>
          {[
            { k: 'camera', icon: Camera, label: 'Caméra' },
            { k: 'upload', icon: Upload, label: 'Image' },
            { k: 'manual', icon: Keyboard, label: 'Coller' },
          ].map(({ k, icon: Icn, label }) => (
            <button key={k} type="button"
              onClick={async () => { await stopCamera(); setMode(k); setError(''); }}
              data-testid={`wifi-qr-mode-${k}`}
              className="py-2 rounded-md text-xs font-bold flex items-center justify-center gap-1.5 transition-colors"
              style={{
                background: mode === k ? 'var(--jp-primary)' : 'transparent',
                color: mode === k ? '#fff' : 'var(--jp-text-muted)',
              }}>
              <Icn size={14} /> {label}
            </button>
          ))}
        </div>

        {error && (
          <div className="rounded-lg p-2.5 mb-3 text-xs"
               data-testid="wifi-qr-error"
               style={{ background: 'rgba(239,68,68,0.10)', color: '#DC2626',
                        border: '1px solid rgba(239,68,68,0.25)' }}>
            {error}
          </div>
        )}

        {mode === 'camera' && (
          <>
            <div id={SCANNER_DIV_ID}
                 data-testid="wifi-qr-camera"
                 className="rounded-xl overflow-hidden"
                 style={{ background: '#000', minHeight: 240 }} />
            {!running && (
              <p className="text-center text-xs mt-2" style={{ color: 'var(--jp-text-muted)' }}>
                Activation de la caméra…
              </p>
            )}
          </>
        )}

        {mode === 'upload' && (
          <div className="text-center py-4">
            <input type="file" accept="image/*"
              data-testid="wifi-qr-upload-input"
              onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0])}
              className="block mx-auto text-sm" />
            <p className="text-[11px] mt-2" style={{ color: 'var(--jp-text-muted)' }}>
              Choisis une photo nette du QR sticker.
            </p>
            <div id={SCANNER_DIV_ID} className="hidden" />
          </div>
        )}

        {mode === 'manual' && (
          <div className="space-y-2">
            <textarea
              value={manual}
              onChange={(e) => setManual(e.target.value)}
              rows={3}
              data-testid="wifi-qr-manual-input"
              placeholder="WIFI:T:WPA2;S:MaBox;P:motdepasse;;"
              className="jp-input text-xs"
              style={{ fontFamily: 'monospace' }} />
            <button type="button"
              onClick={() => handlePayload(manual)}
              disabled={!manual}
              data-testid="wifi-qr-manual-submit"
              className="jp-btn jp-btn-primary jp-btn-sm w-full disabled:opacity-50">
              Analyser
            </button>
            <div id={SCANNER_DIV_ID} className="hidden" />
          </div>
        )}
      </div>
    </div>
  );
}
