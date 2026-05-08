/**
 * JAPAP — Push notifications toggle (iter71, OneSignal-backed)
 *
 * Replaces iter70's VAPID toggle. UI is mostly identical — the only
 * semantic change is that on "Enable" we also call OneSignal.login(user_id)
 * (via `identifyUser`) so the backend can target push by our internal
 * `user_id` via External ID.
 *
 * Handles the same messy edge cases as iter70:
 *   • permission=denied → explain the user must unblock in browser settings
 *   • unsupported browser (iOS <16.4 Safari, some in-app webviews)
 *   • already subscribed → show "Envoyer un test" + toggle OFF
 */
import { useEffect, useState } from "react";
import { BellRinging, BellSlash, CheckCircle, Warning } from "@phosphor-icons/react";
import { toast } from "sonner";
import axios from "axios";
import { useAuth } from "@/context/AuthContext";
import { useTranslation } from 'react-i18next';
import {
  isPushSupported,
  currentPermission,
  getSubscription,
  subscribePush,
  unsubscribePush,
  identifyUser,
} from "@/services/webpush";

const API = process.env.REACT_APP_BACKEND_URL;

export default function PushNotificationsToggle() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [supported, setSupported] = useState(true);
  const [perm, setPerm] = useState("default");
  const [subscribed, setSubscribed] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    (async () => {
      const ok = isPushSupported();
      setSupported(ok);
      if (!ok) return;
      setPerm(currentPermission());
      const sub = await getSubscription();
      setSubscribed(!!sub);
    })();
  }, []);

  if (!supported) {
    return (
      <div
        className="flex items-start gap-3 p-4 rounded-xl"
        style={{ background: "rgba(255,193,7,0.08)", border: "1px solid rgba(255,193,7,0.25)" }}
        data-testid="push-unsupported"
      >
        <Warning size={22} weight="duotone" style={{ color: "#FFC107" }} />
        <div className="text-sm" style={{ color: "var(--jp-text-muted)" }}>
          Ce navigateur ne prend pas en charge les notifications Web Push.
          Installez JAPAP depuis votre écran d'accueil pour les activer (iOS 16.4+).
        </div>
      </div>
    );
  }

  const handleEnable = async () => {
    setBusy(true);
    try {
      const res = await subscribePush();
      if (res.ok) {
        // Tag this subscription with our internal user_id so the backend
        // can target pushes via OneSignal External ID.
        if (user?.user_id) {
          await identifyUser(user.user_id);
        }
        setSubscribed(true);
        setPerm("granted");
        toast.success("Notifications activées", {
          description: t('push_notifications_toggle.vous_recevrez_tips_messages_et_vire'),
        });
      } else if (res.reason === "denied") {
        setPerm("denied");
        toast.error("Notifications bloquées", {
          description: t('push_notifications_toggle.debloquez_les_dans_les_parametres_d'),
        });
      } else if (res.reason === "sdk_unavailable") {
        toast.error("OneSignal indisponible", {
          description: t('push_notifications_toggle.le_sdk_n_a_pas_pu_etre_charge_verif'),
        });
      } else {
        toast.error("Échec d'activation", {
          description: res.reason || "Erreur inconnue",
        });
      }
    } finally {
      setBusy(false);
    }
  };

  const handleDisable = async () => {
    setBusy(true);
    try {
      await unsubscribePush();
      setSubscribed(false);
      toast.success("Notifications désactivées");
    } finally {
      setBusy(false);
    }
  };

  const handleTest = async () => {
    setBusy(true);
    try {
      await axios.post(`${API}/api/push/test-vapid`, {}, { withCredentials: true });
      toast.info("Test envoyé", { description: t('push_notifications_toggle.verifiez_vos_notifications') });
    } catch (e) {
      if (e?.response?.status === 403) {
        toast.warning("Admin uniquement", { description: t('push_notifications_toggle.le_test_push_est_reserve_aux_admins') });
      } else {
        toast.error("Échec du test", { description: e?.message });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="p-4 rounded-xl space-y-3"
      style={{ background: "var(--jp-card)", border: "1px solid var(--jp-border)" }}
      data-testid="push-toggle"
    >
      <div className="flex items-start gap-3">
        <div
          className="flex-shrink-0 w-10 h-10 rounded-full flex items-center justify-center"
          style={{
            background: subscribed ? "rgba(34,197,94,0.12)" : "rgba(224,28,46,0.1)",
            color: subscribed ? "#22C55E" : "#E01C2E",
          }}
        >
          {subscribed
            ? <BellRinging size={20} weight="fill" />
            : <BellSlash size={20} weight="fill" />}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold" style={{ color: "var(--jp-text)" }}>
            Notifications Web Push
          </p>
          <p className="text-xs mt-0.5" style={{ color: "var(--jp-text-muted)" }}>
            {subscribed
              ? t('push_notifications_toggle.actives_vous_serez_notifiee_pour_ti')
              : perm === "denied"
                ? t('push_notifications_toggle.bloquees_par_le_navigateur_debloque')
                : t('push_notifications_toggle.recevez_tips_virements_et_messages')}
          </p>
        </div>
      </div>

      <div className="flex items-center gap-2">
        {!subscribed && perm !== "denied" && (
          <button
            onClick={handleEnable}
            disabled={busy}
            data-testid="push-enable-btn"
            className="px-4 py-2 rounded-full text-sm font-semibold disabled:opacity-50"
            style={{ background: "#E01C2E", color: "white" }}
          >
            {busy ? "…" : "Activer"}
          </button>
        )}
        {subscribed && (
          <>
            <button
              onClick={handleTest}
              disabled={busy}
              data-testid="push-test-btn"
              className="px-4 py-2 rounded-full text-sm font-medium disabled:opacity-50"
              style={{ border: "1px solid var(--jp-border)", color: "var(--jp-text)" }}
            >
              Envoyer un test
            </button>
            <button
              onClick={handleDisable}
              disabled={busy}
              data-testid="push-disable-btn"
              className="px-4 py-2 rounded-full text-sm font-medium disabled:opacity-50"
              style={{ border: "1px solid var(--jp-border)", color: "var(--jp-text-muted)" }}
            >
              Désactiver
            </button>
          </>
        )}
        {subscribed && (
          <span className="ml-auto inline-flex items-center gap-1 text-xs" style={{ color: "#22C55E" }}>
            <CheckCircle size={14} weight="fill" /> Actif
          </span>
        )}
      </div>
    </div>
  );
}
