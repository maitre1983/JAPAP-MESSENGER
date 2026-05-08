/**
 * DriverKycForm — iter97 Phase 1 strict KYC application form.
 *
 * Captures the complete legal driver profile JAPAP requires before allowing
 * the user to operate as a driver:
 *   • Personal phone + emergency contact (name + phone)
 *   • Driving license (number + issue date)
 *   • 3 mandatory document uploads:
 *       1. license image (clear photo of the license card)
 *       2. national ID card image
 *       3. selfie HOLDING the license in the right hand (anti-fraud)
 *
 * On submit → status moves to 'pending_review'. Admin must approve before
 * the user can go online or accept rides. Status banner reflects every state.
 */
import { useState, useRef } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  IdentificationBadge, Camera, UploadSimple, CheckCircle, Warning,
  Hourglass, Hash, Phone, User, Buildings, Calendar,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

// Country list — auto-detected from user profile but the rider can override.
// Sorted with priority on JAPAP target markets (XAF zone first).
const COUNTRY_OPTIONS = [
  { code: 'CM', label: '🇨🇲 Cameroun (XAF)' },
  { code: 'CI', label: '🇨🇮 Côte d\'Ivoire (XOF)' },
  { code: 'SN', label: '🇸🇳 Sénégal (XOF)' },
  { code: 'GA', label: '🇬🇦 Gabon (XAF)' },
  { code: 'CG', label: '🇨🇬 Congo (XAF)' },
  { code: 'BJ', label: '🇧🇯 Bénin (XOF)' },
  { code: 'BF', label: '🇧🇫 Burkina Faso (XOF)' },
  { code: 'ML', label: '🇲🇱 Mali (XOF)' },
  { code: 'NE', label: '🇳🇪 Niger (XOF)' },
  { code: 'TG', label: '🇹🇬 Togo (XOF)' },
  { code: 'GH', label: '🇬🇭 Ghana (GHS)' },
  { code: 'NG', label: '🇳🇬 Nigeria (NGN)' },
  { code: 'FR', label: '🇫🇷 France (EUR)' },
  { code: 'OTHER', label: '🌍 Autre' },
];

const getStatus_banner = (t) => ({
  pending_review: {
    color: '#9A6700', bg: 'rgba(247,147,26,0.10)', icon: Hourglass,
    title: 'Examen en cours',
    msg: t('driver_kyc_form.votre_dossier_a_bien_ete_recu_un_ad'),
  },
  approved: {
    color: '#047857', bg: 'rgba(16,185,129,0.10)', icon: CheckCircle,
    title: t('driver_kyc_form.compte_chauffeur_valide'),
    msg: t('driver_kyc_form.vous_pouvez_desormais_aller_en_lign'),
  },
  rejected: {
    color: '#b91c1c', bg: 'rgba(224,28,46,0.10)', icon: Warning,
    title: t('driver_kyc_form.dossier_refuse'),
    msg: t('driver_kyc_form.vos_documents_necessitent_des_corre'),
  },
  suspended: {
    color: '#7f1d1d', bg: 'rgba(127,29,29,0.10)', icon: Warning,
    title: 'Compte suspendu',
    msg: t('driver_kyc_form.votre_activite_chauffeur_est_tempor'),
  },
});


export default function DriverKycForm({ profile, onSubmitted }) {
  const { t } = useTranslation();
  const STATUS_BANNER = getStatus_banner(t);
  const status = profile?.kyc_status;
  const isLocked = status === 'suspended';
  // Pre-fill the form from the existing profile so a user resubmitting after
  // a rejection doesn't have to retype everything.
  const [form, setForm] = useState({
    vehicle_model: profile?.vehicle_model || '',
    vehicle_plate: profile?.vehicle_plate || '',
    vehicle_type: profile?.vehicle_type || 'standard',
    personal_phone: profile?.personal_phone || '',
    emergency_contact_name: profile?.emergency_contact_name || '',
    emergency_contact_phone: profile?.emergency_contact_phone || '',
    license_number: profile?.license_number || '',
    license_issue_date: profile?.license_issue_date || '',
    country_code: profile?.country_code || '',
  });
  const [docs, setDocs] = useState({
    license_image_url: profile?.license_image_url || '',
    id_card_image_url: profile?.id_card_image_url || '',
    selfie_with_license_url: profile?.selfie_with_license_url || '',
  });
  const [uploading, setUploading] = useState({});
  const [submitting, setSubmitting] = useState(false);

  const set = (k) => (v) => setForm((f) => ({ ...f, [k]: v }));
  const setDoc = (k) => (url) => setDocs((d) => ({ ...d, [k]: url }));

  const ready =
    form.vehicle_model.trim().length >= 2 &&
    form.vehicle_plate.trim().length >= 3 &&
    form.personal_phone.trim().length >= 6 &&
    form.emergency_contact_name.trim().length >= 2 &&
    form.emergency_contact_phone.trim().length >= 6 &&
    form.license_number.trim().length >= 3 &&
    form.license_issue_date &&
    docs.license_image_url &&
    docs.id_card_image_url &&
    docs.selfie_with_license_url;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!ready || submitting) return;
    setSubmitting(true);
    try {
      const r = await axios.post(
        `${API}/api/transport/driver/register`,
        { ...form, ...docs },
        { withCredentials: true },
      );
      toast.success(r.data?.message || 'Dossier soumis');
      onSubmitted?.(r.data);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur lors de la soumission');
    } finally {
      setSubmitting(false);
    }
  };

  const banner = STATUS_BANNER[status];

  return (
    <div className="space-y-4" data-testid="driver-kyc-form">
      {/* Status banner (only when a profile already exists) */}
      {banner && (
        <div
          className="p-3 rounded-xl flex items-start gap-2.5"
          style={{ background: banner.bg, border: `1px solid ${banner.color}` }}
          data-testid={`kyc-status-banner-${status}`}
        >
          <banner.icon size={18} weight="fill" style={{ color: banner.color }} className="shrink-0 mt-0.5" />
          <div className="flex-1">
            <div className="font-bold text-sm" style={{ color: banner.color }}>{banner.title}</div>
            <div className="text-xs mt-0.5 opacity-90">{banner.msg}</div>
            {status === 'rejected' && profile?.kyc_rejection_reason && (
              <div className="mt-1.5 text-xs italic">
                <span className="opacity-70">Raison :</span> {profile.kyc_rejection_reason}
              </div>
            )}
          </div>
        </div>
      )}

      {!isLocked && (
        <form onSubmit={handleSubmit} className="space-y-5">
          {/* ── Vehicle ── */}
          <Section title={t('driver_kyc_form.vehicule')} icon={Buildings}>
            <Field label="Modèle" required>
              <input
                value={form.vehicle_model}
                onChange={(e) => set('vehicle_model')(e.target.value)}
                placeholder={t('driver_kyc_form.ex_toyota_corolla_2018')}
                className="jp-input text-sm" maxLength={100}
                data-testid="kyc-vehicle-model"
              />
            </Field>
            <Field label="Plaque d'immatriculation" required>
              <input
                value={form.vehicle_plate}
                onChange={(e) => set('vehicle_plate')(e.target.value.toUpperCase())}
                placeholder={t('driver_kyc_form.ex_lt_1234_a')}
                className="jp-input text-sm uppercase tracking-wider" maxLength={30}
                data-testid="kyc-vehicle-plate"
              />
            </Field>
            <Field label="Catégorie">
              <select
                value={form.vehicle_type}
                onChange={(e) => set('vehicle_type')(e.target.value)}
                className="jp-input text-sm" data-testid="kyc-vehicle-type"
              >
                <option value="standard">{t('driver_kyc_form.standard')}</option>
                <option value="premium">{t('driver_kyc_form.premium')}</option>
              </select>
            </Field>
          </Section>

          {/* ── Personal ── */}
          <Section title={t('driver_kyc_form.coordonnees_personnelles')} icon={User}>
            <Field label="Numéro de téléphone personnel" required>
              <Prefixed icon={Phone}>
                <input
                  value={form.personal_phone}
                  onChange={(e) => set('personal_phone')(e.target.value)}
                  placeholder="+237 6XX XX XX XX"
                  type="tel" className="jp-input text-sm flex-1 pl-10" maxLength={32}
                  data-testid="kyc-personal-phone"
                />
              </Prefixed>
            </Field>
            <Field label="Pays de résidence" hint="Détecté automatiquement, modifiable">
              <select
                value={form.country_code}
                onChange={(e) => set('country_code')(e.target.value)}
                className="jp-input text-sm" data-testid="kyc-country"
              >
                <option value="">— Auto-détection IP —</option>
                {COUNTRY_OPTIONS.map((c) => (
                  <option key={c.code} value={c.code}>{c.label}</option>
                ))}
              </select>
            </Field>
          </Section>

          {/* ── Emergency contact ── */}
          <Section title={t('driver_kyc_form.contact_d_urgence')} icon={Phone}>
            <Field label="Nom du contact" required>
              <input
                value={form.emergency_contact_name}
                onChange={(e) => set('emergency_contact_name')(e.target.value)}
                placeholder={t('driver_kyc_form.ex_marie_dupont_epouse')}
                className="jp-input text-sm" maxLength={80}
                data-testid="kyc-emergency-name"
              />
            </Field>
            <Field label="Téléphone du contact" required hint="Joignable 24h/24 en cas d'urgence">
              <Prefixed icon={Phone}>
                <input
                  value={form.emergency_contact_phone}
                  onChange={(e) => set('emergency_contact_phone')(e.target.value)}
                  placeholder="+237 6XX XX XX XX"
                  type="tel" className="jp-input text-sm flex-1 pl-10" maxLength={32}
                  data-testid="kyc-emergency-phone"
                />
              </Prefixed>
            </Field>
          </Section>

          {/* ── Driving license ── */}
          <Section title={t('driver_kyc_form.permis_de_conduire')} icon={IdentificationBadge}>
            <Field label="Numéro de permis" required>
              <Prefixed icon={Hash}>
                <input
                  value={form.license_number}
                  onChange={(e) => set('license_number')(e.target.value.toUpperCase())}
                  placeholder={t('driver_kyc_form.ex_cmr_perm_xyz_2024')}
                  className="jp-input text-sm flex-1 pl-10 uppercase" maxLength={50}
                  data-testid="kyc-license-number"
                />
              </Prefixed>
            </Field>
            <Field label="Date d'établissement" required>
              <Prefixed icon={Calendar}>
                <input
                  type="date"
                  value={form.license_issue_date}
                  onChange={(e) => set('license_issue_date')(e.target.value)}
                  max={new Date().toISOString().slice(0, 10)}
                  className="jp-input text-sm flex-1 pl-10"
                  data-testid="kyc-license-date"
                />
              </Prefixed>
            </Field>
          </Section>

          {/* ── Documents ── */}
          <Section title={t('driver_kyc_form.documents_officiels')} icon={Camera}>
            <DocUpload
              testid="kyc-doc-license"
              label="📄 Photo du permis de conduire"
              hint="Recto. Recadrage net, lisible. Format JPG / PNG."
              value={docs.license_image_url}
              onChange={setDoc('license_image_url')}
              uploading={uploading.license_image_url}
              setUploading={(v) => setUploading((u) => ({ ...u, license_image_url: v }))}
            />
            <DocUpload
              testid="kyc-doc-id"
              label="🪪 Photo CNI / Pièce d'identité"
              hint="Carte nationale d'identité ou passeport, recto."
              value={docs.id_card_image_url}
              onChange={setDoc('id_card_image_url')}
              uploading={uploading.id_card_image_url}
              setUploading={(v) => setUploading((u) => ({ ...u, id_card_image_url: v }))}
            />
            <DocUpload
              testid="kyc-doc-selfie"
              label="🤳 Selfie tenant le permis dans la main droite"
              hint="Visage et permis bien visibles, mêmes plan. Anti-fraude."
              value={docs.selfie_with_license_url}
              onChange={setDoc('selfie_with_license_url')}
              uploading={uploading.selfie_with_license_url}
              setUploading={(v) => setUploading((u) => ({ ...u, selfie_with_license_url: v }))}
            />
          </Section>

          <div
            className="text-[11px] p-3 rounded-lg"
            style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}
          >
            En soumettant ce dossier, vous certifiez que les informations fournies
            sont exactes. Toute fausse déclaration entraîne le bannissement immédiat
            de la plateforme JAPAP. Statut initial : <strong>{t('driver_kyc_form.en_attente_d_examen')}</strong>.
          </div>

          <button
            type="submit" disabled={!ready || submitting}
            className="jp-btn jp-btn-primary w-full disabled:opacity-50"
            data-testid="kyc-submit"
            style={{ background: '#E01C2E' }}
          >
            {submitting ? 'Envoi en cours…' :
              status === 'rejected' ? t('driver_kyc_form.soumettre_a_nouveau') :
              status === 'approved' ? t('driver_kyc_form.mettre_a_jour_le_profil') :
              "Soumettre mon dossier"}
          </button>
        </form>
      )}
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════════════ */
function Section({ title, icon: Icon, children }) {
  return (
    <section
      className="jp-card-elevated p-4 space-y-3"
      style={{ borderRadius: '14px' }}
    >
      <header className="flex items-center gap-2 pb-2" style={{ borderBottom: '1px dashed var(--jp-border)' }}>
        <Icon size={16} weight="duotone" style={{ color: 'var(--jp-primary)' }} />
        <h3 className="font-['Outfit'] text-sm font-bold uppercase tracking-wide">{title}</h3>
      </header>
      {children}
    </section>
  );
}

function Field({ label, hint, required, children }) {
  return (
    <label className="block">
      <div className="flex items-baseline justify-between mb-1">
        <span className="text-[11px] font-bold uppercase tracking-wider" style={{ color: 'var(--jp-text-muted)' }}>
          {label} {required && <span style={{ color: '#E01C2E' }}>*</span>}
        </span>
        {hint && <span className="text-[10px] opacity-60">{hint}</span>}
      </div>
      {children}
    </label>
  );
}

function Prefixed({ icon: Icon, children }) {
  return (
    <div className="relative flex items-center">
      <Icon size={14} className="absolute left-3 z-10" style={{ color: 'var(--jp-text-muted)' }} />
      {children}
    </div>
  );
}

function DocUpload({ label, hint, value, onChange, uploading, setUploading, testid }) {
  const fileRef = useRef(null);

  const handleFile = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      toast.error('Format non supporté. Utilisez JPG ou PNG.');
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      toast.error('Image trop volumineuse (max 8 Mo).');
      return;
    }
    const fd = new FormData();
    fd.append('file', file);
    setUploading(true);
    try {
      const r = await axios.post(
        `${API}/api/upload/image?kind=driver_doc`,
        fd,
        { withCredentials: true },
      );
      const url = r.data?.main?.url;
      if (!url) throw new Error('URL manquante');
      onChange(url);
      toast.success('Document uploadé');
    } catch (err) {
      toast.error(err.response?.data?.detail || "Erreur d'upload");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  return (
    <div data-testid={testid}>
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="text-xs font-semibold">{label} <span style={{ color: '#E01C2E' }}>*</span></span>
      </div>
      <div className="flex gap-2 items-stretch">
        {value ? (
          <div className="relative group flex-1">
            <img
              src={value}
              alt={label}
              className="w-full h-28 object-cover rounded-lg"
              style={{ border: '1px solid var(--jp-border)' }}
            />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="absolute inset-0 bg-black/0 hover:bg-black/40 transition-colors flex items-center justify-center text-white text-xs font-bold opacity-0 hover:opacity-100 rounded-lg"
            >
              Remplacer
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
            className="flex-1 h-28 rounded-lg flex flex-col items-center justify-center gap-1 transition-colors disabled:opacity-50"
            style={{
              border: '2px dashed var(--jp-border)',
              background: 'var(--jp-surface-secondary)',
              color: 'var(--jp-text-muted)',
            }}
            data-testid={`${testid}-trigger`}
          >
            {uploading ? (
              <Hourglass size={20} className="animate-spin" />
            ) : (
              <UploadSimple size={22} weight="duotone" />
            )}
            <span className="text-[11px]">{uploading ? 'Upload…' : 'Cliquer pour ajouter'}</span>
          </button>
        )}
      </div>
      <input
        ref={fileRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        capture="environment"
        onChange={handleFile}
        className="hidden"
      />
      {hint && <p className="text-[10px] mt-1.5 opacity-60">{hint}</p>}
    </div>
  );
}
