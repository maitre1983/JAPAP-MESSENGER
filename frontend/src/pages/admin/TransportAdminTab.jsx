/**
 * TransportAdminTab — Phase B/C parent wrapper.
 *
 * The admin "Transport JAPAP" tab branches into 3 sub-tabs:
 *   • Chauffeurs   (existing — KYC review + driver list)
 *   • Tarification (new — AI pricing grid)
 *   • Vue d'ensemble (Phase C — KPI dashboard, wired in iter103)
 */
import { useState } from 'react';
import { Car, CurrencyCircleDollar, ChartBar, Gauge } from '@phosphor-icons/react';
import TransportDriversAdminTab from './TransportDriversAdminTab';
import TransportPricingAdminTab from './TransportPricingAdminTab';
import TransportOverviewAdminTab from './TransportOverviewAdminTab';
import TransportSurgeAdminTab from './TransportSurgeAdminTab';

const SUB_TABS = [
  { id: 'drivers',   label: 'Chauffeurs',     icon: Car },
  { id: 'pricing',   label: 'Tarification',   icon: CurrencyCircleDollar },
  { id: 'surge',     label: 'Surge',          icon: Gauge },
  { id: 'overview',  label: "Vue d'ensemble", icon: ChartBar },
];

export default function TransportAdminTab({ onAction }) {
  const [sub, setSub] = useState('drivers');
  return (
    <div data-testid="transport-admin-root">
      <div className="flex gap-1 mb-4 p-1 rounded-xl"
           style={{ background: 'var(--jp-surface-secondary)' }}>
        {SUB_TABS.map((t) => {
          const active = sub === t.id;
          return (
            <button
              key={t.id}
              onClick={() => setSub(t.id)}
              className={`flex-1 py-2 rounded-lg text-xs font-bold flex items-center justify-center gap-1.5 transition ${active ? 'jp-btn-primary' : ''}`}
              style={!active ? { color: 'var(--jp-text-muted)' } : undefined}
              data-testid={`transport-subtab-${t.id}`}
            >
              <t.icon size={14} weight={active ? 'fill' : 'regular'} /> {t.label}
            </button>
          );
        })}
      </div>
      {sub === 'drivers'  && <TransportDriversAdminTab onAction={onAction} />}
      {sub === 'pricing'  && <TransportPricingAdminTab />}
      {sub === 'surge'    && <TransportSurgeAdminTab />}
      {sub === 'overview' && <TransportOverviewAdminTab />}
    </div>
  );
}
