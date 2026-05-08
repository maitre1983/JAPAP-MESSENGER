/**
 * StatsAdminTab — KPIs for JAPAP admins (Phase 4 of feed refonte).
 * Sections: overview cards, content time-series, engagement totals, top posts/reels/creators.
 */
import { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  ChartBar, UsersThree, FileText, Heart, ChatCircle, ShareNetwork, HandCoins,
  VideoCamera, ArrowUpRight, Crown, TrendUp,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

const PERIODS = [
  { id: 'day',   label: '24h' },
  { id: 'week',  label: '7 j' },
  { id: 'month', label: '30 j' },
  { id: 'year',  label: '12 m' },
];

export default function StatsAdminTab() {
  const [period, setPeriod] = useState('month');
  const [overview, setOverview] = useState(null);
  const [content, setContent] = useState(null);
  const [engagement, setEngagement] = useState(null);
  const [top, setTop] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ov, co, en, tp] = await Promise.all([
        axios.get(`${API}/api/admin/stats/overview`, { withCredentials: true }),
        axios.get(`${API}/api/admin/stats/content?period=${period}`, { withCredentials: true }),
        axios.get(`${API}/api/admin/stats/engagement?period=${period}`, { withCredentials: true }),
        axios.get(`${API}/api/admin/stats/top?period=${period}&limit=5`, { withCredentials: true }),
      ]);
      setOverview(ov.data);
      setContent(co.data);
      setEngagement(en.data);
      setTop(tp.data);
    } catch (e) { toast.error(e.response?.data?.detail || 'Erreur'); }
    finally { setLoading(false); }
  }, [period]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="space-y-5 jp-animate-fadeIn" data-testid="stats-admin-tab">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h2 className="font-['Outfit'] text-xl font-bold flex items-center gap-2">
          <ChartBar size={22} weight="duotone" style={{ color: 'var(--jp-primary)' }} /> Statistiques
        </h2>
        <div className="flex gap-1 rounded-full p-1" style={{ background: 'var(--jp-surface-secondary)' }}>
          {PERIODS.map(p => (
            <button key={p.id} onClick={() => setPeriod(p.id)} data-testid={`stats-period-${p.id}`}
              className="text-xs px-3 py-1 rounded-full font-['Manrope'] font-bold transition-colors"
              style={{
                background: period === p.id ? 'var(--jp-primary)' : 'transparent',
                color: period === p.id ? 'white' : 'var(--jp-text)',
              }}>{p.label}</button>
          ))}
        </div>
      </div>

      {loading && <div className="text-center text-xs py-4" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>}

      {/* Overview cards */}
      {overview && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3" data-testid="stats-overview">
          <Card icon={UsersThree}  label="Utilisateurs"  value={overview.totals.total_users} />
          <Card icon={FileText}    label="Posts"          value={overview.totals.total_posts} />
          <Card icon={VideoCamera} label="Reels"          value={overview.totals.total_reels} />
          <Card icon={Heart}       label="Likes totaux"   value={overview.totals.total_likes} />
          <Card icon={ChatCircle}  label="Commentaires"   value={overview.totals.total_comments} />
          <Card icon={ShareNetwork} label="Partages"      value={overview.totals.total_shares} />
          <Card icon={UsersThree}  label="Groupes"        value={overview.totals.total_groups} />
          <Card icon={FileText}    label="Pages"          value={overview.totals.total_pages} />
        </div>
      )}

      {/* Live activity 24h */}
      {overview?.last_24h && (
        <div className="jp-card-elevated p-4" data-testid="stats-24h">
          <div className="flex items-center gap-2 mb-2">
            <TrendUp size={16} weight="duotone" style={{ color: '#10B981' }} />
            <h3 className="font-['Outfit'] font-bold text-sm">Activité live (24h)</h3>
          </div>
          <div className="grid grid-cols-4 gap-3 text-center">
            <MiniStat label="Posts" value={overview.last_24h.posts_24h} />
            <MiniStat label="Créateurs actifs" value={overview.last_24h.active_posters_24h} />
            <MiniStat label="Likes" value={overview.last_24h.likes_24h} />
            <MiniStat label="Commentaires" value={overview.last_24h.comments_24h} />
          </div>
        </div>
      )}

      {/* Engagement */}
      {engagement && (
        <div className="jp-card-elevated p-4" data-testid="stats-engagement">
          <h3 className="font-['Outfit'] font-bold text-sm mb-3">Engagement — {PERIODS.find(p=>p.id===period)?.label}</h3>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <Badge icon={Heart}        color="#E01C2E" label="Likes"    value={engagement.totals.likes} />
            <Badge icon={ChatCircle}   color="#3B82F6" label="Comm."    value={engagement.totals.comments} />
            <Badge icon={ShareNetwork} color="#10B981" label="Partages" value={engagement.totals.shares} />
            <Badge icon={HandCoins}    color="#F59E0B" label="Tips (USD)" value={`$${engagement.totals.tips_usd.toFixed(2)}`} />
            <Badge icon={UsersThree}   color="#5B21B6" label="Créateurs" value={engagement.totals.active_creators} />
            <Badge icon={ChatCircle}   color="#6B7280" label="Commenteurs" value={engagement.totals.active_commenters} />
          </div>
        </div>
      )}

      {/* Content time series (mini spark) */}
      {content && content.series && content.series.length > 0 && (
        <div className="jp-card-elevated p-4" data-testid="stats-content-series">
          <h3 className="font-['Outfit'] font-bold text-sm mb-3">Contenu publié — {content.total} total</h3>
          <SparkBars data={content.series} />
        </div>
      )}

      {/* Top creators */}
      {top?.top_creators && top.top_creators.length > 0 && (
        <div className="jp-card-elevated p-4" data-testid="stats-top-creators">
          <h3 className="font-['Outfit'] font-bold text-sm mb-3 flex items-center gap-2">
            <Crown size={16} weight="duotone" style={{ color: '#F59E0B' }} /> Top créateurs
          </h3>
          <div className="divide-y" style={{ borderColor: 'var(--jp-border)' }}>
            {top.top_creators.map((c, i) => (
              <div key={c.user_id} className="flex items-center gap-3 py-2" data-testid={`stats-creator-${c.user_id}`}>
                <div className="w-6 text-center text-xs font-bold" style={{ color: i < 3 ? '#F59E0B' : 'var(--jp-text-muted)' }}>
                  {i + 1}
                </div>
                <div className="jp-avatar jp-avatar-sm jp-avatar-primary">{(c.first_name?.[0] || '?').toUpperCase()}</div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-['Manrope'] font-semibold flex items-center gap-1">
                    {c.first_name} {c.last_name}
                    {c.is_pro && <Crown size={10} weight="fill" style={{ color: '#F59E0B' }} />}
                  </div>
                  <div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>
                    {c.posts} posts · {c.total_likes} likes · {c.total_comments} comm.
                  </div>
                </div>
                <div className="text-sm font-['Outfit'] font-bold" style={{ color: 'var(--jp-primary)' }}>
                  {c.total_likes + c.total_comments * 2}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Top posts */}
      {top?.top_posts && top.top_posts.length > 0 && (
        <div className="jp-card-elevated p-4" data-testid="stats-top-posts">
          <h3 className="font-['Outfit'] font-bold text-sm mb-3">Top publications</h3>
          <div className="space-y-2">
            {top.top_posts.map((p, i) => (
              <div key={p.post_id} className="p-2 rounded-lg flex items-start gap-2"
                style={{ background: 'var(--jp-surface-secondary)' }}
                data-testid={`stats-post-${p.post_id}`}>
                <div className="text-xs font-bold w-5 text-center" style={{ color: i < 3 ? '#F59E0B' : 'var(--jp-text-muted)' }}>
                  {i + 1}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-bold truncate">{p.first_name} {p.last_name}</div>
                  <div className="text-[11px] line-clamp-2" style={{ color: 'var(--jp-text-secondary)' }}>{p.text || '(média)'}</div>
                  <div className="flex gap-3 mt-1 text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>
                    <span>❤️ {p.likes_count}</span>
                    <span>💬 {p.comments_count}</span>
                    <span>🔁 {p.shares_count}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Top reels */}
      {top?.top_reels && top.top_reels.length > 0 && (
        <div className="jp-card-elevated p-4" data-testid="stats-top-reels">
          <h3 className="font-['Outfit'] font-bold text-sm mb-3 flex items-center gap-2">
            <VideoCamera size={16} weight="duotone" style={{ color: '#E01C2E' }} /> Top reels
          </h3>
          <div className="space-y-2">
            {top.top_reels.map((r, i) => (
              <div key={r.post_id} className="p-2 rounded-lg flex items-start gap-2"
                style={{ background: 'var(--jp-surface-secondary)' }}>
                <div className="text-xs font-bold w-5 text-center" style={{ color: i < 3 ? '#F59E0B' : 'var(--jp-text-muted)' }}>
                  {i + 1}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-bold truncate">{r.first_name} {r.last_name}</div>
                  <div className="text-[11px] line-clamp-2" style={{ color: 'var(--jp-text-secondary)' }}>{r.text || '(reel)'}</div>
                </div>
                <div className="text-xs font-bold">❤️ {r.likes_count}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Card({ icon: Icon, label, value }) {
  return (
    <div className="p-3 rounded-xl" style={{ background: 'white', border: '1px solid var(--jp-border)' }}>
      <div className="flex items-center justify-between">
        <Icon size={16} weight="duotone" style={{ color: 'var(--jp-primary)' }} />
        <ArrowUpRight size={10} style={{ color: 'var(--jp-text-muted)' }} />
      </div>
      <div className="font-['Outfit'] text-2xl font-extrabold mt-2" style={{ color: 'var(--jp-text)' }}>{value?.toLocaleString('fr-FR')}</div>
      <div className="text-[10px] uppercase tracking-wider" style={{ color: 'var(--jp-text-muted)' }}>{label}</div>
    </div>
  );
}

function MiniStat({ label, value }) {
  return (
    <div>
      <div className="font-['Outfit'] text-lg font-extrabold" style={{ color: '#10B981' }}>{value?.toLocaleString('fr-FR')}</div>
      <div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>{label}</div>
    </div>
  );
}

function Badge({ icon: Icon, color, label, value }) {
  return (
    <div className="flex items-center gap-2 p-2 rounded-lg" style={{ background: 'var(--jp-surface-secondary)' }}>
      <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: `${color}22`, color }}>
        <Icon size={14} weight="duotone" />
      </div>
      <div>
        <div className="font-['Outfit'] text-sm font-bold">{typeof value === 'number' ? value.toLocaleString('fr-FR') : value}</div>
        <div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>{label}</div>
      </div>
    </div>
  );
}

function SparkBars({ data }) {
  const values = data.map(d => Object.entries(d).filter(([k]) => k !== 'bucket').reduce((a, [, v]) => a + v, 0));
  const max = Math.max(1, ...values);
  return (
    <div className="flex items-end gap-0.5 h-24">
      {data.map((d, i) => {
        const tot = values[i];
        return (
          <div key={d.bucket} className="flex-1 flex flex-col items-center justify-end">
            <div className="w-full rounded-t transition-all"
              style={{ height: `${(tot / max) * 100}%`, background: 'linear-gradient(180deg, #0F056B, #5B21B6)', minHeight: tot ? '4px' : '2px' }}
              title={`${d.bucket}: ${tot}`} />
          </div>
        );
      })}
    </div>
  );
}
