/**
 * RecruitAdminTab — iter141seven
 *
 * Admin UI for the viral recruit reward system. Lets superadmins tune
 * every reward + threshold, view the live leaderboard, and toggle the
 * feature off entirely if needed.
 */
import { useEffect, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { Confetti, Crown, FloppyDisk, ToggleLeft } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function RecruitAdminTab() {
  const [settings, setSettings] = useState(null);
  const [defaults, setDefaults] = useState({});
  const [draft, setDraft] = useState({});
  const [board, setBoard] = useState([]);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const [a, b] = await Promise.all([
        axios.get(`${API}/api/admin/recruit/settings`, { withCredentials: true }),
        axios.get(`${API}/api/recruit/leaderboard`),
      ]);
      setSettings(a.data.settings);
      setDefaults(a.data.defaults || {});
      setDraft(a.data.settings);
      setBoard(b.data.items || []);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Chargement impossible');
    } finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const updateField = (k, v) => setDraft(d => ({ ...d, [k]: v }));

  const save = async () => {
    setSaving(true);
    try {
      const payload = { ...draft };
      ['recruit_per_friend_points', 'recruit_buzz_threshold', 'recruit_buzz_bonus_points',
       'recruit_leaderboard_period_days', 'recruit_leaderboard_size'].forEach(k => {
        payload[k] = Number(payload[k]);
      });
      payload.recruit_enabled = !!payload.recruit_enabled;
      const { data } = await axios.put(`${API}/api/admin/recruit/settings`, payload, { withCredentials: true });
      setSettings(data.settings);
      setDraft(data.settings);
      toast.success('Paramètres recruteurs enregistrés');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Échec de la sauvegarde');
    } finally { setSaving(false); }
  };

  if (loading || !settings) return <div className="p-6 text-sm" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>;

  return (
    <div className="space-y-6" data-testid="recruit-admin-tab">
      <div className="jp-card-elevated p-5">
        <div className="flex items-center gap-2 mb-4">
          <Confetti size={22} weight="fill" color="#FFD700" />
          <h2 className="font-['Outfit'] text-lg font-bold" style={{ color: 'var(--jp-text)' }}>
            Recruteurs viraux — récompenses
          </h2>
        </div>
        <p className="text-xs mb-4" style={{ color: 'var(--jp-text-muted)' }}>
          Configure les points et bonus attribués aux initiateurs de défis pour chaque ami invité qui joue.
          Les changements s'appliquent immédiatement aux nouveaux defis (les credits déjà attribués ne sont pas recalculés).
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Toggle2
            label="Système recruteur actif"
            value={!!draft.recruit_enabled}
            onChange={v => updateField('recruit_enabled', v)}
            testid="recruit-enabled-toggle"
          />
          <Field
            label="Points par ami recruté"
            helper={`Défaut: ${defaults.recruit_per_friend_points ?? 50}`}
            value={draft.recruit_per_friend_points}
            onChange={v => updateField('recruit_per_friend_points', v)}
            testid="recruit-per-friend"
          />
          <Field
            label="Seuil bonus 'Roi du Buzz'"
            helper={`# d'amis recrutés avant le bonus. Défaut: ${defaults.recruit_buzz_threshold ?? 3}`}
            value={draft.recruit_buzz_threshold}
            onChange={v => updateField('recruit_buzz_threshold', v)}
            testid="recruit-buzz-threshold"
          />
          <Field
            label="Bonus 'Roi du Buzz' (points)"
            helper={`Défaut: ${defaults.recruit_buzz_bonus_points ?? 200}`}
            value={draft.recruit_buzz_bonus_points}
            onChange={v => updateField('recruit_buzz_bonus_points', v)}
            testid="recruit-buzz-bonus"
          />
          <Field
            label="Libellé du badge"
            helper="Affiché sur le profil du gagnant"
            value={draft.recruit_buzz_badge_label}
            onChange={v => updateField('recruit_buzz_badge_label', v)}
            type="text"
            testid="recruit-buzz-label"
          />
          <Field
            label="Emoji du badge"
            helper="Affiché à côté du nom du badge"
            value={draft.recruit_buzz_badge_emoji}
            onChange={v => updateField('recruit_buzz_badge_emoji', v)}
            type="text"
            testid="recruit-buzz-emoji"
          />
          <Field
            label="Fenêtre leaderboard (jours)"
            helper={`Période roulante. Défaut: ${defaults.recruit_leaderboard_period_days ?? 7}`}
            value={draft.recruit_leaderboard_period_days}
            onChange={v => updateField('recruit_leaderboard_period_days', v)}
            testid="recruit-period"
          />
          <Field
            label="Taille leaderboard"
            helper={`# de recruteurs affichés. Défaut: ${defaults.recruit_leaderboard_size ?? 10}`}
            value={draft.recruit_leaderboard_size}
            onChange={v => updateField('recruit_leaderboard_size', v)}
            testid="recruit-size"
          />
        </div>

        <button onClick={save}
                disabled={saving}
                data-testid="recruit-save-btn"
                className="mt-5 px-5 py-2.5 rounded-full font-bold text-sm flex items-center gap-2 disabled:opacity-50"
                style={{ background: 'var(--jp-primary)', color: '#fff' }}>
          <FloppyDisk size={16} weight="bold" />
          {saving ? 'Enregistrement…' : 'Enregistrer'}
        </button>
      </div>

      <div className="jp-card-elevated p-5">
        <div className="flex items-center gap-2 mb-3">
          <Crown size={20} weight="fill" color="#FFD700" />
          <h3 className="font-['Outfit'] text-base font-bold" style={{ color: 'var(--jp-text)' }}>
            Top recruteurs (live)
          </h3>
        </div>
        {board.length === 0 ? (
          <p className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>Aucun recruteur sur la période.</p>
        ) : (
          <ol className="space-y-2">
            {board.map(it => (
              <li key={it.user_id} className="flex items-center gap-3 p-2 rounded-lg"
                  style={{ background: 'var(--jp-surface-secondary)' }}>
                <span className="w-7 text-center font-bold">#{it.rank}</span>
                <div className="w-9 h-9 rounded-full flex items-center justify-center overflow-hidden"
                     style={{ background: 'rgba(15,5,107,0.08)' }}>
                  {it.avatar ? <img src={it.avatar} alt="" className="w-full h-full object-cover" /> : (it.name?.[0] || '?')}
                </div>
                <div className="flex-1">
                  <div className="font-bold text-sm" style={{ color: 'var(--jp-text)' }}>{it.name}</div>
                  <div className="text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
                    {it.recruits} amis · {it.points} pts
                  </div>
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

function Field({ label, helper, value, onChange, type = 'number', testid }) {
  return (
    <div>
      <label className="text-xs font-bold block mb-1" style={{ color: 'var(--jp-text)' }}>{label}</label>
      <input type={type} value={value ?? ''}
             onChange={e => onChange(type === 'number' ? Number(e.target.value) : e.target.value)}
             data-testid={testid}
             className="jp-input w-full" />
      {helper && <p className="text-[10px] mt-1" style={{ color: 'var(--jp-text-muted)' }}>{helper}</p>}
    </div>
  );
}

function Toggle2({ label, value, onChange, testid }) {
  return (
    <div className="flex items-center justify-between p-2 rounded-lg"
         style={{ background: 'var(--jp-surface-secondary)' }}>
      <div className="flex items-center gap-2">
        <ToggleLeft size={18} weight="duotone" />
        <span className="text-sm font-bold" style={{ color: 'var(--jp-text)' }}>{label}</span>
      </div>
      <button onClick={() => onChange(!value)} data-testid={testid}
              className="px-3 py-1 rounded-full text-xs font-bold transition-all"
              style={{ background: value ? '#22c55e' : 'rgba(0,0,0,0.10)', color: value ? '#fff' : 'var(--jp-text)' }}>
        {value ? 'Activé' : 'Désactivé'}
      </button>
    </div>
  );
}
