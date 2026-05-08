/**
 * TransportSurgeAdminTab — Phase D (iter105).
 *
 * Admin pilots the dynamic surge engine per country:
 *   • Live simulator (GPS coords + vehicle → real-time multiplier + factors)
 *   • Config form (toggles + thresholds for each layer: time / demand / urban / traffic)
 *   • History table with KPIs (extra revenue, avg/max multiplier, surged rides)
 *
 * All changes are audit-logged via transport.surge.config_update.
 */
import { useEffect, useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  Gauge, Clock, Users, Buildings, TrafficCone, Sparkle, FloppyDisk,
  MapPin, Warning, TrendUp,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

const COUNTRY_PRESETS = [
  { code: 'CM', name: 'Cameroun' },
  { code: 'CI', name: "Côte d'Ivoire" },
  { code: 'SN', name: 'Sénégal' },
  { code: 'NG', name: 'Nigeria' },
  { code: 'GH', name: 'Ghana' },
  { code: 'FR', name: 'France' },
];

export default function TransportSurgeAdminTab() {
  const { t } = useTranslation();
  const [country, setCountry] = useState('CM');
  const [config, setConfig] = useState(null);
  const [defaults, setDefaults] = useState(null);
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState(null);
  const [previewLat, setPreviewLat] = useState(3.848);
  const [previewLng, setPreviewLng] = useState(11.502);
  const [previewVehicle, setPreviewVehicle] = useState('standard');
  const [history, setHistory] = useState(null);
  const [historyDays, setHistoryDays] = useState(7);

  const loadConfig = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/api/transport/admin/surge/config`, {
        params: { country }, withCredentials: true,
      });
      setConfig(r.data.config);
      setDefaults(r.data.defaults);
    } catch (e) { toast.error(e.response?.data?.detail || 'Chargement échoué'); }
  }, [country]);

  const loadPreview = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/api/transport/admin/surge/preview`, {
        params: {
          country, pickup_lat: previewLat, pickup_lng: previewLng,
          vehicle_type: previewVehicle,
        },
        withCredentials: true,
      });
      setPreview(r.data);
    } catch (e) { toast.error(e.response?.data?.detail || 'Aperçu indisponible'); }
  }, [country, previewLat, previewLng, previewVehicle]);

  const loadHistory = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/api/transport/admin/surge/history`, {
        params: { days: historyDays, country, min_multiplier: 1.0, limit: 100 },
        withCredentials: true,
      });
      setHistory(r.data);
    } catch (e) { toast.error(e.response?.data?.detail || 'Historique indisponible'); }
  }, [country, historyDays]);

  useEffect(() => { loadConfig(); }, [loadConfig]);
  useEffect(() => { loadHistory(); }, [loadHistory]);

  const save = async () => {
    setSaving(true);
    try {
      await axios.put(`${API}/api/transport/admin/surge/config`,
        { country_code: country, config },
        { withCredentials: true });
      toast.success('Configuration sauvegardée');
      loadPreview();
    } catch (e) { toast.error(e.response?.data?.detail || 'Sauvegarde échouée'); }
    finally { setSaving(false); }
  };

  if (!config) {
    return <div className="p-6 text-center opacity-50 text-xs">Chargement…</div>;
  }

  const setKey = (k, v) => setConfig({ ...config, [k]: v });

  return (
    <div className="space-y-4" data-testid="transport-surge-admin">
      {/* Country + save row */}
      <div className="flex items-center gap-2 flex-wrap">
        <select value={country} onChange={(e) => setCountry(e.target.value)}
                className="jp-input text-xs" style={{ maxWidth: 200 }}
                data-testid="surge-country-select">
          {COUNTRY_PRESETS.map((c) => (
            <option key={c.code} value={c.code}>{c.code} · {c.name}</option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 text-xs font-bold cursor-pointer ml-2"
               data-testid="surge-enabled-toggle">
          <input type="checkbox" checked={Boolean(config.enabled)}
                 onChange={(e) => setKey('enabled', e.target.checked)} />
          Moteur surge activé
        </label>
        <button onClick={save} disabled={saving}
                className="jp-btn jp-btn-primary text-xs ml-auto flex items-center gap-1.5"
                data-testid="surge-save-btn">
          <FloppyDisk size={12} weight="fill" />
          {saving ? 'Sauvegarde…' : 'Enregistrer'}
        </button>
      </div>

      {/* Live preview */}
      <div className="jp-card-elevated p-4" data-testid="surge-preview">
        <h3 className="text-xs font-bold uppercase opacity-60 mb-3 flex items-center gap-1.5">
          <Gauge size={14} weight="fill" style={{ color: '#7c3aed' }} />
          Simulateur live
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-2 mb-3">
          <input type="number" step="0.0001" value={previewLat}
                 onChange={(e) => setPreviewLat(parseFloat(e.target.value))}
                 className="jp-input text-xs" placeholder="Latitude"
                 data-testid="surge-preview-lat" />
          <input type="number" step="0.0001" value={previewLng}
                 onChange={(e) => setPreviewLng(parseFloat(e.target.value))}
                 className="jp-input text-xs" placeholder="Longitude"
                 data-testid="surge-preview-lng" />
          <select value={previewVehicle} onChange={(e) => setPreviewVehicle(e.target.value)}
                  className="jp-input text-xs" data-testid="surge-preview-vehicle">
            <option value="standard">{t('transport_surge_admin.standard')}</option>
            <option value="premium">{t('transport_surge_admin.premium')}</option>
          </select>
          <button onClick={loadPreview} className="jp-btn jp-btn-ghost text-xs font-bold"
                  data-testid="surge-preview-btn">Calculer</button>
        </div>
        {preview && (
          <div className="space-y-2">
            <div className="flex items-center gap-3 p-3 rounded-xl"
                 style={{ background: 'var(--jp-surface-secondary)' }}>
              <div className="text-3xl font-extrabold tabular-nums"
                   style={{ color: preview.multiplier > 1.2 ? '#E01C2E' : preview.multiplier > 1.0 ? '#F7931A' : '#10b981' }}
                   data-testid="surge-preview-multiplier">
                ×{parseFloat(preview.multiplier).toFixed(2)}
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-[10px] uppercase opacity-60 font-bold">Multiplicateur final</div>
                {preview.label && (
                  <span className="inline-flex items-center gap-1 text-[10px] font-bold uppercase px-1.5 py-0.5 rounded mt-1"
                        style={{ background: '#E01C2E20', color: '#E01C2E' }}>
                    <Warning size={9} weight="fill" /> {preview.label}
                  </span>
                )}
                <div className="text-[10px] opacity-60 mt-1">Cellule H3 : {preview.h3_cell}</div>
              </div>
            </div>
            <div className="grid grid-cols-5 gap-1.5 text-center">
              <FactorPill icon={Clock} label="Temps" value={preview.factors.time} extra={preview.details.time_band} />
              <FactorPill icon={Users} label="Demande" value={preview.factors.demand}
                          extra={preview.details.demand_supply_ratio !== null ? `r=${parseFloat(preview.details.demand_supply_ratio).toFixed(2)}` : 'n/a'} />
              <FactorPill icon={Buildings} label="Urbain" value={preview.factors.urban}
                          extra={`${preview.details.urban_score} pickups`} />
              <FactorPill icon={TrafficCone} label="Trafic" value={preview.factors.traffic}
                          extra={preview.details.traffic_kmh !== null ? `${parseFloat(preview.details.traffic_kmh).toFixed(0)} km/h` : 'n/a'} />
              <FactorPill icon={Sparkle} label="Véhicule" value={preview.factors.vehicle} extra={previewVehicle} />
            </div>
          </div>
        )}
      </div>

      {/* Config form */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <ConfigCard icon={Gauge} title={t('transport_surge_admin.cap_global')} color="#7c3aed">
          <NumField label="Multiplicateur max" value={config.max_surge} step="0.1"
                    onChange={(v) => setKey('max_surge', v)} testid="cfg-max-surge" />
          <NumField label="Seuil affichage 'Forte demande'" value={config.label_threshold} step="0.05"
                    onChange={(v) => setKey('label_threshold', v)} testid="cfg-label-threshold" />
          <NumField label="Uplift premium" value={config.premium_uplift} step="0.05"
                    onChange={(v) => setKey('premium_uplift', v)} testid="cfg-premium-uplift" />
        </ConfigCard>

        <ConfigCard icon={Clock} title={t('transport_surge_admin.heures_de_pointe')} color="#F7931A"
                    enabled={config.time_enabled}
                    onToggle={(v) => setKey('time_enabled', v)}
                    testid="cfg-time">
          <div className="grid grid-cols-2 gap-2">
            <NumField label="Matin début (h)" value={config.peak_morning_start} step="1" onChange={(v) => setKey('peak_morning_start', v)} testid="cfg-peak-morning-start" />
            <NumField label="Matin fin (h)" value={config.peak_morning_end} step="1" onChange={(v) => setKey('peak_morning_end', v)} />
            <NumField label="Soir début (h)" value={config.peak_evening_start} step="1" onChange={(v) => setKey('peak_evening_start', v)} />
            <NumField label="Soir fin (h)" value={config.peak_evening_end} step="1" onChange={(v) => setKey('peak_evening_end', v)} />
          </div>
          <NumField label="Uplift pointe" value={config.peak_uplift} step="0.05" onChange={(v) => setKey('peak_uplift', v)} testid="cfg-peak-uplift" />
          <div className="grid grid-cols-2 gap-2">
            <NumField label="Nuit début (h)" value={config.night_start} step="1" onChange={(v) => setKey('night_start', v)} />
            <NumField label="Nuit fin (h)" value={config.night_end} step="1" onChange={(v) => setKey('night_end', v)} />
          </div>
          <NumField label="Uplift nuit" value={config.night_uplift} step="0.05" onChange={(v) => setKey('night_uplift', v)} />
        </ConfigCard>

        <ConfigCard icon={Users} title={t('transport_surge_admin.demande_offre')} color="#E01C2E"
                    enabled={config.demand_enabled}
                    onToggle={(v) => setKey('demand_enabled', v)}
                    testid="cfg-demand">
          <NumField label="Seuil haute (rides/driver)" value={config.ds_high_ratio} step="0.1" onChange={(v) => setKey('ds_high_ratio', v)} />
          <NumField label="Uplift haute" value={config.ds_high_uplift} step="0.05" onChange={(v) => setKey('ds_high_uplift', v)} />
          <NumField label="Seuil moyenne" value={config.ds_med_ratio} step="0.1" onChange={(v) => setKey('ds_med_ratio', v)} />
          <NumField label="Uplift moyenne" value={config.ds_med_uplift} step="0.05" onChange={(v) => setKey('ds_med_uplift', v)} />
        </ConfigCard>

        <ConfigCard icon={Buildings} title={t('transport_surge_admin.zone_urbaine')} color="#0F056B"
                    enabled={config.urban_enabled}
                    onToggle={(v) => setKey('urban_enabled', v)}
                    testid="cfg-urban">
          <NumField label="Seuil pickups 90j" value={config.urban_threshold} step="10" onChange={(v) => setKey('urban_threshold', v)} />
          <NumField label="Uplift urbain" value={config.urban_uplift} step="0.05" onChange={(v) => setKey('urban_uplift', v)} />
        </ConfigCard>

        <ConfigCard icon={TrafficCone} title={t('transport_surge_admin.trafic_proxy_interne')} color="#10b981"
                    enabled={config.traffic_enabled}
                    onToggle={(v) => setKey('traffic_enabled', v)}
                    testid="cfg-traffic">
          <NumField label="Vitesse dense (km/h)" value={config.traffic_slow_kmh} step="1" onChange={(v) => setKey('traffic_slow_kmh', v)} />
          <NumField label="Uplift dense" value={config.traffic_slow_uplift} step="0.05" onChange={(v) => setKey('traffic_slow_uplift', v)} />
          <NumField label="Vitesse modérée (km/h)" value={config.traffic_med_kmh} step="1" onChange={(v) => setKey('traffic_med_kmh', v)} />
          <NumField label="Uplift modérée" value={config.traffic_med_uplift} step="0.05" onChange={(v) => setKey('traffic_med_uplift', v)} />
        </ConfigCard>
      </div>

      {/* History */}
      <div className="jp-card p-4" data-testid="surge-history">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-xs font-bold uppercase opacity-60 flex items-center gap-1.5">
            <TrendUp size={14} weight="fill" /> Historique
          </h3>
          <select value={historyDays} onChange={(e) => setHistoryDays(parseInt(e.target.value, 10))}
                  className="jp-input text-xs" style={{ width: 80 }}>
            {[1, 7, 14, 30, 90].map((d) => <option key={d} value={d}>{d} j</option>)}
          </select>
        </div>
        {history && (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mb-3">
              <SummaryChip label="Courses" value={history.summary.total} />
              <SummaryChip label="Dont surgées" value={history.summary.surged} color="#F7931A" />
              <SummaryChip label="Multiplicateur moy." value={`×${parseFloat(history.summary.avg_multiplier).toFixed(2)}`} />
              <SummaryChip label="Multiplicateur max" value={`×${parseFloat(history.summary.max_multiplier).toFixed(2)}`} color="#E01C2E" />
              <SummaryChip label="Revenu additionnel" value={`+${parseFloat(history.summary.extra_revenue).toLocaleString('fr-FR', { maximumFractionDigits: 0 })}`} color="#10b981" />
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead style={{ background: 'var(--jp-surface-secondary)' }}>
                  <tr>
                    <th className="text-left p-2 opacity-60">Date</th>
                    <th className="text-left p-2 opacity-60">Ride</th>
                    <th className="text-left p-2 opacity-60">Pays / Véh.</th>
                    <th className="text-left p-2 opacity-60">Bande</th>
                    <th className="text-right p-2 opacity-60">×</th>
                    <th className="text-right p-2 opacity-60">Base</th>
                    <th className="text-right p-2 opacity-60">Final</th>
                  </tr>
                </thead>
                <tbody>
                  {history.items.map((h) => (
                    <tr key={h.id} style={{ borderTop: '1px solid var(--jp-border)' }}>
                      <td className="p-2 opacity-70">{new Date(h.applied_at).toLocaleString('fr-FR')}</td>
                      <td className="p-2 font-mono text-[10px] opacity-60">{(h.ride_id || '—').slice(0, 12)}</td>
                      <td className="p-2">{h.country_code} / {h.vehicle_type}</td>
                      <td className="p-2 opacity-70">{h.time_band}</td>
                      <td className="p-2 text-right font-bold tabular-nums"
                          style={{ color: parseFloat(h.final_multiplier) > 1.2 ? '#E01C2E' : parseFloat(h.final_multiplier) > 1.0 ? '#F7931A' : '#10b981' }}>
                        ×{parseFloat(h.final_multiplier).toFixed(2)}
                      </td>
                      <td className="p-2 text-right opacity-70 tabular-nums">{parseFloat(h.base_fare || 0).toLocaleString('fr-FR', { maximumFractionDigits: 0 })}</td>
                      <td className="p-2 text-right font-bold tabular-nums">{parseFloat(h.final_fare || 0).toLocaleString('fr-FR', { maximumFractionDigits: 0 })}</td>
                    </tr>
                  ))}
                  {history.items.length === 0 && (
                    <tr><td colSpan={7} className="p-6 text-center opacity-50">Aucun historique sur la fenêtre.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ConfigCard({ icon: Icon, title, color, enabled, onToggle, children, testid }) {
  const hasToggle = typeof enabled !== 'undefined';
  return (
    <div className="jp-card p-3" data-testid={testid}>
      <div className="flex items-center justify-between mb-2 pb-2"
           style={{ borderBottom: '1px solid var(--jp-border)' }}>
        <div className="flex items-center gap-1.5">
          <Icon size={14} weight="fill" style={{ color }} />
          <span className="text-xs font-bold">{title}</span>
        </div>
        {hasToggle && (
          <label className="flex items-center gap-1.5 text-[10px] font-bold cursor-pointer">
            <input type="checkbox" checked={Boolean(enabled)} onChange={(e) => onToggle(e.target.checked)} />
            Actif
          </label>
        )}
      </div>
      <div className="space-y-1.5">{children}</div>
    </div>
  );
}

function NumField({ label, value, step, onChange, testid }) {
  return (
    <label className="block text-[10px] opacity-70">
      {label}
      <input type="number" step={step || '0.01'}
             value={value ?? ''}
             onChange={(e) => onChange(parseFloat(e.target.value))}
             className="jp-input text-xs w-full mt-0.5"
             data-testid={testid} />
    </label>
  );
}

function FactorPill({ icon: Icon, label, value, extra }) {
  const v = parseFloat(value) || 0;
  const positive = v > 0;
  return (
    <div className="p-1.5 rounded-lg"
         style={{ background: positive ? '#F7931A14' : 'var(--jp-surface-secondary)' }}>
      <div className="flex items-center justify-center gap-1 text-[10px] opacity-70">
        <Icon size={10} /> {label}
      </div>
      <div className="font-bold text-xs" style={{ color: positive ? '#F7931A' : 'var(--jp-text)' }}>
        +{(v * 100).toFixed(0)}%
      </div>
      <div className="text-[9px] opacity-50 truncate">{extra}</div>
    </div>
  );
}

function SummaryChip({ label, value, color }) {
  return (
    <div className="p-2 rounded-lg text-center"
         style={{ background: 'var(--jp-surface-secondary)' }}>
      <div className="text-[9px] uppercase opacity-60 font-bold">{label}</div>
      <div className="font-extrabold tabular-nums text-sm mt-0.5"
           style={{ color: color || 'var(--jp-text)' }}>
        {value}
      </div>
    </div>
  );
}
