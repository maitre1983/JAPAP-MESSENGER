/**
 * PagesPage — list + create + detail for public pages (brands, orgs, creators).
 * Owner-only posting, follow/unfollow for everyone else.
 */
import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '@/context/AuthContext';
import axios from 'axios';
import { toast } from 'sonner';
import { FileText, Plus, Check, Star, CaretLeft, PaperPlaneRight, X } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

const CATEGORIES = ['brand', 'creator', 'business', 'nonprofit', 'media', 'other'];

export default function PagesPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [view, setView] = useState('list');
  const [selectedId, setSelectedId] = useState(null);
  const [items, setItems] = useState([]);
  const [mode, setMode] = useState('discover');
  const [q, setQ] = useState('');
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (mode === 'mine') params.set('mine', 'true');
      if (q) params.set('q', q);
      const { data } = await axios.get(`${API}/api/pages?${params}`, { withCredentials: true });
      setItems(data.items || []);
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  }, [mode, q]);

  useEffect(() => { if (view === 'list') load(); }, [view, load]);

  if (view === 'detail') return (
    <PageDetail pageId={selectedId} onBack={() => { setView('list'); setSelectedId(null); load(); }} user={user} />
  );

  return (
    <div className="max-w-xl mx-auto p-4 pb-24" data-testid="pages-page">
      <div className="flex items-center justify-between mb-4">
        <h1 className="font-['Outfit'] text-2xl font-extrabold flex items-center gap-2">
          <FileText size={26} weight="duotone" style={{ color: '#E01C2E' }} />
          Pages
        </h1>
        <button onClick={() => setCreating(true)} data-testid="pages-create-btn"
          className="jp-btn jp-btn-primary jp-btn-sm">
          <Plus size={14} weight="bold" /> Créer
        </button>
      </div>
      <div className="flex gap-2 mb-3">
        <TabBtn label="Découvrir" active={mode === 'discover'} onClick={() => setMode('discover')} testid="pages-tab-discover" />
        <TabBtn label="Suivies" active={mode === 'mine'} onClick={() => setMode('mine')} testid="pages-tab-mine" />
      </div>
      <input className="jp-input text-sm mb-4" placeholder="Rechercher…" value={q}
        onChange={e => setQ(e.target.value)} data-testid="pages-search" />
      <div className="space-y-2" data-testid="pages-list">
        {items.length === 0 && <div className="text-center text-sm py-8" style={{ color: 'var(--jp-text-muted)' }}>Aucune page pour le moment.</div>}
        {items.map(p => (
          <button key={p.page_id} data-testid={`page-card-${p.page_id}`}
            onClick={() => { setSelectedId(p.page_id); setView('detail'); }}
            className="w-full text-left p-3 rounded-xl transition-colors hover:bg-black/5 flex items-center gap-3"
            style={{ background: 'white', border: '1px solid var(--jp-border)' }}>
            <div className="w-12 h-12 rounded-xl flex items-center justify-center shrink-0"
              style={{ background: 'linear-gradient(135deg, #E01C2E, #9333EA)', color: 'white' }}>
              <FileText size={22} weight="duotone" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="font-['Manrope'] font-bold text-sm flex items-center gap-1.5 truncate">
                {p.name}
                {p.verified && <Star size={11} weight="fill" style={{ color: '#3B82F6' }} />}
              </div>
              <div className="text-[11px] truncate" style={{ color: 'var(--jp-text-muted)' }}>
                {p.category} · {p.followers_count} abonnés · {p.posts_count} posts
              </div>
            </div>
            {p.is_following && (
              <span className="text-[10px] px-2 py-0.5 rounded-full font-bold"
                style={{ background: '#D1FAE5', color: '#065F46' }}>
                <Check size={9} weight="bold" className="inline mr-0.5" /> Suivi
              </span>
            )}
          </button>
        ))}
      </div>
      {creating && <PageCreateModal onClose={() => setCreating(false)} onCreated={() => { setCreating(false); load(); }} />}
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

function PageCreateModal({ onClose, onCreated }) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('brand');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!name.trim()) { toast.error('Nom requis'); return; }
    setBusy(true);
    try {
      await axios.post(`${API}/api/pages`, { name: name.trim(), description, category }, { withCredentials: true });
      toast.success('Page créée');
      onCreated?.();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
    finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.6)' }}
      onClick={onClose}>
      <div className="jp-card-elevated max-w-md w-full p-5 jp-animate-scaleIn" onClick={e => e.stopPropagation()}
        data-testid="page-create-modal">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-['Outfit'] text-lg font-bold">Créer une page</h3>
          <button onClick={onClose}><X size={18} /></button>
        </div>
        <label className="jp-label">Nom</label>
        <input value={name} onChange={e => setName(e.target.value)}
          className="jp-input text-sm mb-3" autoFocus data-testid="page-create-name"
          placeholder={t('pages.ex_japap_studio')} />
        <label className="jp-label">Catégorie</label>
        <select value={category} onChange={e => setCategory(e.target.value)}
          className="jp-input text-sm mb-3" data-testid="page-create-category">
          {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <label className="jp-label">Description</label>
        <textarea rows={2} value={description} onChange={e => setDescription(e.target.value)}
          className="jp-input text-sm mb-4" data-testid="page-create-description" />
        <div className="flex gap-2 justify-end">
          <button className="jp-btn jp-btn-ghost" onClick={onClose}>Annuler</button>
          <button className="jp-btn jp-btn-primary" onClick={submit} disabled={busy} data-testid="page-create-submit">
            {busy ? '...' : 'Créer'}
          </button>
        </div>
      </div>
    </div>
  );
}

function PageDetail({ pageId, onBack, user }) {
  const [page, setPage] = useState(null);
  const [posts, setPosts] = useState([]);
  const [text, setText] = useState('');
  const [publishing, setPublishing] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/pages/${pageId}`, { withCredentials: true });
      setPage(data);
      const { data: p } = await axios.get(`${API}/api/pages/${pageId}/posts`, { withCredentials: true });
      setPosts(p.items || []);
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  }, [pageId]);

  useEffect(() => { load(); }, [load]);

  const toggle = async () => {
    try {
      const url = page.is_following ? `/api/pages/${pageId}/unfollow` : `/api/pages/${pageId}/follow`;
      await axios.post(`${API}${url}`, {}, { withCredentials: true });
      toast.success(page.is_following ? 'Vous ne suivez plus cette page' : 'Page suivie !');
      load();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
  };

  const publish = async () => {
    if (!text.trim()) return;
    setPublishing(true);
    try {
      await axios.post(`${API}/api/pages/${pageId}/posts`, { text: text.trim() }, { withCredentials: true });
      setText(''); toast.success('Publié');
      load();
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
    finally { setPublishing(false); }
  };

  if (!page) return <div className="p-8 text-center text-sm">Chargement…</div>;

  return (
    <div className="max-w-xl mx-auto p-4 pb-24" data-testid={`page-detail-${pageId}`}>
      <button onClick={onBack} data-testid="page-back"
        className="flex items-center gap-1 text-sm mb-3" style={{ color: 'var(--jp-text-muted)' }}>
        <CaretLeft size={14} /> Retour
      </button>
      <div className="p-4 rounded-2xl mb-4" style={{ background: 'white', border: '1px solid var(--jp-border)' }}>
        <div className="flex items-center gap-3 mb-2">
          <div className="w-14 h-14 rounded-2xl flex items-center justify-center shrink-0"
            style={{ background: 'linear-gradient(135deg, #E01C2E, #9333EA)', color: 'white' }}>
            <FileText size={26} weight="duotone" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="font-['Outfit'] text-lg font-bold flex items-center gap-1">
              {page.name}
              {page.verified && <Star size={14} weight="fill" style={{ color: '#3B82F6' }} />}
            </h2>
            <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
              {page.category} · {page.followers_count} abonnés
            </div>
          </div>
          {!page.is_owner && (
            <button onClick={toggle} data-testid={page.is_following ? 'page-unfollow' : 'page-follow'}
              className={`jp-btn jp-btn-sm ${page.is_following ? 'jp-btn-ghost' : 'jp-btn-primary'}`}>
              {page.is_following ? 'Suivi' : 'Suivre'}
            </button>
          )}
        </div>
        {page.description && <p className="text-sm mt-2" style={{ color: 'var(--jp-text-secondary)' }}>{page.description}</p>}
      </div>

      {page.is_owner && (
        <div className="p-3 rounded-2xl mb-3 flex gap-2" style={{ background: 'white', border: '1px solid var(--jp-border)' }}>
          <input value={text} onChange={e => setText(e.target.value)} data-testid="page-post-input"
            placeholder="Publier sur votre page…" className="jp-input text-sm flex-1"
            onKeyDown={e => { if (e.key === 'Enter') publish(); }} />
          <button onClick={publish} disabled={publishing || !text.trim()} data-testid="page-post-submit"
            className="jp-btn jp-btn-primary jp-btn-sm">
            <PaperPlaneRight size={14} weight="fill" />
          </button>
        </div>
      )}

      <div className="space-y-2" data-testid="page-posts">
        {posts.length === 0 && <div className="text-center text-xs py-6" style={{ color: 'var(--jp-text-muted)' }}>
          Aucune publication.
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
