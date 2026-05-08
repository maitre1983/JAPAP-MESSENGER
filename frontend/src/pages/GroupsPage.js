/**
 * GroupsPage — list + create + detail (inline) for social groups.
 * Minimal Phase-3 surface: list view + create modal + group detail with posts + join/leave.
 */
import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '@/context/AuthContext';
import axios from 'axios';
import { toast } from 'sonner';
import { UsersThree, Plus, Lock, Globe, CaretLeft, PaperPlaneRight, X, Check } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default function GroupsPage() {
  const { user } = useAuth();
  const [view, setView] = useState('list');      // list | detail
  const [selectedId, setSelectedId] = useState(null);
  const [groups, setGroups] = useState([]);
  const [mode, setMode] = useState('discover');  // discover | mine
  const [q, setQ] = useState('');
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (mode === 'mine') params.set('mine', 'true');
      if (q) params.set('q', q);
      const { data } = await axios.get(`${API}/api/groups?${params}`, { withCredentials: true });
      setGroups(data.items || []);
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  }, [mode, q]);

  useEffect(() => { if (view === 'list') load(); }, [view, load]);

  if (view === 'detail') return (
    <GroupDetail groupId={selectedId} onBack={() => { setView('list'); setSelectedId(null); load(); }} user={user} />
  );

  return (
    <div className="max-w-xl mx-auto p-4 pb-24" data-testid="groups-page">
      <div className="flex items-center justify-between mb-4">
        <h1 className="font-['Outfit'] text-2xl font-extrabold flex items-center gap-2">
          <UsersThree size={26} weight="duotone" style={{ color: '#0F056B' }} />
          Groupes
        </h1>
        <button onClick={() => setCreating(true)} data-testid="groups-create-btn"
          className="jp-btn jp-btn-primary jp-btn-sm">
          <Plus size={14} weight="bold" /> Créer
        </button>
      </div>
      <div className="flex gap-2 mb-3">
        <TabBtn label="Découvrir" active={mode === 'discover'} onClick={() => setMode('discover')} testid="groups-tab-discover" />
        <TabBtn label="Mes groupes" active={mode === 'mine'} onClick={() => setMode('mine')} testid="groups-tab-mine" />
      </div>
      <input className="jp-input text-sm mb-4" placeholder="Rechercher…" value={q}
        onChange={e => setQ(e.target.value)} data-testid="groups-search" />
      <div className="space-y-2" data-testid="groups-list">
        {groups.length === 0 && <div className="text-center text-sm py-8" style={{ color: 'var(--jp-text-muted)' }}>Aucun groupe pour le moment.</div>}
        {groups.map(g => (
          <button key={g.group_id} data-testid={`group-card-${g.group_id}`}
            onClick={() => { setSelectedId(g.group_id); setView('detail'); }}
            className="w-full text-left p-3 rounded-xl transition-colors hover:bg-black/5 flex items-center gap-3"
            style={{ background: 'white', border: '1px solid var(--jp-border)' }}>
            <div className="w-12 h-12 rounded-xl flex items-center justify-center shrink-0"
              style={{ background: 'linear-gradient(135deg, #0F056B, #5B21B6)', color: 'white' }}>
              <UsersThree size={22} weight="duotone" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="font-['Manrope'] font-bold text-sm flex items-center gap-1.5 truncate">
                {g.name}
                {g.privacy === 'private' ? <Lock size={12} weight="fill" style={{ color: '#B45309' }} /> : <Globe size={12} style={{ color: 'var(--jp-text-muted)' }} />}
              </div>
              <div className="text-[11px] truncate" style={{ color: 'var(--jp-text-muted)' }}>
                {g.members_count} membre{g.members_count > 1 ? 's' : ''} · {g.posts_count} post{g.posts_count > 1 ? 's' : ''}
              </div>
            </div>
            {g.is_member && (
              <span className="text-[10px] px-2 py-0.5 rounded-full font-bold"
                style={{ background: '#D1FAE5', color: '#065F46' }}>
                <Check size={9} weight="bold" className="inline mr-0.5" /> Membre
              </span>
            )}
          </button>
        ))}
      </div>
      {creating && <GroupCreateModal onClose={() => setCreating(false)} onCreated={() => { setCreating(false); load(); }} />}
    </div>
  );
}

function TabBtn({ label, active, onClick, testid }) {
  return (
    <button onClick={onClick} data-testid={testid}
      className="text-xs font-['Manrope'] font-bold px-4 py-1.5 rounded-full transition-colors"
      style={{
        background: active ? 'var(--jp-primary)' : 'var(--jp-surface-secondary)',
        color: active ? 'white' : 'var(--jp-text)',
      }}>{label}</button>
  );
}

function GroupCreateModal({ onClose, onCreated }) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [privacy, setPrivacy] = useState('public');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!name.trim()) { toast.error('Nom requis'); return; }
    setBusy(true);
    try {
      await axios.post(`${API}/api/groups`, { name: name.trim(), description, privacy }, { withCredentials: true });
      toast.success('Groupe créé');
      onCreated?.();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
    finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.6)' }}
      onClick={onClose}>
      <div className="jp-card-elevated max-w-md w-full p-5 jp-animate-scaleIn" onClick={e => e.stopPropagation()}
        data-testid="group-create-modal">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-['Outfit'] text-lg font-bold">Créer un groupe</h3>
          <button onClick={onClose}><X size={18} /></button>
        </div>
        <label className="jp-label">Nom du groupe</label>
        <input value={name} onChange={e => setName(e.target.value)} className="jp-input text-sm mb-3"
          placeholder="Ex: Dev Côte d'Ivoire" data-testid="group-create-name" autoFocus />
        <label className="jp-label">Description</label>
        <textarea rows={2} value={description} onChange={e => setDescription(e.target.value)}
          className="jp-input text-sm mb-3" data-testid="group-create-description" />
        <label className="jp-label">Confidentialité</label>
        <div className="flex gap-2 mb-4">
          {['public', 'private'].map(p => (
            <button key={p} onClick={() => setPrivacy(p)}
              data-testid={`group-privacy-${p}`}
              className="flex-1 p-3 rounded-xl border-2 text-left"
              style={{
                borderColor: privacy === p ? 'var(--jp-primary)' : 'var(--jp-border)',
                background: privacy === p ? 'var(--jp-primary-subtle)' : 'transparent',
              }}>
              <div className="flex items-center gap-1.5">
                {p === 'public' ? <Globe size={14} /> : <Lock size={14} weight="fill" />}
                <span className="text-sm font-bold">{p === 'public' ? 'Public' : 'Privé'}</span>
              </div>
              <div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>
                {p === 'public' ? 'Visible par tous' : 'Sur invitation uniquement'}
              </div>
            </button>
          ))}
        </div>
        <div className="flex gap-2 justify-end">
          <button className="jp-btn jp-btn-ghost" onClick={onClose}>Annuler</button>
          <button className="jp-btn jp-btn-primary" onClick={submit} disabled={busy} data-testid="group-create-submit">
            {busy ? '...' : 'Créer'}
          </button>
        </div>
      </div>
    </div>
  );
}

function GroupDetail({ groupId, onBack, user }) {
  const [group, setGroup] = useState(null);
  const [posts, setPosts] = useState([]);
  const [text, setText] = useState('');
  const [publishing, setPublishing] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/groups/${groupId}`, { withCredentials: true });
      setGroup(data);
      if (data.is_member || data.privacy === 'public') {
        const { data: p } = await axios.get(`${API}/api/groups/${groupId}/posts`, { withCredentials: true });
        setPosts(p.items || []);
      }
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  }, [groupId]);

  useEffect(() => { load(); }, [load]);

  const toggle = async () => {
    try {
      if (group.is_member) {
        await axios.post(`${API}/api/groups/${groupId}/leave`, {}, { withCredentials: true });
        toast.success('Vous avez quitté le groupe');
      } else {
        await axios.post(`${API}/api/groups/${groupId}/join`, {}, { withCredentials: true });
        toast.success('Bienvenue dans le groupe !');
      }
      load();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  const publish = async () => {
    if (!text.trim()) return;
    setPublishing(true);
    try {
      await axios.post(`${API}/api/groups/${groupId}/posts`, { text: text.trim() }, { withCredentials: true });
      setText(''); toast.success('Publié');
      load();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
    finally { setPublishing(false); }
  };

  if (!group) return <div className="p-8 text-center text-sm">Chargement…</div>;

  return (
    <div className="max-w-xl mx-auto p-4 pb-24" data-testid={`group-detail-${groupId}`}>
      <button onClick={onBack} data-testid="group-back"
        className="flex items-center gap-1 text-sm mb-3" style={{ color: 'var(--jp-text-muted)' }}>
        <CaretLeft size={14} /> Retour
      </button>
      <div className="p-4 rounded-2xl mb-4" style={{ background: 'white', border: '1px solid var(--jp-border)' }}>
        <div className="flex items-center gap-3 mb-2">
          <div className="w-14 h-14 rounded-2xl flex items-center justify-center shrink-0"
            style={{ background: 'linear-gradient(135deg, #0F056B, #5B21B6)', color: 'white' }}>
            <UsersThree size={26} weight="duotone" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="font-['Outfit'] text-lg font-bold">{group.name}</h2>
            <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
              {group.privacy === 'private' ? '🔒 Privé' : '🌍 Public'} · {group.members_count} membre{group.members_count > 1 ? 's' : ''}
            </div>
          </div>
          {user?.user_id !== group.owner_id && (
            <button onClick={toggle} data-testid={group.is_member ? 'group-leave' : 'group-join'}
              className={`jp-btn jp-btn-sm ${group.is_member ? 'jp-btn-ghost' : 'jp-btn-primary'}`}>
              {group.is_member ? 'Quitter' : 'Rejoindre'}
            </button>
          )}
        </div>
        {group.description && <p className="text-sm mt-2" style={{ color: 'var(--jp-text-secondary)' }}>{group.description}</p>}
      </div>

      {group.is_member && (
        <div className="p-3 rounded-2xl mb-3 flex gap-2" style={{ background: 'white', border: '1px solid var(--jp-border)' }}>
          <input value={text} onChange={e => setText(e.target.value)} data-testid="group-post-input"
            placeholder="Publier dans le groupe…" className="jp-input text-sm flex-1"
            onKeyDown={e => { if (e.key === 'Enter') publish(); }} />
          <button onClick={publish} disabled={publishing || !text.trim()}
            data-testid="group-post-submit"
            className="jp-btn jp-btn-primary jp-btn-sm">
            <PaperPlaneRight size={14} weight="fill" />
          </button>
        </div>
      )}

      <div className="space-y-2" data-testid="group-posts">
        {posts.length === 0 && <div className="text-center text-xs py-6" style={{ color: 'var(--jp-text-muted)' }}>
          Aucune publication — soyez le premier !
        </div>}
        {posts.map(p => (
          <div key={p.post_id} className="p-3 rounded-xl text-sm"
            style={{ background: 'white', border: '1px solid var(--jp-border)' }}>
            <div className="text-xs font-bold mb-1">{p.first_name} {p.last_name}</div>
            <div style={{ color: 'var(--jp-text)' }}>{p.text}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
