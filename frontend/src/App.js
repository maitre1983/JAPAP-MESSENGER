import ErrorBoundary from '@/components/ErrorBoundary';
import PushOptInPrompt from '@/components/PushOptInPrompt';
import PwaUpdateBanner from '@/components/PwaUpdateBanner';
import { installAxiosErrorReporter } from '@/utils/axiosErrorReporter';
import { useEffect, lazy, Suspense } from 'react';

// iter137 — Install once at module load so every axios call (anywhere)
// auto-reports 4xx/5xx errors to the AI Error Monitor pipeline.
installAxiosErrorReporter();
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Toaster } from 'sonner';
import { HelmetProvider } from 'react-helmet-async';
import { AuthProvider, useAuth } from '@/context/AuthContext';
import { CallProvider } from '@/context/CallContext';
import { RealtimeProvider } from '@/context/RealtimeContext';
import { SmartUpsellProvider } from '@/context/SmartUpsellContext';
import { TasksProvider } from '@/context/TasksContext';

// iter198 — Lazy-loaded pages (chargées uniquement à la navigation)
// Pages critiques (chargées en priorité — utilisées dès la connexion)
const LoginPage = lazy(() => import('@/pages/LoginPage'));
const RegisterPage = lazy(() => import('@/pages/RegisterPage'));
const FeedPage = lazy(() => import('@/pages/FeedPage'));
const ChatPage = lazy(() => import('@/pages/ChatPage'));
const ProfilePage = lazy(() => import('@/pages/ProfilePage'));
const WalletPage = lazy(() => import('@/pages/WalletPage'));
const ServicesPage = lazy(() => import('@/pages/ServicesPage'));

// Pages secondaires
const AdminPage = lazy(() => import('@/pages/AdminPage'));
const LandingPage = lazy(() => import('@/pages/LandingPage'));
const ForgotPasswordPage = lazy(() => import('@/pages/ForgotPasswordPage'));
const ResetPasswordPage = lazy(() => import('@/pages/ResetPasswordPage'));
const AuthCallback = lazy(() => import('@/pages/AuthCallback'));
const ReferralRedirectPage = lazy(() => import('@/pages/ReferralRedirectPage'));
const ReferralPreviewPage = lazy(() => import('@/pages/ReferralPreviewPage'));
const SuperAdminLoginPage = lazy(() => import('@/pages/SuperAdminLoginPage'));
const SuperAdminPage = lazy(() => import('@/pages/SuperAdminPage'));
const ProPage = lazy(() => import('@/pages/ProPage'));
const ReferralPage = lazy(() => import('@/pages/ReferralPage'));
const ConnectPage = lazy(() => import('@/pages/ConnectPage'));
const ConnectRedeemPage = lazy(() => import('@/pages/ConnectRedeemPage'));
const MyTasksPage = lazy(() => import('@/pages/MyTasksPage'));
const CallHistoryPage = lazy(() => import('@/pages/CallHistoryPage'));
const ReelsPage = lazy(() => import('@/pages/ReelsPage'));
const NotificationsPage = lazy(() => import('@/pages/NotificationsPage'));
const GroupsPage = lazy(() => import('@/pages/GroupsPage'));
const PagesPage = lazy(() => import('@/pages/PagesPage'));
const PostDetailPage = lazy(() => import('@/pages/PostDetailPage'));
const UserFollowListPage = lazy(() => import('@/pages/UserFollowListPage'));
const SettingsPage = lazy(() => import('@/pages/SettingsPage'));
const FollowRequestsPage = lazy(() => import('@/pages/FollowRequestsPage'));
const DepositReturnPage = lazy(() => import('@/pages/wallet/DepositReturnPage'));
// iter237af — Hubtel Mobile Money (Ghana 🇬🇭) standalone page.
const WalletHubtelMomoPage = lazy(() => import('@/pages/WalletHubtelMomoPage'));
// iter238 — Paystack Ghana pages (strictly additive).
const WalletPaystackPage = lazy(() => import('@/pages/WalletPaystackPage'));
const WalletPaystackResultPage = lazy(() => import('@/pages/WalletPaystackResultPage'));
// iter238c — Admin wallet diagnostics (back-office only, additive).
const AdminWalletDiagnosticsPage = lazy(() => import('@/pages/AdminWalletDiagnosticsPage'));

// iter237n — Legal pages (CGU / CGJ / RGPD) + About.
const CGUPage = lazy(() => import('@/pages/legal/CGUPage'));
const CGJPage = lazy(() => import('@/pages/legal/CGJPage'));
const PrivacyPage = lazy(() => import('@/pages/legal/PrivacyPage'));
const AboutPage = lazy(() => import('@/pages/AboutPage'));

// Games (groupe lourd — chargé uniquement quand l'utilisateur navigue vers /games)
const GamesModule = lazy(() => import('@/pages/GamesModule'));
const WheelFortunePage = lazy(() => import('@/pages/WheelFortunePage'));
const QuizJAPAPPage = lazy(() => import('@/pages/QuizJAPAPPage'));
const QuizChampionPage = lazy(() => import('@/pages/QuizChampionPage'));
const QuizChallengesPage = lazy(() => import('@/pages/QuizChallengesPage'));
const QuizChallengePage = lazy(() => import('@/pages/QuizChallengePage'));
const OpenChallengePage = lazy(() => import('@/pages/OpenChallengePage'));        // iter228
const PublicChallengePage = lazy(() => import('@/pages/PublicChallengePage'));    // iter228
const TapChallengePage = lazy(() => import('@/pages/TapChallengePage'));
const DuelPage = lazy(() => import('@/pages/DuelPage'));
const MyDuelsSentPage = lazy(() => import('@/pages/MyDuelsSentPage'));

// Pages publiques spéciales
const PublicRideTrackPage = lazy(() => import('@/pages/PublicRideTrackPage'));
const SupportPage = lazy(() => import('@/pages/SupportPage'));
const PayPage = lazy(() => import('@/pages/PayPage'));
const ConnectHotspotLanding = lazy(() => import('@/pages/ConnectHotspotLanding'));
const CrowdfundingProjectPage = lazy(() => import('@/pages/CrowdfundingProjectPage'));
const CrowdfundingLeaderboardPage = lazy(() => import('@/pages/CrowdfundingLeaderboardPage'));
const MarketplaceProductPage = lazy(() => import('@/pages/MarketplaceProductPage'));
const MarketplaceAdsPage = lazy(() => import('@/pages/MarketplaceAdsPage'));

// Layout (non-lazy car utilisé immédiatement dans ProtectedRoute)
import Layout from '@/components/layout/Layout';
import InstallPWA from '@/components/InstallPWA';
import { CurrencyProvider } from '@/utils/currency';
import useGeoLanguageBootstrap from '@/hooks/useGeoLanguageBootstrap';
import '@/App.css';

/*
  JAPAP MESSENGER — ROUTING (NO DUPLICATES)
  ============================================
  /feed       → HOME / FEED (actualites, posts, stories)
  /chat       → MESSENGER (chat 1-1, groupes, appels)
  /wallet     → WALLET (solde, depot, retrait, transactions)
  /services   → SERVICES HUB (marketplace, food, taxi, etc.)
  /profile    → PROFILE / SETTINGS (info, parametres, KYC)
  /admin      → ADMIN (admin-only, accessible depuis profil)
  
  Chaque fonctionnalite = un seul endroit.
*/

function ProtectedRoute({ children, adminOnly = false }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--jp-bg)' }}>
      <div className="w-8 h-8 border-2 border-t-transparent rounded-full animate-spin" style={{ borderColor: 'var(--jp-primary)', borderTopColor: 'transparent' }} />
    </div>
  );
  // Preserve the intended deep link (e.g. /post/xxx or /users/:id/followers)
  // across the login detour so external sharers land on the right page.
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  // Iter83 — superadmin implicitly has all admin capabilities.
  if (adminOnly && !['admin', 'superadmin'].includes(user.role)) return <Navigate to="/feed" replace />;
  return <Layout>{children}</Layout>;
}

/* Iter83 — Superadmin guard. Does NOT wrap with the normal Layout (the
   superadmin dashboard has its own chrome + dark theme). Invalid/unknown
   roles are redirected to the dynamic login page for the same token. */
function SuperadminDashboardRoute({ children }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  // pathname like /admin230426/dashboard  → token = '230426'
  const token = (location.pathname.split('/')[1] || '').replace(/^admin/, '');
  if (loading) return null;
  if (!user || user.role !== 'superadmin') {
    return <Navigate to={`/admin${token}`} replace />;
  }
  return children;
}

function PublicRoute({ children }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--jp-bg)' }}>
      <div className="w-8 h-8 border-2 border-t-transparent rounded-full animate-spin" style={{ borderColor: 'var(--jp-primary)', borderTopColor: 'transparent' }} />
    </div>
  );
  // If the user is already logged in and the router-state remembers an
  // intended URL (set by ProtectedRoute), honour it over the default /feed.
  // iter141nine — also honour `?redirect=` query param so shared payment
  // request links (PayPage) survive a login round-trip.
  if (user) {
    const from = location.state?.from;
    const queryRedirect = new URLSearchParams(location.search).get('redirect');
    return <Navigate to={from || queryRedirect || '/feed'} replace />;
  }
  return children;
}

/* Iter83 — Wildcard handler that routes /admin{DDMMYY} and its
   /dashboard sub-path to the superadmin portal, while anything else falls
   back to /feed. This lets the path be literally /admin230426 (no slash)
   even though React Router doesn't parse inline segment params. */
function DynamicAdminOrFallback() {
  const location = useLocation();
  const path = (location.pathname || '').replace(/\/+$/, '');
  const m = path.match(/^\/admin(\d{6})(\/dashboard)?$/);
  if (!m) return <Navigate to="/feed" replace />;
  const isDashboard = Boolean(m[2]);
  if (isDashboard) {
    return (
      <SuperadminDashboardRoute>
        <SuperAdminPage />
      </SuperadminDashboardRoute>
    );
  }
  return <SuperAdminLoginPage />;
}

// iter198 — Spinner léger pour les transitions lazy de pages (adapté 2G/3G)
function PageLoader() {
  return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: 'var(--jp-bg)' }}>
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 border-2 border-t-transparent rounded-full animate-spin"
          style={{ borderColor: 'var(--jp-primary)', borderTopColor: 'transparent' }} />
        <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>Chargement...</p>
      </div>
    </div>
  );
}

function AppRouter() {
  // iter203 — Détection automatique de langue par IP pour visiteurs anonymes.
  // Ne fait rien si l'utilisateur a déjà choisi une langue (localStorage).
  useGeoLanguageBootstrap();
  const location = useLocation();
  if (location.hash?.includes('session_id=')) return <Suspense fallback={<PageLoader />}><AuthCallback /></Suspense>;

  return (
    <Suspense fallback={<PageLoader />}>
    <Routes>
      {/* Landing page publique */}
      <Route path="/" element={<PublicRoute><LandingPage /></PublicRoute>} />
      <Route path="/login" element={<PublicRoute><LoginPage /></PublicRoute>} />
      <Route path="/register" element={<PublicRoute><RegisterPage /></PublicRoute>} />
      {/* iter109 — Short-link parrainage : /r/{code} → /register?ref={code}. */}
      <Route path="/r/:code" element={<ReferralRedirectPage />} />
      {/* iter110 — Public conversion landing : /p/{code}. */}
      <Route path="/p/:code" element={<ReferralPreviewPage />} />
      <Route path="/forgot-password" element={<PublicRoute><ForgotPasswordPage /></PublicRoute>} />
      <Route path="/reset-password" element={<PublicRoute><ResetPasswordPage /></PublicRoute>} />
      
      {/* 5 BLOCS PRINCIPAUX — Sans duplication */}
      <Route path="/feed" element={<ProtectedRoute><FeedPage /></ProtectedRoute>} />
      <Route path="/post/:postId" element={<ProtectedRoute><PostDetailPage /></ProtectedRoute>} />
      <Route path="/users/:userId/followers" element={<ProtectedRoute><UserFollowListPage /></ProtectedRoute>} />
      <Route path="/users/:userId/following" element={<ProtectedRoute><UserFollowListPage /></ProtectedRoute>} />
      <Route path="/settings" element={<ProtectedRoute><SettingsPage /></ProtectedRoute>} />
      <Route path="/settings/requests" element={<ProtectedRoute><FollowRequestsPage /></ProtectedRoute>} />
      <Route path="/games" element={<ProtectedRoute><GamesModule /></ProtectedRoute>} />
      <Route path="/games/wheel" element={<ProtectedRoute><WheelFortunePage /></ProtectedRoute>} />
      <Route path="/games/quiz" element={<ProtectedRoute><QuizJAPAPPage /></ProtectedRoute>} />
      <Route path="/games/quiz/daily" element={<ProtectedRoute><QuizJAPAPPage /></ProtectedRoute>} />
      <Route path="/games/quiz/champion" element={<ProtectedRoute><QuizChampionPage /></ProtectedRoute>} />
      <Route path="/games/quiz/champion/:country" element={<ProtectedRoute><QuizChampionPage /></ProtectedRoute>} />
      {/* iter228 — Open challenge flow (A creates → A plays → A shares link → B claims). */}
      <Route path="/games/quiz/challenge/new" element={<ProtectedRoute><OpenChallengePage /></ProtectedRoute>} />
      <Route path="/c/:cid" element={<PublicChallengePage />} />
      <Route path="/games/quiz/challenges" element={<ProtectedRoute><QuizChallengesPage /></ProtectedRoute>} />
      <Route path="/games/quiz/challenges/:id" element={<ProtectedRoute><QuizChallengePage /></ProtectedRoute>} />
      <Route path="/games/tap" element={<ProtectedRoute><TapChallengePage /></ProtectedRoute>} />
      <Route path="/duel/:token" element={<DuelPage />} />
      <Route path="/duel/me/sent" element={<ProtectedRoute><MyDuelsSentPage /></ProtectedRoute>} />
      <Route path="/track/:token" element={<PublicRideTrackPage />} />
      <Route path="/pay/:requestId" element={<PayPage />} />
      <Route path="/connect/h/:hotspotId" element={<ConnectHotspotLanding />} />
      <Route path="/crowdfunding/p/:slug" element={<CrowdfundingProjectPage />} />
      <Route path="/crowdfunding/leaderboard" element={<CrowdfundingLeaderboardPage />} />
      <Route path="/crowdfunding" element={<Navigate to="/services?view=crowdfunding" replace />} />
      <Route path="/marketplace/p/:product_id" element={<ProtectedRoute><MarketplaceProductPage /></ProtectedRoute>} />
      <Route path="/marketplace/ads" element={<ProtectedRoute><MarketplaceAdsPage /></ProtectedRoute>} />
      <Route path="/support" element={<ProtectedRoute><SupportPage /></ProtectedRoute>} />
      <Route path="/chat" element={<ProtectedRoute><ChatPage /></ProtectedRoute>} />
      <Route path="/wallet" element={<ProtectedRoute><WalletPage /></ProtectedRoute>} />
      {/* iter195 — CEO : page dédiée de retour Hubtel (clone EAA) */}
      <Route path="/wallet/deposit/return" element={<ProtectedRoute><DepositReturnPage /></ProtectedRoute>} />
      {/* iter237af — Hubtel Mobile Money (Ghana 🇬🇭) — strictly additive */}
      <Route path="/wallet/hubtel-momo" element={<ProtectedRoute><WalletHubtelMomoPage /></ProtectedRoute>} />
      {/* iter238 — Paystack Ghana — strictly additive */}
      <Route path="/wallet/paystack" element={<ProtectedRoute><WalletPaystackPage /></ProtectedRoute>} />
      <Route path="/wallet/paystack/result" element={<ProtectedRoute><WalletPaystackResultPage /></ProtectedRoute>} />
      <Route path="/services" element={<ProtectedRoute><ServicesPage /></ProtectedRoute>} />
      <Route path="/groups" element={<ProtectedRoute><GroupsPage /></ProtectedRoute>} />
      <Route path="/pages" element={<ProtectedRoute><PagesPage /></ProtectedRoute>} />
      <Route path="/profile" element={<ProtectedRoute><ProfilePage /></ProtectedRoute>} />
      
      {/* Pro, Notifications, Admin */}
      <Route path="/pro" element={<ProtectedRoute><ProPage /></ProtectedRoute>} />
      <Route path="/referral" element={<ProtectedRoute><ReferralPage /></ProtectedRoute>} />
      <Route path="/connect" element={<ProtectedRoute><ConnectPage /></ProtectedRoute>} />
      <Route path="/connect/redeem" element={<ProtectedRoute><ConnectRedeemPage /></ProtectedRoute>} />
      <Route path="/tasks" element={<ProtectedRoute><MyTasksPage /></ProtectedRoute>} />
      <Route path="/calls" element={<ProtectedRoute><CallHistoryPage /></ProtectedRoute>} />
      <Route path="/reels" element={<ProtectedRoute><ReelsPage /></ProtectedRoute>} />
      {/* iter217 — viral deep-link: /reels/{reel_id} opens the page
          scrolled to the targeted reel. Same component, parsed via
          useParams() inside ReelsPage. */}
      <Route path="/reels/:reelId" element={<ProtectedRoute><ReelsPage /></ProtectedRoute>} />
      <Route path="/notifications" element={<ProtectedRoute><NotificationsPage /></ProtectedRoute>} />
      <Route path="/admin" element={<ProtectedRoute adminOnly><AdminPage /></ProtectedRoute>} />
      {/* iter238c — Admin wallet diagnostics (back-office only, additive). */}
      <Route path="/admin/wallet/diagnostics" element={<ProtectedRoute adminOnly><AdminWalletDiagnosticsPage /></ProtectedRoute>} />
      {/* iter237n — Pages légales publiques (pas d'auth requise) */}
      <Route path="/legal/cgu" element={<CGUPage />} />
      <Route path="/legal/conditions-de-jeu" element={<CGJPage />} />
      <Route path="/legal/confidentialite" element={<PrivacyPage />} />
      <Route path="/about" element={<AboutPage />} />
      <Route path="/contact" element={<AboutPage />} />
      {/* Iter83 — Dynamic superadmin portal at /admin{DDMMYY}.
          The path is handled by the catch-all `DynamicAdminOrFallback`
          below so that /admin230426 maps to SuperAdminLoginPage while any
          other unknown URL still falls through to /feed. */}

      {/* Redirect par défaut (capture également /admin{DDMMYY}) */}
      <Route path="*" element={<DynamicAdminOrFallback />} />
    </Routes>
    </Suspense>
  );
}

function App() {
  return (
    <ErrorBoundary>
      <HelmetProvider>
        <div className="App">
          <BrowserRouter>
          <AuthProvider>
            <CurrencyProvider>
              <RealtimeProvider>
                <SmartUpsellProvider>
                  <CallProvider>
                    <TasksProvider>
                      <AppRouter />
                      <InstallPWA />
                      <PushOptInPrompt />
                      <PwaUpdateBanner />
                      <Toaster
                        position="top-center"
                        richColors
                        closeButton
                        toastOptions={{
                          style: {
                            fontFamily: 'Manrope, sans-serif',
                            border: '1px solid rgba(15,5,107,0.12)',
                          },
                        }}
                      />
                    </TasksProvider>
                  </CallProvider>
                </SmartUpsellProvider>
              </RealtimeProvider>
            </CurrencyProvider>
          </AuthProvider>
          </BrowserRouter>
        </div>
      </HelmetProvider>
    </ErrorBoundary>
  );
}

export default App;
