/**
 * ConnectWifiCredentialsModal — Host-side: set or update SSID+password+security
 * for one of their hotspots. The password never appears in any GET; this
 * modal writes-only via PUT /api/connect/hotspots/{id}/wifi.
 */
import { useState } from 'react';
import { toast } from 'sonner';
import axios from 'axios';
import { X, WifiHigh, Eye, EyeSlash, Trash } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

export default function ConnectWifiCredentialsModal({ hotspot, onClose, onSaved }) {
  const { t } = useTranslation();
  const [ssid, setSsid] = useState(hotspot.ssid || '');
  const [password, setPassword] = useState('');
  const [securityType, setSecurityType] = useState(hotspot.security_type || 'WPA2');
  const [showPw, setShowPw] = useState(false);
  const [saving, setSaving] = useState(false);

  const save = async (e) => {
    e.preventDefault();
    if (!ssid.trim() || !password.trim()) return toast.error('SSID et mot de passe requis.');
    setSaving(true);
    try {
      await axios.put(
        `${API}/api/connect/hotspots/${hotspot.hotspot_id}/wifi`,
        { ssid: ssid.trim(), password, security_type: securityType },
        { withCredentials: true },
      );
      toast.success('Identifiants WiFi enregistrés (chiffrés).');
      onSaved?.();
      onClose?.();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Impossible d\'enregistrer les identifiants.');
    } finally {
      setSaving(false);
    }
  };

  const clear = async () => {
    if (!window.confirm('Supprimer les identifiants WiFi ? Les invités ne pourront plus se connecter via JAPAP.')) return;
    setSaving(true);
    try {
      await axios.delete(
        `${API}/api/connect/hotspots/${hotspot.hotspot_id}/wifi`,
        { withCredentials: true },
      );
      toast.success('Identifiants supprimés.');
      onSaved?.();
      onClose?.();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Erreur');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-end sm:items-center justify-center bg-black/70 backdrop-blur-sm"
         onClick={onClose} data-testid="connect-wifi-creds-modal">
      <form onSubmit={save} onClick={(e) => e.stopPropagation()}
            className="w-full sm:w-[420px] max-h-[92vh] overflow-y-auto rounded-t-3xl sm:rounded-3xl p-6 flex flex-col gap-4"
            style={{ background: 'var(--jp-surface, #fff)', color: 'var(--jp-text)' }}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <WifiHigh size={20} weight="bold" style={{ color: 'var(--jp-primary)' }} />
            <h2 className="font-['Outfit'] font-bold text-lg">Identifiants WiFi</h2>
          </div>
          <button type="button" onClick={onClose} className="p-2 rounded-full hover:bg-black/5"
                  data-testid="connect-wifi-creds-close">
            <X size={20} />
          </button>
        </div>

        <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
          Les identifiants sont chiffrés au repos et ne sont jamais renvoyés dans les réponses publiques.
          Seuls les invités ayant scanné votre QR (avec un compte autorisé) obtiendront le mot de passe.
        </p>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-bold">SSID (nom du réseau)</span>
          <input value={ssid} onChange={(e) => setSsid(e.target.value)}
                 placeholder={t('connect_wifi_credentials_modal.cafeabidjan')} maxLength={64} required
                 data-testid="connect-wifi-ssid-input"
                 className="jp-input w-full" />
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-bold">Mot de passe</span>
          <div className="flex items-center gap-2">
            <input value={password} onChange={(e) => setPassword(e.target.value)}
                   type={showPw ? 'text' : 'password'} placeholder="••••••••"
                   minLength={1} maxLength={128} required
                   data-testid="connect-wifi-password-input"
                   className="jp-input w-full flex-1" />
            <button type="button" onClick={() => setShowPw(!showPw)}
                    className="p-2 rounded-full hover:bg-black/5"
                    data-testid="connect-wifi-password-toggle"
                    aria-label={showPw ? 'Masquer' : 'Afficher'}>
              {showPw ? <EyeSlash size={18} /> : <Eye size={18} />}
            </button>
          </div>
        </label>

        <label className="flex flex-col gap-1 text-sm">
          <span className="font-bold">Type de sécurité</span>
          <select value={securityType} onChange={(e) => setSecurityType(e.target.value)}
                  data-testid="connect-wifi-security-select" className="jp-input w-full">
            <option value="WPA2">{t('connect_wifi_credentials_modal.wpa2_le_plus_courant')}</option>
            <option value="WPA3">WPA3</option>
            <option value="WPA">WPA</option>
            <option value="WEP">{t('connect_wifi_credentials_modal.wep_ancien')}</option>
            <option value="OPEN">{t('connect_wifi_credentials_modal.ouvert_aucun')}</option>
          </select>
        </label>

        <div className="flex items-center gap-2 pt-2">
          {hotspot.wifi_configured && (
            <button type="button" onClick={clear} disabled={saving}
                    className="jp-btn jp-btn-ghost text-sm"
                    style={{ color: 'var(--jp-error)' }}
                    data-testid="connect-wifi-clear-btn">
              <Trash size={14} /> Supprimer
            </button>
          )}
          <button type="submit" disabled={saving}
                  className="jp-btn jp-btn-primary text-sm flex-1"
                  data-testid="connect-wifi-save-btn">
            {saving ? 'Enregistrement...' : 'Enregistrer (chiffré)'}
          </button>
        </div>
      </form>
    </div>
  );
}
