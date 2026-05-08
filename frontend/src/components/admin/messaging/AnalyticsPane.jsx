/**
 * AnalyticsPane — dashboard cards + per-campaign drilldown.
 */
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { PaperPlaneTilt, Eye, CursorClick, XCircle, WarningCircle, Envelope } from '@phosphor-icons/react';
import { msgApi } from './messagingApi';
import { useTranslation } from 'react-i18next';

export default function AnalyticsPane() {
  const { t } = useTranslation();
  const [cards, setCards] = useState(null);
  const [campaigns, setCampaigns] = useState([]);

  useEffect(() => {
    msgApi.analytics().then(setCards).catch(() => toast.error('Analytics indisponibles.'));
    msgApi.campaigns().then((cs) => setCampaigns(cs.filter((c) => c.status === 'sent').slice(0, 10)))
                     .catch(() => {});
  }, []);

  if (!cards) return <div className="text-xs opacity-60">Chargement…</div>;

  const rate = (num, den) => (den > 0 ? `${((num / den) * 100).toFixed(1)}%` : '—');

  const statCards = [
    { label: t('analytics_pane.emails_envoyes'),   value: cards.total_sent,      Icon: PaperPlaneTilt, color: '#F7931A', bg: 'rgba(247,147,26,0.12)' },
    { label: t('analytics_pane.delivres'),         value: cards.total_delivered, Icon: Envelope,       color: '#059669', bg: 'rgba(5,150,105,0.12)' },
    { label: 'Ouvertures',       value: cards.total_opened,    Icon: Eye,            color: '#3B82F6', bg: 'rgba(59,130,246,0.12)' },
    { label: 'Clics',            value: cards.total_clicked,   Icon: CursorClick,    color: '#7c3aed', bg: 'rgba(124,58,237,0.12)' },
    { label: 'Bounces',          value: cards.total_bounced,   Icon: WarningCircle,  color: '#b45309', bg: 'rgba(180,83,9,0.12)' },
    { label: t('analytics_pane.desabonnements'),   value: cards.total_unsub,     Icon: XCircle,        color: '#b91c1c', bg: 'rgba(185,28,28,0.12)' },
  ];

  return (
    <div data-testid="messaging-analytics-pane">
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-5">
        {statCards.map((c) => (
          <div key={c.label} className="p-3 rounded-xl"
               style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}
               data-testid={`analytics-card-${c.label.replace(/[^a-z0-9]/gi, '-').toLowerCase()}`}>
            <div className="w-9 h-9 rounded-full flex items-center justify-center mb-2"
                 style={{ background: c.bg, color: c.color }}>
              <c.Icon size={18} weight="fill" />
            </div>
            <div className="text-[10px] uppercase tracking-wide" style={{ color: 'var(--jp-text-muted)' }}>
              {c.label}
            </div>
            <div className="font-['Outfit'] text-xl font-bold">{c.value.toLocaleString('fr-FR')}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        <MiniMetric label="Taux de livraison" value={rate(cards.total_delivered, cards.total_sent)} />
        <MiniMetric label="Taux d'ouverture" value={rate(cards.total_opened, cards.total_sent)} />
        <MiniMetric label="Taux de clic" value={rate(cards.total_clicked, cards.total_sent)} />
        <MiniMetric label="File d'attente" value={`${cards.queue_pending} · ${cards.queue_failed} échecs`} />
      </div>

      <h3 className="font-['Outfit'] font-bold text-sm mb-2">10 dernières campagnes envoyées</h3>
      {campaigns.length === 0 && (
        <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>Aucune campagne envoyée pour l'instant.</p>
      )}
      <div className="flex flex-col gap-1">
        {campaigns.map((c) => (
          <div key={c.campaign_id}
               className="p-3 rounded-xl flex items-center gap-3 text-xs"
               style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}
               data-testid={`analytics-campaign-row-${c.campaign_id}`}>
            <div className="flex-1 min-w-0">
              <div className="font-semibold truncate">{c.name}</div>
              <div className="opacity-60 text-[10px]">{new Date(c.completed_at || c.created_at).toLocaleString('fr-FR')}</div>
            </div>
            <div className="flex gap-3 shrink-0 text-[11px]">
              <span><strong>{c.sent_count}</strong> sent</span>
              <span style={{ color: '#3B82F6' }}><strong>{c.opened_count}</strong> open</span>
              <span style={{ color: '#7c3aed' }}><strong>{c.clicked_count}</strong> click</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function MiniMetric({ label, value }) {
  return (
    <div className="p-2.5 rounded-lg"
         style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
      <div className="text-[10px] uppercase tracking-wide" style={{ color: 'var(--jp-text-muted)' }}>
        {label}
      </div>
      <div className="font-bold text-sm mt-0.5">{value}</div>
    </div>
  );
}
