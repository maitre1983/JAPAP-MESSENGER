import { NavLink, Link, useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import { useRealtime } from '@/context/RealtimeContext';
import { useTasks } from '@/context/TasksContext';
import { useTranslation } from 'react-i18next';
import LanguageSwitcher from '@/components/LanguageSwitcher';
import { House, ChatCircle, Wallet, SquaresFour, UserCircle, SignOut, ShieldCheck, Bell, GearSix, List, X, MagnifyingGlass, UsersThree, FileText } from '@phosphor-icons/react';
import { useState } from 'react';

/*
  JAPAP MESSENGER — 5-BLOCK NAVIGATION
  ============================================
  1. HOME / FEED     → /feed      (Actualites, posts, stories)
  2. MESSENGER       → /chat      (Chat, appels, statuts)
  3. SERVICES        → /services  (Marketplace, Food, Taxi, Jobs, JAPAP Staking, etc.)
  4. WALLET          → /wallet    (Solde, depot, retrait, transactions)
  5. PROFILE         → /profile   (Info utilisateur, parametres, KYC)

  JAPAP Staking vit UNIQUEMENT dans /services (pas de menu "Crypto" séparé).
  Admin panel accessible depuis Profile (si admin). Aucune duplication.
*/

const NAV_ITEMS = [
  { to: '/feed', icon: House, key: 'home' },
  { to: '/chat', icon: ChatCircle, key: 'messenger' },
  { to: '/services', icon: SquaresFour, key: 'services' },
  { to: '/wallet', icon: Wallet, key: 'wallet' },
  { to: '/profile', icon: UserCircle, key: 'profile' },
];

export default function Layout({ children }) {
  const { user, logout } = useAuth();
  const { unreadCount: unreadNotifs } = useRealtime();
  const { pending: pendingTasks } = useTasks();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="flex flex-col md:flex-row h-screen" style={{ background: 'var(--jp-bg)' }} data-testid="app-layout">
      
      {/* ══ DESKTOP SIDEBAR ══ */}
      <aside className="hidden md:flex flex-col w-64 border-r flex-shrink-0" style={{ background: '#0B0542', borderColor: '#1A0F5A' }} data-testid="sidebar">
        {/* Logo */}
        <div className="flex items-center justify-center px-5 py-5 border-b" style={{ borderColor: '#1A0F5A' }}>
          <div className="inline-flex items-center rounded-xl bg-white px-3 py-2 shadow-sm">
            <img src="/japap-logo.jpg" alt="JAPAP" className="h-10 object-contain" data-testid="sidebar-logo" />
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 p-3 space-y-1">
          {NAV_ITEMS.map(item => (
            <NavLink key={item.to} to={item.to} data-testid={`nav-${item.key}`}
              className="flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-['Manrope'] font-medium transition-all"
              style={({ isActive }) => ({
                background: isActive ? '#E01C2E' : 'transparent',
                color: isActive ? 'white' : 'rgba(255,255,255,0.6)',
              })}>
              <item.icon size={20} weight="duotone" />
              {t(`nav.${item.key}`)}
            </NavLink>
          ))}
          {user?.role === 'admin' && (
            <NavLink to="/admin" data-testid="nav-admin"
              className="flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-['Manrope'] font-medium transition-all"
              style={({ isActive }) => ({
                background: isActive ? 'rgba(255,255,255,0.1)' : 'transparent',
                color: isActive ? 'white' : 'rgba(255,255,255,0.4)',
              })}>
              <ShieldCheck size={20} weight="duotone" />
              {t('nav.admin')}
            </NavLink>
          )}
        </nav>

        {/* User & Logout */}
        <div className="border-t p-4" style={{ borderColor: '#1A0F5A' }}>
          <div className="flex items-center gap-3 px-2 py-2 mb-2">
            <div className="jp-avatar jp-avatar-md" style={{ background: '#E01C2E', color: 'white' }}>
              {user?.avatar ? <img src={user.avatar} alt="" /> : (user?.first_name?.[0] || '?').toUpperCase()}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium font-['Manrope'] truncate text-white">{user?.first_name} {user?.last_name}</p>
              <p className="text-[10px] truncate text-white/40">{user?.email}</p>
            </div>
            {unreadNotifs > 0 && <span className="jp-badge" style={{ background: '#E01C2E', color: 'white', fontSize: '10px' }}>{unreadNotifs}</span>}
          </div>
          <button onClick={handleLogout} data-testid="logout-button"
            className="flex items-center gap-3 px-4 py-2.5 rounded-xl text-sm font-['Manrope'] font-medium w-full transition-all"
            style={{ color: 'rgba(255,255,255,0.5)' }}>
            <SignOut size={18} /> {t('nav.logout')}
          </button>
          <div className="mt-3"><LanguageSwitcher compact className="w-full" /></div>
        </div>
      </aside>

      {/* ══ MOBILE TOP BAR — Blue header matching screenshots ══ */}
      <div className="md:hidden fixed top-0 left-0 right-0 z-50 px-4 py-2.5 flex items-center justify-between jp-safe-top"
        style={{ background: '#0F056B', paddingTop: 'max(env(safe-area-inset-top, 0px), 10px)' }}>
        <div className="inline-flex items-center rounded-lg bg-white px-2 py-1 shadow-sm">
          <img src="/japap-logo.jpg" alt="JAPAP" className="h-7 object-contain" data-testid="mobile-logo" />
        </div>
        <div className="flex items-center gap-3">
          <MagnifyingGlass size={20} className="text-white/80" />
          {/* iter163 — Bell is now a proper Link to /notifications (was a
              static icon, making users think notifications were "dead"). */}
          <Link
            to="/notifications"
            data-testid="mobile-bell"
            aria-label="Notifications"
            className="relative inline-flex items-center justify-center p-1 rounded-full hover:bg-white/10 active:scale-95 transition-transform"
          >
            <Bell size={20} className="text-white/80" />
            {unreadNotifs > 0 && (
              <span
                className="absolute -top-0.5 -right-0.5 w-4 h-4 text-[9px] font-bold rounded-full flex items-center justify-center"
                style={{ background: '#E01C2E', color: 'white' }}
                data-testid="mobile-bell-badge"
              >
                {unreadNotifs > 9 ? '9+' : unreadNotifs}
              </span>
            )}
          </Link>
          <button onClick={() => setMobileMenuOpen(!mobileMenuOpen)} data-testid="mobile-menu-toggle" className="text-white/80">
            {mobileMenuOpen ? <X size={20} /> : <List size={20} />}
          </button>
        </div>
      </div>

      {/* ══ MOBILE SLIDE MENU ══ */}
      {mobileMenuOpen && (
        <div className="md:hidden fixed inset-0 z-40" onClick={() => setMobileMenuOpen(false)}>
          <div className="absolute inset-0" style={{ background: 'rgba(0,0,0,0.4)' }} />
          <div className="absolute right-0 top-0 bottom-0 w-72 pt-14 jp-animate-slideInRight"
            style={{ background: 'var(--jp-surface)' }} onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-3 px-5 py-4 border-b" style={{ borderColor: 'var(--jp-border)' }}>
              <div className="jp-avatar jp-avatar-md jp-avatar-primary">
                {(user?.first_name?.[0] || '?').toUpperCase()}
              </div>
              <div>
                <p className="text-sm font-semibold font-['Manrope']" style={{ color: 'var(--jp-text)' }}>{user?.first_name} {user?.last_name}</p>
                <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>{user?.email}</p>
              </div>
            </div>
            <nav className="p-3 space-y-1">
              {user?.role === 'admin' && (
                <NavLink to="/admin" onClick={() => setMobileMenuOpen(false)}
                  className="flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-['Manrope'] font-medium"
                  style={({ isActive }) => ({ background: isActive ? 'var(--jp-secondary)' : 'transparent', color: isActive ? 'white' : 'var(--jp-text-secondary)' })}>
                  <ShieldCheck size={20} weight="duotone" /> {t('nav.administration')}
                </NavLink>
              )}
              <NavLink to="/groups" onClick={() => setMobileMenuOpen(false)}
                className="flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-['Manrope'] font-medium"
                style={{ color: 'var(--jp-text-secondary)' }}
                data-testid="drawer-link-groups">
                <UsersThree size={20} weight="duotone" /> {t('nav.groups')}
              </NavLink>
              <NavLink to="/pages" onClick={() => setMobileMenuOpen(false)}
                className="flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-['Manrope'] font-medium"
                style={{ color: 'var(--jp-text-secondary)' }}
                data-testid="drawer-link-pages">
                <FileText size={20} weight="duotone" /> {t('nav.pages')}
              </NavLink>
              <NavLink to="/profile" onClick={() => setMobileMenuOpen(false)}
                className="flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-['Manrope'] font-medium"
                style={{ color: 'var(--jp-text-secondary)' }}>
                <GearSix size={20} weight="duotone" /> {t('nav.settings')}
              </NavLink>
              <button onClick={handleLogout}
                className="flex items-center gap-3 px-4 py-3 rounded-xl text-sm font-['Manrope'] font-medium w-full"
                style={{ color: 'var(--jp-error)' }}>
                <SignOut size={18} /> {t('nav.logout')}
              </button>
            </nav>
          </div>
        </div>
      )}

      {/* ══ MAIN CONTENT ══ */}
      <main
        className="flex-1 overflow-auto overflow-x-hidden mt-14 mb-20 md:mt-0 md:mb-0 min-h-0"
        style={{
          // Respect iPhone notch + home indicator so no content is clipped
          paddingLeft: 'env(safe-area-inset-left, 0px)',
          paddingRight: 'env(safe-area-inset-right, 0px)',
        }}
        data-testid="main-content"
      >
        {children}
      </main>

      {/* ══ MOBILE BOTTOM NAV (5 BLOCKS) ══ */}
      <nav className="md:hidden fixed bottom-0 left-0 right-0 z-50 flex border-t"
        style={{
          background: '#0F056B',
          paddingBottom: 'env(safe-area-inset-bottom, 0px)',
        }}
        data-testid="bottom-nav">
        {NAV_ITEMS.map(item => {
          const isActive = location.pathname.startsWith(item.to);
          const showBadge = item.to === '/chat' && unreadNotifs > 0;
          const showTaskBadge = item.to === '/profile' && pendingTasks > 0;
          return (
            <NavLink key={item.to} to={item.to}
              data-testid={`bottom-nav-${item.key}`}
              className="flex-1 flex flex-col items-center gap-0.5 py-2.5 transition-all relative"
              style={{
                color: isActive ? '#FFFFFF' : 'rgba(255,255,255,0.5)',
              }}>
              <div className="relative">
                <item.icon size={22} weight={isActive ? 'fill' : 'regular'} />
                {showBadge && (
                  <span data-testid="nav-unread-badge"
                    className="absolute -top-1.5 -right-2 min-w-[16px] h-[16px] px-1 text-[9px] font-bold rounded-full flex items-center justify-center"
                    style={{ background: '#E01C2E', color: 'white' }}>
                    {unreadNotifs > 99 ? '99+' : unreadNotifs}
                  </span>
                )}
                {showTaskBadge && (
                  <span data-testid="nav-tasks-badge"
                    className="absolute -top-1.5 -right-2 min-w-[16px] h-[16px] px-1 text-[9px] font-bold rounded-full flex items-center justify-center"
                    style={{ background: '#E01C2E', color: 'white' }}>
                    {pendingTasks > 99 ? '99+' : pendingTasks}
                  </span>
                )}
              </div>
              <span className="text-[9px] font-['Manrope'] font-medium">{t(`nav.${item.key}`)}</span>
            </NavLink>
          );
        })}
      </nav>
    </div>
  );
}
