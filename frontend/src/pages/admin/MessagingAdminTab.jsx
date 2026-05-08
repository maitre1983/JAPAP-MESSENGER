/**
 * MessagingAdminTab — Iter 65 Admin Messaging Center (UI + AI).
 *
 * 5 sub-tabs exactly as briefed :
 *   Campaigns · Audience · Templates · Automations · Analytics
 *
 * Uses backend built in iter64. Claude Sonnet 4.5 wired for template gen.
 *
 * Kept deliberately simple & action-oriented — no clutter, admin-grade UX.
 */
import { useState } from 'react';
import {
  Megaphone, UsersThree, FileText, Lightning, ChartLineUp, ShieldCheck,
} from '@phosphor-icons/react';
import CampaignsPane from '@/components/admin/messaging/CampaignsPane';
import AudiencePane from '@/components/admin/messaging/AudiencePane';
import TemplatesPane from '@/components/admin/messaging/TemplatesPane';
import AnalyticsPane from '@/components/admin/messaging/AnalyticsPane';
import BatchSafetyPane from '@/components/admin/messaging/BatchSafetyPane';

const SUBTABS = [
  { id: 'campaigns', label: 'Campagnes', icon: Megaphone },
  { id: 'audience', label: 'Audience', icon: UsersThree },
  { id: 'templates', label: 'Templates', icon: FileText },
  { id: 'batch', label: 'Batch & Safety', icon: ShieldCheck },
  { id: 'automations', label: 'Automations', icon: Lightning },
  { id: 'analytics', label: 'Analytics', icon: ChartLineUp },
];

export default function MessagingAdminTab() {
  const [sub, setSub] = useState('campaigns');

  return (
    <div data-testid="messaging-admin-tab">
      <div className="mb-4 flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="font-['Outfit'] text-xl font-bold" style={{ color: 'var(--jp-text)' }}>
            📩 Messaging Center
          </h2>
          <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
            Campagnes · segments · templates · automations · analytics
          </p>
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5 mb-5 p-1 rounded-xl"
           style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
        {SUBTABS.map((s) => {
          const active = sub === s.id;
          return (
            <button key={s.id} onClick={() => setSub(s.id)}
                    data-testid={`messaging-subtab-${s.id}`}
                    className="px-3 py-2 rounded-lg text-xs font-semibold transition-colors flex items-center gap-1.5"
                    style={{
                      background: active ? 'var(--jp-primary)' : 'transparent',
                      color: active ? '#fff' : 'var(--jp-text-muted)',
                    }}>
              <s.icon size={14} weight={active ? 'bold' : 'regular'} />
              {s.label}
            </button>
          );
        })}
      </div>

      {sub === 'campaigns' && <CampaignsPane />}
      {sub === 'audience' && <AudiencePane />}
      {sub === 'templates' && <TemplatesPane />}
      {sub === 'batch' && <BatchSafetyPane />}
      {sub === 'automations' && <AutomationsPlaceholder />}
      {sub === 'analytics' && <AnalyticsPane />}
    </div>
  );
}

function AutomationsPlaceholder() {
  return (
    <div className="p-8 rounded-2xl text-center"
         style={{ background: 'var(--jp-surface-secondary)', border: '1px dashed var(--jp-border)' }}
         data-testid="messaging-automations-placeholder">
      <Lightning size={40} weight="duotone" style={{ color: 'var(--jp-primary)', margin: '0 auto 12px' }} />
      <h3 className="font-['Outfit'] font-bold text-base mb-1">Automations — Phase 2</h3>
      <p className="text-xs max-w-md mx-auto" style={{ color: 'var(--jp-text-muted)' }}>
        Les flows automatiques (onboarding, réactivation 7j/30j, upgrade Pro, parrainage)
        seront disponibles à l'itération suivante. L'infrastructure (triggers, delays,
        segments) est déjà en place côté DB.
      </p>
    </div>
  );
}
