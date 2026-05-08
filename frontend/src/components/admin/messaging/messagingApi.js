/**
 * Shared messaging API client + variable whitelist + utilities.
 */
import axios from 'axios';

const API = process.env.REACT_APP_BACKEND_URL;

export const msgApi = {
  segments:       () => axios.get(`${API}/api/admin/messaging/segments`, { withCredentials: true }).then((r) => r.data.items),
  segmentPreview: (payload) => axios.post(`${API}/api/admin/messaging/segments/preview`, payload, { withCredentials: true }).then((r) => r.data),
  createSegment:  (p) => axios.post(`${API}/api/admin/messaging/segments`, p, { withCredentials: true }).then((r) => r.data),
  deleteSegment:  (id) => axios.delete(`${API}/api/admin/messaging/segments/${id}`, { withCredentials: true }),

  searchUsers:    (q) => axios.get(`${API}/api/admin/messaging/users/search`, { params: { q }, withCredentials: true }).then((r) => r.data.items),

  templates:      () => axios.get(`${API}/api/admin/messaging/templates`, { withCredentials: true }).then((r) => r.data.items),
  createTemplate: (p) => axios.post(`${API}/api/admin/messaging/templates`, p, { withCredentials: true }).then((r) => r.data),
  updateTemplate: (id, p) => axios.put(`${API}/api/admin/messaging/templates/${id}`, p, { withCredentials: true }).then((r) => r.data),
  deleteTemplate: (id) => axios.delete(`${API}/api/admin/messaging/templates/${id}`, { withCredentials: true }),
  aiGenerate:     (p) => axios.post(`${API}/api/admin/messaging/templates/generate-ai`, p, { withCredentials: true }).then((r) => r.data),

  campaigns:      () => axios.get(`${API}/api/admin/messaging/campaigns`, { withCredentials: true }).then((r) => r.data.items),
  campaign:       (id) => axios.get(`${API}/api/admin/messaging/campaigns/${id}`, { withCredentials: true }).then((r) => r.data),
  createCampaign: (p) => axios.post(`${API}/api/admin/messaging/campaigns`, p, { withCredentials: true }).then((r) => r.data),
  updateCampaign: (id, p) => axios.put(`${API}/api/admin/messaging/campaigns/${id}`, p, { withCredentials: true }).then((r) => r.data),
  deleteCampaign: (id) => axios.delete(`${API}/api/admin/messaging/campaigns/${id}`, { withCredentials: true }),
  testCampaign:   (id, email) => axios.post(`${API}/api/admin/messaging/campaigns/${id}/test`, { recipient_email: email || null }, { withCredentials: true }).then((r) => r.data),
  sendCampaign:   (id) => axios.post(`${API}/api/admin/messaging/campaigns/${id}/send`, { confirm: true }, { withCredentials: true }).then((r) => r.data),

  analytics:       () => axios.get(`${API}/api/admin/messaging/analytics`, { withCredentials: true }).then((r) => r.data),
  campaignStats:   (id) => axios.get(`${API}/api/admin/messaging/analytics/campaigns/${id}`, { withCredentials: true }).then((r) => r.data),
};

export const VARIABLES = [
  { key: 'first_name',        label: 'Prénom' },
  { key: 'last_name',         label: 'Nom' },
  { key: 'email',             label: 'Email' },
  { key: 'country',           label: 'Pays' },
  { key: 'plan_name',         label: 'Plan (Pro/Free)' },
  { key: 'wallet_balance',    label: 'Solde wallet' },
  { key: 'referral_count',    label: 'Nb filleuls' },
  { key: 'pending_tasks',     label: 'Tâches en attente' },
  { key: 'last_active_days',  label: 'Jours inactif' },
  { key: 'connect_points',    label: 'Points Connect' },
  { key: 'app_url',           label: 'URL app' },
];

export const STATUS_BADGES = {
  draft:    { label: 'Brouillon',   color: '#71717a', bg: '#f4f4f5' },
  queued:   { label: 'File',        color: '#0369a1', bg: '#e0f2fe' },
  sending:  { label: 'Envoi…',      color: '#9A6700', bg: '#FEF3C7' },
  sent:     { label: 'Envoyée',     color: '#047857', bg: '#d1fae5' },
  paused:   { label: 'Pause',       color: '#78350f', bg: '#fed7aa' },
  failed:   { label: 'Échec',       color: '#b91c1c', bg: '#fee2e2' },
};

export function StatusBadge({ status }) {
  const s = STATUS_BADGES[status] || STATUS_BADGES.draft;
  return (
    <span className="inline-block text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wide"
          style={{ color: s.color, background: s.bg }}>
      {s.label}
    </span>
  );
}

/**
 * Lightweight client-side preview: substitutes {{var}} with a demo dataset
 * so the admin sees what the user will get. Stays in sync with the
 * backend whitelist in email_renderer.py.
 */
export function renderPreview(template, sample = null) {
  const ctx = sample || {
    first_name: 'Aminata',
    last_name: 'Diallo',
    email: 'aminata@example.com',
    country: 'Côte d\'Ivoire',
    plan_name: 'Free',
    wallet_balance: '42.50',
    referral_count: 3,
    pending_tasks: 2,
    last_active_days: 14,
    connect_points: 12,
    app_url: window.location.origin,
  };
  return (template || '').replace(/\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}/gi, (m, k) => {
    const v = ctx[k.toLowerCase()];
    return v === undefined ? m : String(v);
  });
}
