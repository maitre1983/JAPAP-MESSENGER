/**
 * iter134 — Global ErrorBoundary.
 *
 * Catches any uncaught rendering error and replaces the white screen with
 * a friendly French error UI plus two recovery actions:
 *   • "Réessayer"          → reset state and re-render the same tree
 *   • "Retour à l'accueil"  → hard navigation to /
 *
 * Errors are logged to the console (and reported to /api/client-errors
 * when available) so we keep observability.
 */
import { Component } from 'react';
import { ArrowClockwise, House, WarningCircle } from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

export default class ErrorBoundary extends Component {
  state = { hasError: false, error: null, errorInfo: null };

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    this.setState({ errorInfo });
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary]', error, errorInfo);
    // Best-effort error report → AI Error Monitor pipeline.
    try {
      const payload = {
        source: 'frontend',
        module: this._guessModule(),
        message: String(error?.message || error).slice(0, 2000),
        stack: String(error?.stack || '').slice(0, 8000),
        severity: 'high',  // any uncaught render error is high by definition
        url: window.location.href.slice(0, 500),
        user_agent: navigator.userAgent.slice(0, 255),
        request_id: '',
      };
      const blob = new Blob([JSON.stringify(payload)], { type: 'application/json' });
      // sendBeacon survives navigation, fits perfectly the boundary use-case.
      if (navigator.sendBeacon) {
        navigator.sendBeacon(`${API}/api/errors/report`, blob);
      } else {
        fetch(`${API}/api/errors/report`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
          credentials: 'include',
          keepalive: true,
        }).catch(() => {});
      }
    } catch { /* swallow */ }
  }

  // Best-effort module detection from the URL pathname so the AI Error
  // Monitor can group "GamesAdminTab crash" vs "QuizJAPAPPage crash" etc.
  _guessModule() {
    const p = (window.location.pathname || '/').split('/').filter(Boolean);
    if (p.length === 0) return 'home';
    return p.slice(0, 3).join('/').slice(0, 80);
  }

  reset = () => this.setState({ hasError: false, error: null, errorInfo: null });
  goHome = () => { window.location.href = '/'; };

  render() {
    if (!this.state.hasError) return this.props.children;
    const detail = this.state.error?.message || 'Erreur inattendue';
    return (
      <div className="min-h-screen flex flex-col items-center justify-center p-6 text-white"
           data-testid="error-boundary"
           style={{ background: 'linear-gradient(160deg, #0F056B 0%, #1c0b8a 60%, #2a0e95 100%)' }}>
        <div className="w-20 h-20 rounded-full flex items-center justify-center mb-5"
             style={{ background: 'linear-gradient(135deg, #E01C2E, #FF6B00)',
                      boxShadow: '0 18px 56px rgba(224,28,46,0.55)' }}>
          <WarningCircle size={40} weight="fill" color="#fff" />
        </div>
        <h1 className="font-['Outfit'] text-3xl font-extrabold mb-2 text-center">
          Oups — quelque chose a planté
        </h1>
        <p className="text-white/70 text-sm text-center max-w-sm mb-6">
          Pas de panique, JAPAP est toujours là. Réessayez ou rentrez à
          l'accueil — votre progression est sauvegardée côté serveur.
        </p>
        <div className="text-[10px] text-white/40 mb-6 max-w-sm font-mono break-all text-center"
             data-testid="error-boundary-detail">
          {detail}
        </div>
        <div className="w-full max-w-sm space-y-2.5">
          <button onClick={this.reset}
                  data-testid="error-boundary-retry"
                  className="w-full py-3 rounded-full font-bold text-base flex items-center justify-center gap-2 active:scale-[0.97]"
                  style={{ background: 'linear-gradient(90deg, #FFD700, #F7931A)', color: '#111',
                           boxShadow: '0 10px 36px rgba(255,215,0,0.55)' }}>
            <ArrowClockwise size={18} weight="bold" /> Réessayer
          </button>
          <button onClick={this.goHome}
                  data-testid="error-boundary-home"
                  className="w-full py-3 rounded-full font-bold text-base flex items-center justify-center gap-2"
                  style={{ background: 'rgba(255,255,255,0.12)', color: '#fff',
                           border: '1px solid rgba(255,255,255,0.25)' }}>
            <House size={18} weight="bold" /> Retour à l'accueil
          </button>
        </div>
      </div>
    );
  }
}
