import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChatCircle, Wallet, ShieldCheck, Lightning, ArrowRight, Users, Globe, GameController } from '@phosphor-icons/react';
import LanguageSwitcher from '@/components/LanguageSwitcher';

export default function LandingPage() {
  const { t } = useTranslation();
  const stats = [
    { n: t('landing.stat_users_number'),    l: t('landing.stat_users') },
    { n: t('landing.stat_messages_number'), l: t('landing.stat_messages') },
  ];
  const features = [
    { icon: ChatCircle,     title: t('landing.feat_msg_title'),         desc: t('landing.feat_msg_desc'),         color: '#4A90E2' },
    { icon: Wallet,         title: t('landing.feat_wallet_title'),      desc: t('landing.feat_wallet_desc'),      color: '#059669' },
    { icon: Globe,          title: t('landing.feat_marketplace_title'), desc: t('landing.feat_marketplace_desc'), color: '#E01C2E' },
    { icon: GameController, title: t('landing.feat_games_title'),       desc: t('landing.feat_games_desc'),       color: '#F7931A' },
  ];

  return (
    <div className="min-h-screen flex flex-col" data-testid="landing-page">
      {/* ══ HERO SECTION — Dark blue gradient with lifestyle visual ══ */}
      <div className="relative overflow-hidden" style={{ background: '#0B0542', minHeight: '100vh' }}>
        {/* Header */}
        <header className="relative z-20 flex items-center justify-between px-6 md:px-12 py-4">
          <div className="inline-flex items-center justify-center rounded-xl bg-white px-3 py-1.5 shadow-sm">
            <img src="/japap-logo.jpg" alt="JAPAP" className="h-8 object-contain" />
          </div>
          <div className="flex items-center gap-3">
            {/* iter206 — language switcher premium dans le header landing */}
            <LanguageSwitcher variant="light" compact data-testid="landing-lang-switcher" />
            <Link to="/login" data-testid="landing-login-btn"
              className="px-5 py-2 rounded-full text-sm font-['Manrope'] font-semibold text-white/80 transition-all hover:text-white"
              style={{ border: '1px solid rgba(255,255,255,0.3)' }}>
              {t('landing.nav_login')}
            </Link>
            <Link to="/register" data-testid="landing-register-btn"
              className="px-5 py-2 rounded-full text-sm font-['Manrope'] font-semibold text-white transition-all"
              style={{ background: '#E01C2E' }}>
              {t('landing.nav_signup')}
            </Link>
          </div>
        </header>

        {/* Background image */}
        <div className="absolute inset-0 z-0">
          <img src="https://images.pexels.com/photos/28999878/pexels-photo-28999878.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"
            alt="" className="w-full h-full object-cover opacity-20" />
          <div className="absolute inset-0" style={{ background: 'radial-gradient(ellipse at 30% 50%, rgba(15,5,107,0.6) 0%, #0B0542 70%)' }} />
        </div>

        {/* Hero Content */}
        <div className="relative z-10 flex flex-col md:flex-row items-center justify-between px-6 md:px-16 lg:px-24 py-16 md:py-24 max-w-7xl mx-auto">
          <div className="max-w-xl text-center md:text-left mb-12 md:mb-0">
            <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full mb-6"
              style={{ background: 'rgba(224,28,46,0.15)', border: '1px solid rgba(224,28,46,0.3)' }}>
              <Lightning size={14} style={{ color: '#E01C2E' }} weight="fill" />
              <span className="text-xs font-['Manrope'] font-semibold" style={{ color: '#E01C2E' }}>{t('landing.badge')}</span>
            </div>

            <h1 className="font-['Outfit'] text-5xl md:text-6xl lg:text-7xl font-extrabold text-white leading-tight mb-6">
              {t('landing.hero_connect')}<br/>
              <span style={{ color: '#E01C2E' }}>{t('landing.hero_share')}</span><br/>
              {t('landing.hero_impact')}
            </h1>

            <p className="font-['Manrope'] text-white/60 text-lg mb-8 max-w-md">
              {t('landing.hero_subtitle')}
            </p>

            <div className="flex flex-col sm:flex-row gap-4 justify-center md:justify-start">
              <Link to="/register" data-testid="hero-cta"
                className="px-8 py-4 rounded-full font-['Manrope'] font-bold text-white text-lg flex items-center justify-center gap-2 transition-all hover:opacity-90"
                style={{ background: '#E01C2E' }}>
                {t('landing.cta_start')} <ArrowRight size={20} />
              </Link>
              <Link to="/login"
                className="px-8 py-4 rounded-full font-['Manrope'] font-semibold text-white/80 text-lg flex items-center justify-center transition-all"
                style={{ border: '1px solid rgba(255,255,255,0.2)' }}>
                {t('landing.cta_login')}
              </Link>
            </div>

            {/* Stats */}
            <div className="flex gap-8 mt-10 justify-center md:justify-start">
              {stats.map((s, i) => (
                <div key={i} className="text-center md:text-left">
                  <p className="font-['Outfit'] text-2xl font-bold text-white">{s.n}</p>
                  <p className="text-xs font-['Manrope'] text-white/40">{s.l}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Phone mockup */}
          <div className="relative w-64 md:w-72 flex-shrink-0">
            <div className="w-full aspect-[9/19] rounded-[2.5rem] overflow-hidden shadow-2xl"
              style={{ border: '3px solid rgba(255,255,255,0.1)', background: '#1a1a2e' }}>
              <img src="https://images.pexels.com/photos/9885405/pexels-photo-9885405.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"
                alt="JAPAP App" className="w-full h-full object-cover opacity-80" />
              <div className="absolute inset-0 flex flex-col justify-end p-6">
                <div className="absolute top-4 left-0 right-0 flex justify-center">
                  <div className="w-24 h-1 rounded-full bg-white/30" />
                </div>
                <div className="inline-flex items-center rounded-lg bg-white/95 px-2 py-1 self-start mb-1 shadow">
                  <img src="/japap-logo.jpg" alt="JAPAP" className="h-6 object-contain" />
                </div>
                <p className="text-xs text-white/60 font-['Manrope']">{t('landing.phone_caption')}</p>
              </div>
            </div>
            {/* Glow effect */}
            <div className="absolute -inset-8 rounded-full opacity-20 blur-3xl" style={{ background: '#E01C2E' }} />
          </div>
        </div>

        {/* ══ FEATURES SECTION ══ */}
        <div className="relative z-10 px-6 md:px-16 lg:px-24 pb-20 max-w-7xl mx-auto">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {features.map((f, i) => (
              <div key={i} className="p-5 rounded-2xl transition-all"
                style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)' }}>
                <div className="w-10 h-10 rounded-xl flex items-center justify-center mb-3" style={{ background: `${f.color}20` }}>
                  <f.icon size={22} weight="duotone" style={{ color: f.color }} />
                </div>
                <h3 className="font-['Outfit'] text-base font-bold text-white mb-1">{f.title}</h3>
                <p className="text-xs font-['Manrope'] text-white/50">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Footer */}
        <div className="relative z-10 py-4 px-6 text-center" style={{ borderTop: '1px solid rgba(255,255,255,0.08)' }}>
          <p className="text-white/30 text-xs font-['Manrope']">{t('landing.copyright')}</p>
        </div>
      </div>
    </div>
  );
}
