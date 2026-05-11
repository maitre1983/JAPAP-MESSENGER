/**
 * iter239b — HubtelSettingsCard
 * =============================
 * Admin UI to manage the four Hubtel MoMo credentials and run a live
 * credential-validity test against the Hubtel sandbox/prod endpoint.
 *
 * Strictly additive — mounted from PaymentsAdminTab.jsx. The four keys
 * live in `admin_settings` (source of truth) with env fallback handled
 * server-side. Saves take effect immediately on the next Hubtel call.
 */
import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Eye, EyeSlash, Stethoscope, CheckCircle, XCircle, Warning } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;
const MASK_PREFIX = '••••••';

export default function HubtelSettingsCard() {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

  // Stored / masked values returned by the API
  const [stored, setStored] = useState({
    hubtel_api_id: '',
    hubtel_api_key: '',
    hubtel_collection_account: '',
    hubtel_disbursement_account: '',
  });
  const [configured, setConfigured] = useState({});

  // Local edit buffer — only fields the admin has actually touched are sent
  const [edits, setEdits] = useState({});
  const [showApiId, setShowApiId] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await axios.get(`${API}/api/admin/hubtel/settings`, { withCredentials: true });
      setStored({
        hubtel_api_id: data.hubtel_api_id || '',
        hubtel_api_key: data.hubtel_api_key || '',
        hubtel_collection_account: data.hubtel_collection_account || '',
        hubtel_disbursement_account: data.hubtel_disbursement_account || '',
      });
      setConfigured(data.configured || {});
      setEdits({});
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Erreur chargement Hubtel');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const valueFor = (key) => {
    if (key in edits) return edits[key];
    return stored[key] || '';
  };

  const setField = (key, val) => {
    setEdits((prev) => ({ ...prev, [key]: val }));
  };

  const save = async () => {
    if (Object.keys(edits).length === 0) {
      toast.info('Aucun changement à enregistrer.');
      return;
    }
    // Drop masked echoes — only send fields the admin actually typed.
    const payload = {};
    for (const [k, v] of Object.entries(edits)) {
      if (typeof v === 'string' && v.startsWith(MASK_PREFIX)) continue;
      payload[k] = v;
    }
    if (Object.keys(payload).length === 0) {
      toast.info('Aucun changement réel détecté.');
      return;
    }
    setSaving(true);
    try {
      const { data } = await axios.put(
        `${API}/api/admin/hubtel/settings`, payload,
        { withCredentials: true },
      );
      toast.success(`Hubtel mis à jour (${(data.updated || []).length} champ${(data.updated || []).length > 1 ? 's' : ''})`);
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail?.message || e.response?.data?.detail || 'Erreur enregistrement');
    } finally {
      setSaving(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      // If admin has uncommitted edits, pass them through so the test
      // uses the new values without forcing them to save first.
      const body = {};
      if ('hubtel_api_id' in edits && !edits.hubtel_api_id.startsWith(MASK_PREFIX)) {
        body.api_id = edits.hubtel_api_id;
      }
      if ('hubtel_api_key' in edits && !edits.hubtel_api_key.startsWith(MASK_PREFIX)) {
        body.api_key = edits.hubtel_api_key;
      }
      if ('hubtel_collection_account' in edits) {
        body.collection_account = edits.hubtel_collection_account;
      }
      const { data } = await axios.post(
        `${API}/api/admin/hubtel/test-credentials`, body,
        { withCredentials: true, timeout: 15000 },
      );
      setTestResult(data);
      if (data.ok) {
        toast.success(data.message || 'Credentials valides');
      } else {
        toast.error(data.message || `Échec (${data.verdict})`);
      }
    } catch (e) {
      const detail = e.response?.data?.detail;
      const msg = detail?.message || detail || e.message;
      setTestResult({ ok: false, verdict: 'request_failed', message: msg });
      toast.error(msg);
    } finally {
      setTesting(false);
    }
  };

  const isEdited = Object.keys(edits).length > 0;

  return (
    <div className="jp-card-elevated p-5" data-testid="hubtel-settings-card">
      <div className="flex items-center justify-between gap-2 flex-wrap mb-1">
        <h3 className="font-['Outfit'] text-lg font-bold flex items-center gap-2">
          🇬🇭 Hubtel Mobile Money — Identifiants
        </h3>
        <StatusChip configured={configured} />
      </div>
      <p className="text-xs mb-4" style={{ color: 'var(--jp-text-muted)' }}>
        Source de vérité = base de données. Les variables d'environnement servent
        uniquement de fallback au premier démarrage. Toute modification s'applique
        sur les <strong>appels Hubtel suivants</strong> (cache invalidé à
        l'enregistrement).
      </p>

      <div className="space-y-3">
        <SecretField
          label="API ID"
          testid="hubtel-api-id"
          value={valueFor('hubtel_api_id')}
          show={showApiId}
          onToggleShow={() => setShowApiId((v) => !v)}
          onChange={(v) => setField('hubtel_api_id', v)}
          placeholder="Ex: XDM9VrA"
        />
        <SecretField
          label="API Key"
          testid="hubtel-api-key"
          value={valueFor('hubtel_api_key')}
          show={showApiKey}
          onToggleShow={() => setShowApiKey((v) => !v)}
          onChange={(v) => setField('hubtel_api_key', v)}
          placeholder="Ex: a73b646bee664204aa39f682d207ffbe"
        />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <TextField
            label="Collection Account (dépôts)"
            testid="hubtel-collection-account"
            value={valueFor('hubtel_collection_account')}
            onChange={(v) => setField('hubtel_collection_account', v)}
            placeholder="Ex: 2024252"
          />
          <TextField
            label="Disbursement Account (retraits)"
            testid="hubtel-disbursement-account"
            value={valueFor('hubtel_disbursement_account')}
            onChange={(v) => setField('hubtel_disbursement_account', v)}
            placeholder="Ex: 2021772"
          />
        </div>
      </div>

      <div className="flex flex-wrap gap-2 mt-4">
        <button
          type="button"
          onClick={save}
          disabled={saving || !isEdited}
          className="jp-btn jp-btn-primary"
          data-testid="hubtel-save-btn"
        >
          {saving ? 'Enregistrement…' : '💾 Enregistrer'}
        </button>
        <button
          type="button"
          onClick={runTest}
          disabled={testing}
          className="jp-btn"
          style={{ background: 'var(--jp-surface-secondary)', color: 'var(--jp-text)' }}
          data-testid="hubtel-test-btn"
        >
          <Stethoscope size={14} weight="duotone" /> {testing ? 'Test en cours…' : 'Tester les credentials'}
        </button>
        {isEdited && (
          <button
            type="button"
            onClick={() => setEdits({})}
            className="jp-btn jp-btn-ghost"
            data-testid="hubtel-reset-btn"
          >
            Annuler
          </button>
        )}
      </div>

      {testResult && <TestResultBanner result={testResult} />}

      <div className="mt-4 p-3 rounded-xl text-[11px] leading-relaxed"
        style={{ background: '#FEF3C7', color: '#92400E' }}>
        <strong>🔐 Sécurité :</strong> l'API ID et l'API Key ne sont jamais
        retournés en clair par l'API. Ils sont masqués (<code>••••••XXXX</code>)
        et chaque modification est journalisée dans <code>audit_logs</code>.
        Le test envoie une requête réelle à Hubtel via le proxy Fixie afin de
        valider le binding API-Key ↔ Business (code <code>4101</code> si KO).
      </div>
    </div>
  );
}

function StatusChip({ configured }) {
  const ok = configured.hubtel_api_id && configured.hubtel_api_key
    && configured.hubtel_collection_account && configured.hubtel_disbursement_account;
  return (
    <span
      className="text-[10px] px-2 py-0.5 rounded-full font-bold uppercase tracking-wider"
      style={{
        background: ok ? '#D1FAE5' : '#FEE2E2',
        color: ok ? '#065F46' : '#991B1B',
      }}
      data-testid="hubtel-status-chip"
      data-configured={ok ? 'true' : 'false'}
    >
      {ok ? '✓ Configuré' : '⚠ Incomplet'}
    </span>
  );
}

function TextField({ label, testid, value, onChange, placeholder }) {
  return (
    <div className="py-1.5">
      <label className="jp-label text-xs">{label}</label>
      <input
        type="text"
        className="jp-input text-sm font-mono"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testid}
      />
    </div>
  );
}

function SecretField({ label, testid, value, show, onToggleShow, onChange, placeholder }) {
  const isMasked = typeof value === 'string' && value.startsWith(MASK_PREFIX);
  return (
    <div className="py-1.5">
      <label className="jp-label text-xs">{label}</label>
      <div className="flex gap-1.5">
        <input
          type={show || !isMasked ? 'text' : 'password'}
          className="jp-input text-sm font-mono flex-1"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          data-testid={testid}
        />
        <button
          type="button"
          onClick={onToggleShow}
          className="jp-btn jp-btn-ghost jp-btn-sm text-xs shrink-0"
          data-testid={`${testid}-toggle-show`}
          title={show ? 'Masquer' : 'Afficher'}
        >
          {show ? <EyeSlash size={14} /> : <Eye size={14} />}
        </button>
      </div>
      {isMasked && (
        <div className="text-[10px] mt-0.5" style={{ color: 'var(--jp-text-muted)' }}>
          Valeur enregistrée masquée — tapez une nouvelle valeur pour la remplacer.
        </div>
      )}
    </div>
  );
}

function TestResultBanner({ result }) {
  const ok = result.ok;
  const Icon = ok ? CheckCircle : (result.verdict === 'network_error' ? Warning : XCircle);
  const bg = ok ? '#D1FAE5' : (result.verdict === 'network_error' ? '#FEF3C7' : '#FEE2E2');
  const fg = ok ? '#065F46' : (result.verdict === 'network_error' ? '#92400E' : '#991B1B');
  return (
    <div
      className="mt-4 p-3 rounded-xl text-xs leading-relaxed"
      style={{ background: bg, color: fg }}
      data-testid="hubtel-test-result"
      data-ok={ok ? 'true' : 'false'}
      data-verdict={result.verdict || ''}
    >
      <div className="flex items-start gap-2">
        <Icon size={18} weight="duotone" className="shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <div className="font-bold mb-1">
            {ok ? '✓ Credentials valides' : '✗ Échec du test'}
            {result.code && <span className="ml-2 opacity-70">(code {result.code})</span>}
            {result.http_status && <span className="ml-2 opacity-70">HTTP {result.http_status}</span>}
          </div>
          <div data-testid="hubtel-test-message">{result.message}</div>
          {result.description && result.description !== result.message && (
            <div className="mt-1 opacity-80 italic">{result.description}</div>
          )}
        </div>
      </div>
    </div>
  );
}
