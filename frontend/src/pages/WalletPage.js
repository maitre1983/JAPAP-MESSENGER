import { useNavigate } from 'react-router-dom';
import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '@/context/AuthContext';
import axios from 'axios';
import { toast } from 'sonner';
import { Wallet, ArrowUp, ArrowDown, PaperPlaneTilt, MagnifyingGlass, X, IdentificationCard, CheckCircle, Clock, Warning, Crown, QrCode, Camera, HandCoins } from '@phosphor-icons/react';
import CryptoMethodIcon from '@/components/wallet/CryptoMethodIcon';
import EngagementPointsCard from '@/components/wallet/EngagementPointsCard';
import QRCodeCard from '@/components/wallet/QRCodeCard';
import QRScannerModal from '@/components/wallet/QRScannerModal';
import NowPaymentsDepositCard from '@/components/wallet/NowPaymentsDepositCard';
import HubtelDepositStatus from '@/components/wallet/HubtelDepositStatus';
import DepositConversionPreview from '@/components/wallet/DepositConversionPreview';
import WalletDepositCurrencySelector from '@/components/wallet/WalletDepositCurrencySelector';
import RequestPaymentModal from '@/components/wallet/RequestPaymentModal';
import PaymentRequestsWidget from '@/components/wallet/PaymentRequestsWidget';
// iter237g — Mobile Money : TOUTES les méthodes visibles pour TOUS les users
// avec un badge d'éligibilité. La validation pays reste côté serveur (403
// avec message clair si l'user soumet sans être éligible).
import OrangeMoneyDeposit from '@/components/wallet/OrangeMoneyDeposit';
import OrangeMoneyWithdraw from '@/components/wallet/OrangeMoneyWithdraw';
import WaveDeposit from '@/components/wallet/WaveDeposit';
import WaveWithdraw from '@/components/wallet/WaveWithdraw';
import MethodBadge from '@/components/wallet/MethodBadge';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

export default function WalletPage() {
  const { t } = useTranslation();
  const { user, refreshUser } = useAuth();
  const navigate = useNavigate();
  const [transactions, setTransactions] = useState([]);
  const [totalTx, setTotalTx] = useState(0);
  const [page, setPage] = useState(1);
  const [showSend, setShowSend] = useState(false);
  const [showDeposit, setShowDeposit] = useState(false);
  const [showWithdraw, setShowWithdraw] = useState(false);
  const [showKyc, setShowKyc] = useState(false);
  const [sendForm, setSendForm] = useState({ to_user_id: '', amount: '', notes: '' });
  // iter141eight — keep the picked recipient profile around so we can
  // surface it in a confirmation block before the user submits.
  const [selectedRecipient, setSelectedRecipient] = useState(null);
  const [depositForm, setDepositForm] = useState({ amount: '', method: 'usdt_trc20', reference: '', notes: '' });
  const [withdrawForm, setWithdrawForm] = useState({ amount: '', method: 'usdt_trc20', address: '', notes: '' });
  const [depositResult, setDepositResult] = useState(null);
  const [paymentMethods, setPaymentMethods] = useState({ deposit: [], withdraw: [], fee: {}, min_withdraw_usd: 0 });
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [feePreview, setFeePreview] = useState(null);
  const [showQRCard, setShowQRCard] = useState(false);
  const [showQRScanner, setShowQRScanner] = useState(false);
  const [showRequestModal, setShowRequestModal] = useState(false);
  const [publicSettings, setPublicSettings] = useState({});
  const [kycStatus, setKycStatus] = useState(null);
  // iter158 — canonical USD balance + live display equivalent.
  const [balanceInfo, setBalanceInfo] = useState(null);

  const loadBalance = useCallback(async () => {
    if (!user?.user_id) return;
    try {
      const { data } = await axios.get(`${API}/api/wallet/balance`,
        { withCredentials: true });
      setBalanceInfo(data);
    } catch (err) {
      console.error('[wallet] loadBalance failed:', err?.response?.status, err?.message);
    }
  }, [user?.user_id]);

  useEffect(() => { loadBalance(); }, [loadBalance]);

  const setDisplayCurrency = async (code) => {
    try {
      await axios.post(`${API}/api/wallet/display-currency`,
        { display_currency: code },
        { withCredentials: true });
      // iter161 — reload balance and toast the resolved currency so the user
      // gets immediate visual feedback (fixes the "Local (auto) doesn't do
      // anything" bug where the request succeeded but the UI showed no change
      // because IP detection wasn't wired into the balance endpoint).
      const { data } = await axios.get(`${API}/api/wallet/balance`, { withCredentials: true });
      setBalanceInfo(data);
      if (code === 'local') {
        if (data.display_currency && data.display_currency !== 'USD') {
          toast.success(`Devise locale détectée : ${data.display_currency}`);
        } else {
          toast.info("Devise locale indisponible — affichage en USD. Tu peux choisir manuellement depuis ton profil.");
        }
      } else if (code === 'USD') {
        toast.success("Affichage en USD");
      }
    } catch (err) {
      toast.error("Impossible de changer la devise d'affichage.");
    }
  };

  const loadTransactions = useCallback(async () => {
    // iter230 — Gate on user.user_id so we only fetch once auth is ready;
    // surface errors via console (was silently swallowed before).
    if (!user?.user_id) return;
    try {
      const { data } = await axios.get(`${API}/api/wallet/transactions?page=${page}&limit=20`, { withCredentials: true });
      setTransactions(data.transactions || []);
      setTotalTx(data.total || 0);
    } catch (err) {
      console.error('[wallet] loadTransactions failed:', err?.response?.status, err?.message);
    }
  }, [page, user?.user_id]);

  const loadGating = useCallback(async () => {
    if (!user?.user_id) return;
    try {
      const [s, k, pm] = await Promise.all([
        axios.get(`${API}/api/settings/public`),
        axios.get(`${API}/api/kyc/status`, { withCredentials: true }),
        axios.get(`${API}/api/wallet/payment-methods`, { withCredentials: true }),
      ]);
      setPublicSettings(s.data || {});
      setKycStatus(k.data || { status: 'none' });
      setPaymentMethods(pm.data);
    } catch (err) {
      console.error('[wallet] loadGating failed:', err?.response?.status, err?.message);
    }
  }, [user?.user_id]);

  // iter230 — depend on user?.user_id directly to avoid useCallback identity
  // races. The previous `[loadTransactions]` dep was provably never re-firing
  // for the transactions endpoint despite working for loadBalance/loadGating.
  useEffect(() => { loadTransactions(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [user?.user_id, page]);
  useEffect(() => { loadGating(); }, [loadGating]);

  // Handle Hubtel return redirect (?deposit=success|cancelled&tx=dep_xxx)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const deposit = params.get('deposit');
    const tx = params.get('tx');
    if (deposit === 'success' && tx) {
      toast.success(`Paiement reçu pour ${tx} — crédit en cours de confirmation.`);
      refreshUser(); loadTransactions();
    } else if (deposit === 'cancelled' && tx) {
      toast.error('Paiement annulé.');
    }
    if (deposit) {
      // clean URL
      window.history.replaceState({}, '', window.location.pathname);
    }
  }, [loadTransactions, refreshUser]);

  const resetForms = () => {
    setShowSend(false); setShowDeposit(false); setShowWithdraw(false); setDepositResult(null);
    setError(''); setSuccess(''); setSearchQuery(''); setSearchResults([]);
    setSendForm({ to_user_id: '', amount: '', notes: '' });
    setSelectedRecipient(null);
    setDepositForm({ amount: '', method: 'usdt_trc20', reference: '', notes: '' });
    setWithdrawForm({ amount: '', method: 'usdt_trc20', address: '', notes: '' });
  };

  const handleSend = async (e) => {
    e.preventDefault(); setError(''); setSuccess('');
    if (!sendForm.to_user_id) {
      setError('Sélectionne un destinataire dans la liste de recherche.');
      return;
    }
    if (!sendForm.amount || parseFloat(sendForm.amount) <= 0) {
      setError('Montant invalide.');
      return;
    }
    try {
      // iter141eight — generate a fresh idempotency key per submit so the
      // backend can short-circuit a duplicate (double-tap, network retry).
      const idem = (window.crypto?.randomUUID?.()
        || `${Date.now()}_${Math.random().toString(36).slice(2)}`);
      const { data } = await axios.post(`${API}/api/wallet/send`, {
        to_user_id: sendForm.to_user_id,
        amount: parseFloat(sendForm.amount),
        notes: sendForm.notes,
        idempotency_key: idem,
      }, { withCredentials: true });
      setSuccess(data.message); resetForms(); refreshUser(); loadTransactions();
    } catch (err) {
      setError(err.response?.data?.detail || 'Erreur');
    }
  };

  const handleDeposit = async (e) => {
    e.preventDefault(); setError(''); setSuccess('');
    try {
      const { data } = await axios.post(`${API}/api/wallet/deposit`, {
        amount: parseFloat(depositForm.amount),
        method: depositForm.method,
        reference: depositForm.reference,
        notes: depositForm.notes,
      }, { withCredentials: true });
      setDepositResult(data);
      loadTransactions();
      // For NowPayments crypto deposits, the new flow returns the full
      // payment payload (pay_address + pay_amount + payment_status). We
      // render <NowPaymentsDepositCard> inline — NO redirect to a hosted
      // page anymore (the previous flow left users with a blank page).
      if (data.method?.startsWith('nowpayments_') && data.pay_address) {
        setSuccess('Adresse de paiement générée — envoyez les fonds avec votre wallet.');
      } else if (data.checkout_url && !data.mocked) {
        // Hubtel still uses redirect-to-checkout flow.
        const providerName = 'Hubtel';
        setSuccess(`Redirection vers ${providerName} dans un instant…`);
        toast.success(`Redirection vers ${providerName}…`);
        setTimeout(() => { window.location.href = data.checkout_url; }, 1200);
      } else if (data.mocked) {
        setSuccess('Dépôt créé (MOCKED — configurez les clés API pour activer)');
      } else {
        setSuccess('Dépôt initié — suivez les instructions.');
      }
    } catch (err) { setError(err.response?.data?.detail || 'Erreur'); }
  };

  const handleWithdraw = async (e) => {
    e.preventDefault(); setError(''); setSuccess('');
    try {
      const { data } = await axios.post(`${API}/api/wallet/withdraw`, {
        amount: parseFloat(withdrawForm.amount),
        method: withdrawForm.method,
        address: withdrawForm.address,
        notes: withdrawForm.notes,
      }, { withCredentials: true });
      setSuccess(`${data.message} — Net: ${data.net_usd} USDT (frais ${data.fee_usd})`);
      resetForms(); refreshUser(); loadTransactions();
    } catch (err) {
      const detail = err.response?.data?.detail || 'Erreur';
      if (String(detail).startsWith('KYC_REQUIRED')) {
        setShowWithdraw(false); setShowKyc(true);
        toast.error('Vérification KYC requise pour les retraits.');
      } else {
        setError(detail);
      }
    }
  };

  const withdrawEnabled = publicSettings.withdraw_enabled !== 'false';
  const withdrawDisabledMsg = publicSettings.withdraw_disabled_message || '';
  const kycRequired = publicSettings.kyc_required_for_withdraw !== 'false';
  const isKycApproved = kycStatus?.status === 'approved';

  const openWithdraw = () => {
    if (!withdrawEnabled) {
      toast.error(withdrawDisabledMsg || 'Les retraits sont désactivés.');
      return;
    }
    if (kycRequired && !isKycApproved) {
      setShowKyc(true);
      return;
    }
    resetForms(); setShowWithdraw(true);
  };

  const handleSearchUser = async (q) => {
    setSearchQuery(q);
    if (q.length < 2) { setSearchResults([]); return; }
    try { const { data } = await axios.get(`${API}/api/users/search?q=${q}`, { withCredentials: true }); setSearchResults(data); } catch {}
  };

  const txTypeLabel = (tx) => {
    const map = {
      'send': tx.from_user_id === user?.user_id ? 'Envoye' : 'Recu',
      'deposit': 'Depot',
      'withdrawal': 'Retrait',
      'admin_credit': 'Credit Admin',
      'admin_debit': 'Debit Admin',
      // iter230 — quiz champion P2P escrow movements (P1 wallet history fix)
      'quiz_challenge_lock': 'Mise verrouillée — défi quiz',
      'quiz_challenge_release': 'Gain défi quiz',
      'quiz_challenge_refund': 'Remboursement défi quiz',
      'quiz_challenge_commission': 'Commission JAPAP — défi',
      'quiz_challenge_bonus': 'Bonus engagement — défi',
      // Other common types surfaced in tests
      'ads_campaign_fund': 'Financement campagne pub',
      'product_boost': 'Boost produit',
      'marketplace_purchase': 'Achat marketplace',
      'marketplace_release': 'Vente marketplace (libérée)',
      'marketplace_split_seller': 'Vente marketplace (split)',
      'ride_payment': 'Paiement course',
      'tip': 'Pourboire',
      'referral_bonus': 'Bonus parrainage',
      'fee_send': 'Frais envoi',
    };
    return map[tx.type] || tx.type;
  };

  const isOutgoing = (tx) => (
    (tx.type === 'send' && tx.from_user_id === user?.user_id)
    || tx.type === 'withdrawal'
    || tx.type === 'admin_debit'
    // iter230 — outgoing escrow movements (lock & commission debit the user)
    || tx.type === 'quiz_challenge_lock'
    || tx.type === 'ads_campaign_fund'
    || tx.type === 'product_boost'
    || tx.type === 'marketplace_purchase'
    || tx.type === 'ride_payment'
    || tx.type === 'fee_send'
  );

  // Détermine le provider (Hubtel vs NowPayments) à partir du libellé de la transaction.
  const depositProvider = (tx) => {
    const notes = String(tx.notes || '').toLowerCase();
    if (notes.includes('hubtel')) return 'hubtel';
    if (notes.includes('nowpayments')) return 'nowpayments';
    return null;
  };

  const [verifyingTx, setVerifyingTx] = useState(null);

  const handleVerifyDeposit = async (tx) => {
    const provider = depositProvider(tx);
    if (!provider) return;
    setVerifyingTx(tx.tx_id);
    try {
      const { data } = await axios.post(`${API}/api/wallet/${provider}/verify/${tx.tx_id}`, {}, { withCredentials: true });
      if (data.status === 'completed') {
        toast.success('Paiement confirmé — votre solde a été crédité.');
        refreshUser(); loadTransactions();
      } else if (data.status === 'already_completed') {
        toast.info('Ce dépôt est déjà crédité.');
        loadTransactions();
      } else {
        // not_paid_yet / check_failed / pending_verification / tout autre statut non final
        toast.info(
          "Dépôt en cours. Si après 10 minutes votre compte n'est pas crédité, " +
          "contactez depot@japapmessenger.com avec votre preuve de paiement " +
          (provider === 'nowpayments' ? "(code hash de transaction)." : "(capture d'écran Hubtel).")
        );
      }
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Erreur de vérification');
    } finally {
      setVerifyingTx(null);
    }
  };

  // Calcule l'âge d'une transaction en minutes (utilisé pour la bannière "en cours")
  const minutesSince = (isoDate) => {
    try { return Math.floor((Date.now() - new Date(isoDate).getTime()) / 60000); } catch { return 0; }
  };

  return (
    <div className="p-6 max-w-6xl mx-auto jp-animate-fadeIn" data-testid="wallet-page">
      {/* Balance Card */}
      <div className="jp-card-primary p-8 mb-8">
        <div className="flex items-center gap-3 mb-3">
          <Wallet size={28} weight="duotone" style={{ opacity: 0.8 }} />
          <span className="font-['Manrope'] text-sm" style={{ opacity: 0.75 }}>Solde disponible</span>
          {/* iter158 — display-currency toggle */}
          {balanceInfo && (
            <div className="ml-auto flex items-center gap-1 text-[11px]" data-testid="display-currency-toggle">
              <button
                onClick={() => setDisplayCurrency('USD')}
                data-testid="display-currency-usd"
                className={`px-2 py-1 rounded-full font-bold`}
                style={{
                  background: balanceInfo.display_currency === 'USD' ? 'rgba(255,255,255,0.25)' : 'transparent',
                  color: 'white', border: '1px solid rgba(255,255,255,0.3)',
                }}>USD</button>
              <button
                onClick={() => setDisplayCurrency('local')}
                data-testid="display-currency-local"
                className={`px-2 py-1 rounded-full font-bold`}
                style={{
                  background: balanceInfo.display_currency !== 'USD' ? 'rgba(255,255,255,0.25)' : 'transparent',
                  color: 'white', border: '1px solid rgba(255,255,255,0.3)',
                }}>Local ({balanceInfo.display_currency !== 'USD' ? balanceInfo.display_currency : 'auto'})</button>
            </div>
          )}
        </div>
        <p className="font-['Outfit'] text-5xl font-bold tracking-tight mb-1" data-testid="wallet-balance">
          {parseFloat(balanceInfo?.display_amount || balanceInfo?.balance_usd || user?.wallet_balance || '0')
            .toLocaleString('fr-FR', { minimumFractionDigits: 2 })}
          <span className="text-xl ml-2" style={{ opacity: 0.6 }}>
            {balanceInfo?.display_currency || 'USD'}
          </span>
        </p>
        {/* iter158 — canonical USD marker when displaying in a local currency */}
        {balanceInfo?.display_currency && balanceInfo.display_currency !== 'USD' && (
          <p className="text-xs font-['Manrope']" style={{ opacity: 0.7 }} data-testid="wallet-balance-usd-hint">
            Solde principal : <strong>{parseFloat(balanceInfo.balance_usd).toLocaleString('fr-FR', { minimumFractionDigits: 2 })} USD</strong>
          </p>
        )}
        <div className="flex gap-3 mt-6 flex-wrap">
          <button data-testid="send-money-button"
            onClick={() => { resetForms(); setShowSend(true); }}
            className="jp-btn jp-btn-sm"
            style={{ background: 'rgba(255,255,255,0.18)', color: 'white', backdropFilter: 'blur(4px)' }}>
            <PaperPlaneTilt size={16} /> Envoyer
          </button>
          <button data-testid="request-money-button"
            onClick={() => { resetForms(); setShowRequestModal(true); }}
            className="jp-btn jp-btn-sm"
            style={{ background: 'rgba(255,255,255,0.18)', color: 'white' }}>
            <HandCoins size={16} /> Demander
          </button>
          <button data-testid="deposit-button"
            onClick={() => { resetForms(); setShowDeposit(true); }}
            className="jp-btn jp-btn-sm"
            style={{ background: 'rgba(255,255,255,0.18)', color: 'white' }}>
            <ArrowDown size={16} /> Deposer
          </button>
          <button data-testid="withdraw-button"
            onClick={openWithdraw}
            className="jp-btn jp-btn-sm"
            style={{ background: withdrawEnabled ? 'var(--jp-secondary)' : 'rgba(255,255,255,0.1)', color: 'white', opacity: withdrawEnabled ? 1 : 0.6 }}>
            <ArrowUp size={16} /> Retirer
          </button>
          <button data-testid="wallet-qr-button"
            onClick={() => setShowQRCard(v => !v)}
            className="jp-btn jp-btn-sm"
            style={{ background: 'rgba(255,255,255,0.18)', color: 'white' }}>
            <QrCode size={16} /> Mon QR
          </button>
          <button data-testid="wallet-scan-button"
            onClick={() => setShowQRScanner(true)}
            className="jp-btn jp-btn-sm"
            style={{ background: 'rgba(255,255,255,0.18)', color: 'white' }}>
            <Camera size={16} /> Scanner
          </button>
        </div>
      </div>

      {/* QR Code (own) */}
      {showQRCard && <QRCodeCard onClose={() => setShowQRCard(false)} />}

      {/* iter141nine — Request Payment (Demander à recevoir) */}
      <RequestPaymentModal
        open={showRequestModal}
        onClose={() => setShowRequestModal(false)}
        currency={user?.wallet_currency || 'XAF'}
      />

      {/* QR Scanner Modal */}
      {showQRScanner && (
        <QRScannerModal
          onClose={() => setShowQRScanner(false)}
          onResolved={(contact) => {
            setShowQRScanner(false);
            resetForms();
            setSendForm({ to_user_id: contact.user_id, amount: '', notes: '' });
            setSearchQuery(contact.name || contact.username || '');
            setShowSend(true);
          }}
        />
      )}

      {/* Withdraw disabled banner */}
      {!withdrawEnabled && (
        <div className="jp-alert jp-alert-warning mb-4 flex items-center gap-2" data-testid="withdraw-disabled-banner">
          <Warning size={18} weight="bold" />
          <span>{withdrawDisabledMsg || 'Les retraits sont temporairement suspendus.'}</span>
        </div>
      )}

      {/* KYC banner */}
      {kycRequired && kycStatus && kycStatus.status !== 'approved' && (
        <div className="jp-card-elevated p-4 mb-4 flex items-center gap-3" data-testid="kyc-banner"
          style={{ borderLeft: '4px solid var(--jp-primary)' }}>
          <IdentificationCard size={24} weight="duotone" style={{ color: 'var(--jp-primary)' }} />
          <div className="flex-1 text-sm">
            {kycStatus.status === 'none' && <><strong>{t('wallet.verification_d_identite_kyc')}</strong> requise pour retirer des fonds.</>}
            {kycStatus.status === 'pending' && <><strong>{t('wallet.kyc_en_cours_de_revue')}</strong> — vous serez notifié sous 24h.</>}
            {kycStatus.status === 'rejected' && <><strong>{t('wallet.kyc_rejete')}</strong> {kycStatus.rejection_reason || 'Raison non fournie'}. Vous pouvez resoumettre.</>}
          </div>
          {(kycStatus.status === 'none' || kycStatus.status === 'rejected') && (
            <button onClick={() => setShowKyc(true)} className="jp-btn jp-btn-primary jp-btn-sm" data-testid="kyc-submit-btn">
              Soumettre mes documents
            </button>
          )}
          {kycStatus.status === 'pending' && <Clock size={20} style={{ color: 'var(--jp-warning, #F59E0B)' }} />}
          {kycStatus.status === 'approved' && <CheckCircle size={20} weight="fill" style={{ color: 'var(--jp-success)' }} />}
        </div>
      )}

      {error && <div className="jp-alert jp-alert-error mb-4" data-testid="wallet-error">{error}</div>}
      {success && <div className="jp-alert jp-alert-success mb-4" data-testid="wallet-success">{success}</div>}

      {/* Engagement points bridge — cycle Starter Pro */}
      <EngagementPointsCard />

      {/* iter141nineC — Mes demandes en cours (pending + recently paid) */}
      <PaymentRequestsWidget />

      {/* Send Form */}
      {showSend && (
        <div className="jp-card-elevated p-6 mb-6 jp-animate-scaleIn" data-testid="send-form">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-['Outfit'] text-lg font-bold" style={{ color: 'var(--jp-text)' }}>Envoyer de l'argent</h3>
            <button onClick={resetForms} className="p-1 rounded-lg" style={{ color: 'var(--jp-text-muted)' }}><X size={18} /></button>
          </div>
          <form onSubmit={handleSend} className="space-y-4">
            <div>
              <label className="jp-label">Destinataire</label>
              <div className="relative">
                <MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2" size={16} style={{ color: 'var(--jp-text-muted)' }} />
                <input data-testid="send-search-input" value={searchQuery} onChange={e => handleSearchUser(e.target.value)}
                  className="jp-input text-sm" style={{ paddingLeft: '36px' }} placeholder="Rechercher un utilisateur..." />
              </div>
              {searchResults.length > 0 && (
                <div className="mt-1 border rounded-xl overflow-hidden" style={{ borderColor: 'var(--jp-border)' }}>
                  {searchResults.slice(0, 5).map(u => (
                    <button key={u.user_id} type="button"
                      data-testid={`send-recipient-${u.user_id}`}
                      onClick={() => { setSendForm(f => ({ ...f, to_user_id: u.user_id })); setSelectedRecipient(u); setSearchResults([]); setSearchQuery(`${u.first_name} ${u.last_name}`); }}
                      className="w-full flex items-center gap-2 px-3 py-2.5 text-left text-sm font-['Manrope'] transition-colors"
                      onMouseEnter={e => e.currentTarget.style.background = 'var(--jp-surface-secondary)'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                      <div className="jp-avatar jp-avatar-sm jp-avatar-primary">{u.first_name?.[0]}</div>
                      <span>{u.first_name} {u.last_name}</span>
                    </button>
                  ))}
                </div>
              )}
              {/* iter141eight — Show the locked-in recipient so the user
                  knows EXACTLY who will receive the funds (anti-mistake). */}
              {sendForm.to_user_id && selectedRecipient && (
                <div className="mt-2 p-3 rounded-xl flex items-center gap-3"
                     data-testid="send-selected-recipient"
                     style={{ background: 'var(--jp-primary-subtle)',
                              border: '1px solid var(--jp-primary-muted)' }}>
                  <div className="jp-avatar jp-avatar-sm jp-avatar-primary">
                    {selectedRecipient.first_name?.[0]}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-bold" style={{ color: 'var(--jp-text)' }}>
                      {selectedRecipient.first_name} {selectedRecipient.last_name}
                    </div>
                    <div className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>
                      ID : {sendForm.to_user_id}
                    </div>
                  </div>
                  <button type="button"
                          onClick={() => { setSendForm(f => ({ ...f, to_user_id: '' })); setSelectedRecipient(null); setSearchQuery(''); }}
                          className="text-xs underline"
                          style={{ color: 'var(--jp-text-muted)' }}
                          data-testid="send-clear-recipient">
                    Changer
                  </button>
                </div>
              )}
            </div>
            <div>
              <label className="jp-label">Montant ({user?.wallet_currency || 'XAF'})</label>
              <input data-testid="send-amount-input" type="number" min="1" step="0.01" value={sendForm.amount}
                onChange={e => {
                  const v = e.target.value;
                  setSendForm(f => ({ ...f, amount: v }));
                  // Live fee preview (debounced via microtask)
                  const num = parseFloat(v);
                  if (num > 0) {
                    axios.get(`${API}/api/wallet/fees-preview?amount=${num}`, { withCredentials: true })
                      .then(r => setFeePreview(r.data))
                      .catch(() => setFeePreview(null));
                  } else { setFeePreview(null); }
                }}
                className="jp-input text-sm" placeholder="0.00" required />
              {feePreview && feePreview.enabled && parseFloat(feePreview.fee) > 0 && (
                <div className="mt-2 p-2.5 rounded-lg text-xs" data-testid="send-fee-preview"
                     style={{ background: 'var(--jp-primary-subtle)', color: 'var(--jp-text-secondary)' }}>
                  Frais applicables : <strong>{feePreview.fee} {user?.wallet_currency || 'XAF'}</strong>
                  {' '}· Montant reçu par le destinataire : <strong>{feePreview.net_to_recipient}</strong>
                  {feePreview.is_pro && <span className="ml-2 jp-badge" style={{ background: '#FFD70033', color: '#9A6700' }}>PRO</span>}
                </div>
              )}
            </div>
            <div>
              <label className="jp-label">Note (optionnel)</label>
              <input value={sendForm.notes} onChange={e => setSendForm(f => ({ ...f, notes: e.target.value }))}
                className="jp-input text-sm" placeholder="Motif du transfert" />
            </div>
            <div className="flex gap-3 pt-1">
              <button type="submit" data-testid="confirm-send-button"
                      disabled={!sendForm.to_user_id || !sendForm.amount || parseFloat(sendForm.amount) <= 0}
                      className="jp-btn jp-btn-primary disabled:opacity-50">
                Confirmer l'envoi
              </button>
              <button type="button" onClick={resetForms} className="jp-btn jp-btn-ghost">Annuler</button>
            </div>
          </form>
        </div>
      )}

      {/* Deposit Form */}
      {showDeposit && (
        <div className="jp-card-elevated p-6 mb-6 jp-animate-scaleIn" data-testid="deposit-form">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-['Outfit'] text-lg font-bold" style={{ color: 'var(--jp-text)' }}>Déposer des fonds</h3>
            <button onClick={resetForms} className="p-1 rounded-lg" style={{ color: 'var(--jp-text-muted)' }}><X size={18} /></button>
          </div>
          {!depositResult && (
            <form onSubmit={handleDeposit} className="space-y-4">
              <div>
                <label className="jp-label">Méthode de dépôt</label>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  {paymentMethods.deposit?.filter(m => m.enabled).map(m => (
                    <button key={m.id} type="button"
                      onClick={() => setDepositForm(f => ({ ...f, method: m.id }))}
                      className={`rounded-xl p-3 text-sm font-bold border-2 transition-all flex items-center gap-2 ${depositForm.method === m.id ? 'jp-btn-primary' : 'jp-btn-ghost'}`}
                      data-testid={`deposit-method-${m.id}`}
                      style={{ borderColor: depositForm.method === m.id ? 'var(--jp-primary)' : 'var(--jp-border)' }}>
                      <CryptoMethodIcon iconUrl={m.icon_url} chainIconUrl={m.chain_icon_url} icon={m.icon} alt={m.label} size={32} />
                      <span className="flex-1 text-left leading-tight">
                        {m.label}
                        {m.chain && <div className="text-[10px] font-normal opacity-80 mt-0.5">{m.chain}</div>}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="jp-label">Montant (USD)</label>
                <input data-testid="deposit-amount-input" type="number" min="1" step="0.01" value={depositForm.amount}
                  onChange={e => setDepositForm(f => ({ ...f, amount: e.target.value }))}
                  className="jp-input text-sm" placeholder="0.00" required />
                {/* iter159 — live conversion preview */}
                <DepositConversionPreview amount={depositForm.amount} method={depositForm.method} />
              </div>
              {/* iter209 — interactive currency selector for Hubtel deposits.
                  Hubtel supports many African currencies (GHS/XAF/XOF/NGN/…)
                  while NowPayments crypto deposits stay USDT-only. */}
              {(depositForm.method === 'hubtel_card' || depositForm.method === 'mobile_money') && (
                <WalletDepositCurrencySelector
                  amountUsd={depositForm.amount}
                  countryCode={user?.country_code}
                  onChange={({ currency }) => {
                    if (currency) {
                      setDepositForm(f => ({ ...f, currency }));
                    }
                  }}
                />
              )}
              {depositForm.method.startsWith('usdt_') && (
                <div>
                  <label className="jp-label">Hash de transaction (après envoi)</label>
                  <input value={depositForm.reference} onChange={e => setDepositForm(f => ({ ...f, reference: e.target.value }))}
                    className="jp-input text-sm" placeholder="Tx hash 0x... (optionnel, à remplir après envoi)" data-testid="deposit-txhash" />
                </div>
              )}
              <div className="flex gap-3 pt-1">
                <button type="submit" data-testid="confirm-deposit-button" className="jp-btn jp-btn-primary">Initier le dépôt</button>
                <button type="button" onClick={resetForms} className="jp-btn jp-btn-ghost">Annuler</button>
              </div>
            </form>
          )}
          {depositResult && (
            <div className="space-y-3" data-testid="deposit-result">
              {/* iter106 P0 fix — NowPayments crypto deposits get a dedicated
                  card with QR + address + amount + live status polling. */}
              {depositResult.method?.startsWith('nowpayments_') && depositResult.pay_address ? (
                <NowPaymentsDepositCard
                  deposit={depositResult}
                  onDone={() => {
                    setSuccess('Solde crédité ! Rafraîchissez la page si besoin.');
                    setDepositResult(null);
                    setShowDeposit(false);
                    loadTransactions();
                  }}
                  onClose={() => { setDepositResult(null); setShowDeposit(false); }}
                />
              ) : (
              <>
              <HubtelDepositStatus
                txId={depositResult.tx_id}
                onDone={() => {
                  setSuccess('Dépôt réussi ✅ — solde crédité.');
                  setDepositResult(null);
                  setShowDeposit(false);
                  loadTransactions();
                  loadBalance();
                  refreshUser();
                }}
              />
              {/* Encart support universel — affiché pour chaque dépôt en attente */}
              <div
                className="rounded-xl p-4"
                style={{ background: '#FFF7ED', color: '#7C2D12', border: '1px solid #FED7AA' }}
                data-testid="deposit-support-banner"
              >
                <div className="text-sm font-bold mb-1">⏳ Paiement en cours</div>
                <p className="text-xs leading-relaxed">
                  Nous interrogeons automatiquement Hubtel toutes les 4 secondes.
                  Dès que le paiement est confirmé, ton solde est crédité
                  instantanément — aucune validation humaine nécessaire.
                  <br /><br />
                  Si après <strong>10 minutes</strong> ton compte n'est pas crédité, contacte le support :
                  <br />
                  <a href="mailto:depot@japapmessenger.com" className="underline font-bold" data-testid="deposit-support-email">
                    depot@japapmessenger.com
                  </a>
                  <br />
                  Joins ta preuve :{' '}
                  {depositResult.method?.startsWith('nowpayments_') || depositResult.method?.startsWith('usdt_')
                    ? <><strong>hash</strong> de transaction blockchain</>
                    : <><strong>capture d'écran</strong> du paiement Hubtel</>
                  }.
                </p>
              </div>
              {depositResult.address && (
                <div className="rounded-xl p-3" style={{ background: '#FEF3C7', color: '#78350F' }}>
                  <div className="text-xs font-bold uppercase tracking-widest mb-1">Adresse {depositResult.chain}</div>
                  <code className="block text-sm break-all font-mono select-all" data-testid="deposit-address">
                    {depositResult.address}
                  </code>
                  <p className="text-xs mt-2">
                    Envoyez exactement {depositResult.amount_usd} USDT à cette adresse sur {depositResult.chain},
                    puis collez le hash de transaction dans le champ "Hash".
                  </p>
                </div>
              )}
              {depositResult.checkout_url && (
                <div className="rounded-xl p-4" style={{ background: depositResult.mocked ? '#FEF3C7' : '#DBEAFE', color: depositResult.mocked ? '#78350F' : '#1E3A8A' }}>
                  <div className="text-xs font-bold uppercase tracking-widest mb-2">
                    {depositResult.method?.startsWith('nowpayments_') ? 'NowPayments' : 'Hubtel'} Checkout {depositResult.mocked && '(MOCKED)'}
                  </div>
                  {!depositResult.mocked && (
                    <p className="text-sm mb-3 font-['Manrope']">
                      ⏳ Redirection automatique vers la page de paiement sécurisée…
                    </p>
                  )}
                  <a
                    href={depositResult.checkout_url}
                    target={depositResult.mocked ? '_blank' : '_self'}
                    rel="noreferrer"
                    className="jp-btn jp-btn-primary jp-btn-sm inline-flex"
                    data-testid="deposit-checkout-url"
                  >
                    {depositResult.mocked ? 'Ouvrir le mock →' : 'Continuer manuellement →'}
                  </a>
                </div>
              )}
              <button onClick={resetForms} className="jp-btn jp-btn-ghost jp-btn-full">Fermer</button>
              </>
              )}
            </div>
          )}
        </div>
      )}

      {/* Withdraw Form — USDT TRC20 / BEP20 only */}
      {showWithdraw && (
        <div className="jp-card-elevated p-6 mb-6 jp-animate-scaleIn" data-testid="withdraw-form">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-['Outfit'] text-lg font-bold" style={{ color: 'var(--jp-text)' }}>Retirer en USDT</h3>
            <button onClick={resetForms} className="p-1 rounded-lg" style={{ color: 'var(--jp-text-muted)' }}><X size={18} /></button>
          </div>
          <form onSubmit={handleWithdraw} className="space-y-4">
            <div>
              <label className="jp-label">Réseau USDT</label>
              <div className="grid grid-cols-2 gap-2">
                {paymentMethods.withdraw?.filter(m => m.enabled).map(m => (
                  <button key={m.id} type="button"
                    onClick={() => setWithdrawForm(f => ({ ...f, method: m.id }))}
                    className={`rounded-xl p-3 text-sm font-bold border-2 transition-all flex items-center gap-2 ${withdrawForm.method === m.id ? 'jp-btn-primary' : 'jp-btn-ghost'}`}
                    data-testid={`withdraw-method-${m.id}`}
                    style={{ borderColor: withdrawForm.method === m.id ? 'var(--jp-primary)' : 'var(--jp-border)' }}>
                    <CryptoMethodIcon iconUrl={m.icon_url} chainIconUrl={m.chain_icon_url} icon={m.icon} alt={m.label} size={32} />
                    <span className="flex-1 text-left leading-tight">
                      {m.label}
                      <div className="text-[10px] font-normal opacity-80 mt-0.5">{m.chain}</div>
                    </span>
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="jp-label">
                Adresse de destination USDT ({withdrawForm.method === 'usdt_trc20' ? 'TRON' : 'BSC'})
              </label>
              <input required value={withdrawForm.address}
                onChange={e => setWithdrawForm(f => ({ ...f, address: e.target.value.trim() }))}
                className="jp-input text-sm font-mono" placeholder={withdrawForm.method === 'usdt_trc20' ? 'T...' : '0x...'}
                data-testid="withdraw-address" />
            </div>
            <div>
              <label className="jp-label">
                Montant (USD) · Min : ${paymentMethods.min_withdraw_usd || 0}
              </label>
              <input data-testid="withdraw-amount-input" type="number"
                min={paymentMethods.min_withdraw_usd || 1} step="0.01" value={withdrawForm.amount}
                onChange={e => setWithdrawForm(f => ({ ...f, amount: e.target.value }))}
                className="jp-input text-sm" placeholder="0.00" required />
            </div>
            {/* 💡 Upsell Pro — only when user is non-Pro and a better Pro tier exists */}
            {paymentMethods.best_pro_fee && paymentMethods.fee?.source === 'default' && (
              <button
                type="button"
                onClick={() => navigate('/pro')}
                data-testid="withdraw-pro-upsell"
                className="w-full text-left rounded-2xl p-4 transition-all hover:scale-[1.01]"
                style={{
                  background: 'linear-gradient(135deg, #F59E0B 0%, #D97706 55%, #B45309 100%)',
                  color: 'white',
                  boxShadow: '0 10px 30px -8px rgba(217,119,6,0.45)',
                }}>
                <div className="flex items-start gap-3">
                  <div className="shrink-0 w-10 h-10 rounded-full flex items-center justify-center"
                    style={{ background: 'rgba(255,255,255,0.22)', backdropFilter: 'blur(6px)' }}>
                    <Crown size={20} weight="fill" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-['Outfit'] text-base font-extrabold leading-tight">
                      💡 Économisez {Math.max(0, (parseFloat(paymentMethods.fee.value) - parseFloat(paymentMethods.best_pro_fee.value))).toFixed(1)}% sur chaque retrait
                    </div>
                    <div className="font-['Manrope'] text-[13px] mt-0.5" style={{ opacity: 0.95 }}>
                      Passez <strong>{paymentMethods.best_pro_fee.plan_name || 'Pro'}</strong>
                      {paymentMethods.best_pro_fee.price_usd ? ` ($${paymentMethods.best_pro_fee.price_usd}/mois)` : ''} et payez seulement <strong>{paymentMethods.best_pro_fee.value}%</strong> de frais au lieu de {paymentMethods.fee.value}%.
                    </div>
                    {withdrawForm.amount && parseFloat(withdrawForm.amount) > 0 && (() => {
                      const amt = parseFloat(withdrawForm.amount) || 0;
                      const cur = paymentMethods.fee.mode === 'flat'
                        ? parseFloat(paymentMethods.fee.value)
                        : amt * parseFloat(paymentMethods.fee.value) / 100;
                      const best = paymentMethods.best_pro_fee.mode === 'flat'
                        ? parseFloat(paymentMethods.best_pro_fee.value)
                        : amt * parseFloat(paymentMethods.best_pro_fee.value) / 100;
                      const save = Math.max(0, cur - best);
                      if (save <= 0) return null;
                      return (
                        <div className="mt-2 text-xs font-['Manrope']" data-testid="upsell-savings-this-tx"
                          style={{ background: 'rgba(255,255,255,0.2)', padding: '6px 10px', borderRadius: '999px', display: 'inline-block' }}>
                          Sur ce retrait : vous économiseriez <strong>{save.toFixed(2)} USDT</strong>
                        </div>
                      );
                    })()}
                  </div>
                  <div className="shrink-0 font-['Outfit'] text-sm font-bold opacity-90">
                    → Devenir Pro
                  </div>
                </div>
              </button>
            )}
            {withdrawForm.amount && paymentMethods.fee?.mode && (
              <div className="rounded-xl p-3 text-sm" style={{ background: 'var(--jp-primary-subtle)' }} data-testid="withdraw-fee-preview">
                {paymentMethods.fee.source === 'plan' && paymentMethods.fee.plan_id && (
                  <div className="flex items-center gap-1.5 mb-1.5 text-xs font-bold" style={{ color: '#B45309' }}
                    data-testid="withdraw-fee-pro-badge">
                    <span>👑</span>
                    <span>Tarif JAPAP Pro — plan {paymentMethods.fee.plan_id}</span>
                  </div>
                )}
                {(() => {
                  const amt = parseFloat(withdrawForm.amount) || 0;
                  const fv = parseFloat(paymentMethods.fee.value) || 0;
                  const fee = paymentMethods.fee.mode === 'flat' ? fv : (amt * fv / 100);
                  const net = Math.max(0, amt - fee);
                  return (
                    <>
                      <div className="flex justify-between"><span>Frais ({paymentMethods.fee.mode === 'flat' ? `fixe ${fv} USDT` : `${fv}%`})</span><strong>−{fee.toFixed(2)} USDT</strong></div>
                      <div className="flex justify-between mt-1"><span>Vous recevrez</span><strong style={{ color: '#10B981' }}>{net.toFixed(2)} USDT</strong></div>
                    </>
                  );
                })()}
              </div>
            )}
            <div className="flex gap-3 pt-1">
              <button type="submit" data-testid="confirm-withdraw-button" className="jp-btn jp-btn-secondary">Confirmer le retrait</button>
              <button type="button" onClick={resetForms} className="jp-btn jp-btn-ghost">Annuler</button>
            </div>
          </form>
        </div>
      )}

      {/* iter237g — Mobile Money : TOUTES les méthodes désormais visibles pour
          tous les utilisateurs, avec un badge d'éligibilité. Le serveur reste
          l'autorité finale (HTTP 403 avec message clair si l'utilisateur
          soumet sans être éligible). Le code-resolver country (iter237e)
          continue d'alimenter le `eligible` flag pour le visuel uniquement. */}
      {user && (() => {
        const cc_iso = String(user?.country_code || '').trim().toUpperCase();
        const cc_raw = String(user?.country || '').trim().toUpperCase();
        const cc = cc_iso && cc_iso.length === 2 ? cc_iso
                                                 : (cc_raw.length === 2 ? cc_raw : '');
        const phone = String(user?.phone_number || user?.phone || '');
        const WAVE_COUNTRIES = ['BF', 'CI', 'ML', 'NE', 'SN', 'GM', 'UG'];
        // Eligibility flags (used ONLY for visual hint — server validates).
        const eligibleOMDep = !!cc && cc !== 'GH';
        const eligibleOMWith = cc === 'CM' && phone.startsWith('+237');
        const eligibleWave = WAVE_COUNTRIES.includes(cc);
        const isAdmin = !!(user?.is_admin || user?.role === 'admin' || user?.role === 'superadmin');
        const reload = () => { loadTransactions(); loadBalance?.(); };
        return (
          <div data-testid="mobile-money-section">
            {isAdmin && (
              <div className="text-[10px] opacity-60 px-3 py-2 rounded-lg mb-2"
                   style={{ background: 'rgba(0,0,0,0.05)', fontFamily: 'monospace',
                            color: 'var(--jp-text)', border: '1px dashed var(--jp-border)' }}
                   data-testid="mobile-money-admin-debug">
                🔍 Debug admin · Pays détecté : <strong>{cc || '∅'}</strong>
                {' '}· code_iso=<code>{cc_iso || '∅'}</code>
                {' '}· raw=<code>{cc_raw || '∅'}</code>
                {' '}· phone=<code>{phone || '∅'}</code>
                <br />OM-dépôt {eligibleOMDep ? '✓' : '✗'}
                {' '}· OM-retrait {eligibleOMWith ? '✓' : '✗'}
                {' '}· Wave {eligibleWave ? '✓' : '✗'}
              </div>
            )}
            <h3 className="font-['Outfit'] text-base font-bold mb-2" style={{ color: 'var(--jp-text)' }}>
              💸 Mobile Money
            </h3>

            {/* Orange Money — Dépôt */}
            <div data-testid="om-deposit-block">
              <MethodBadge
                availability="Cameroun uniquement — numéros +237"
                countries="CM"
                restricted={true}
                eligible={eligibleOMDep}
                methodId="orange_money_cm"
                flow="deposit"
              />
              <OrangeMoneyDeposit onSuccess={reload} />
            </div>

            {/* Orange Money — Retrait */}
            <div data-testid="om-withdraw-block">
              <MethodBadge
                availability="Cameroun uniquement — numéros +237"
                countries="CM"
                restricted={true}
                eligible={eligibleOMWith}
                methodId="orange_money_cm"
                flow="withdraw"
              />
              <OrangeMoneyWithdraw onSuccess={reload} />
            </div>

            {/* Wave — Dépôt */}
            <div data-testid="wave-deposit-block">
              <MethodBadge
                availability="Burkina Faso · Côte d'Ivoire · Mali · Niger · Sénégal · Gambie · Ouganda"
                countries="BF · CI · ML · NE · SN · GM · UG"
                restricted={true}
                eligible={eligibleWave}
                methodId="wave"
                flow="deposit"
              />
              <WaveDeposit onSuccess={reload} />
            </div>

            {/* Wave — Retrait */}
            <div data-testid="wave-withdraw-block">
              <MethodBadge
                availability="Burkina Faso · Côte d'Ivoire · Mali · Niger · Sénégal · Gambie · Ouganda"
                countries="BF · CI · ML · NE · SN · GM · UG"
                restricted={true}
                eligible={eligibleWave}
                methodId="wave"
                flow="withdraw"
              />
              <WaveWithdraw onSuccess={reload} />
            </div>
          </div>
        );
      })()}

      {/* Transactions */}
      <div className="jp-card" data-testid="transactions-list">
        <div className="px-6 py-4 border-b" style={{ borderColor: 'var(--jp-border)' }}>
          <h3 className="font-['Outfit'] text-lg font-bold" style={{ color: 'var(--jp-text)' }}>Historique des transactions</h3>
          <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>{totalTx} transaction{totalTx > 1 ? 's' : ''} au total</p>
        </div>
        <div>
          {transactions.map(tx => {
            const isPendingDeposit = tx.type === 'deposit' && tx.status === 'pending' && depositProvider(tx);
            const ageMin = isPendingDeposit ? minutesSince(tx.created_at) : 0;
            const isStale = isPendingDeposit && ageMin >= 10;
            return (
            <div key={tx.tx_id}>
            <div className="flex items-center gap-4 px-6 py-4 border-b transition-colors"
              style={{ borderColor: 'rgba(229,228,226,0.5)' }}
              data-testid={`transaction-${tx.tx_id}`}
              onMouseEnter={e => e.currentTarget.style.background = 'var(--jp-surface-secondary)'}
              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
              <div className="w-10 h-10 rounded-full flex items-center justify-center"
                style={{ background: isOutgoing(tx) ? 'var(--jp-error-light)' : 'var(--jp-success-light)' }}>
                {isOutgoing(tx) ? <ArrowUp size={18} style={{ color: 'var(--jp-error)' }} /> : <ArrowDown size={18} style={{ color: 'var(--jp-success)' }} />}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium font-['Manrope']" style={{ color: 'var(--jp-text)' }}>{txTypeLabel(tx)}</p>
                <p className="text-xs truncate" style={{ color: 'var(--jp-text-muted)' }}>{tx.notes || tx.tx_id}</p>
              </div>
              <div className="text-right">
                <p className="text-sm font-semibold font-['Outfit']"
                  style={{ color: isOutgoing(tx) ? 'var(--jp-error)' : 'var(--jp-success)' }}>
                  {isOutgoing(tx) ? '-' : '+'}{parseFloat(tx.amount).toLocaleString('fr-FR')} {tx.currency}
                </p>
                <p className="text-[10px]" style={{ color: 'var(--jp-text-muted)' }}>{new Date(tx.created_at).toLocaleDateString('fr-FR')}</p>
              </div>
              <span className={`jp-badge ${tx.status === 'completed' ? 'jp-badge-success' : tx.status === 'pending' ? 'jp-badge-warning' : 'jp-badge-error'}`}>
                {tx.status === 'completed' ? 'Termine' : tx.status === 'pending' ? 'En attente' : tx.status}
              </span>
              {isPendingDeposit && (
                <button
                  type="button"
                  onClick={() => handleVerifyDeposit(tx)}
                  disabled={verifyingTx === tx.tx_id}
                  data-testid={`verify-deposit-${tx.tx_id}`}
                  className="jp-btn jp-btn-primary jp-btn-sm"
                  style={{ opacity: verifyingTx === tx.tx_id ? 0.6 : 1 }}>
                  {verifyingTx === tx.tx_id ? t('wallet.verif') : t('wallet.verifier_mon_paiement')}
                </button>
              )}
            </div>
            {isStale && (
              <div
                className="px-6 py-3 border-b text-xs font-['Manrope']"
                style={{ background: '#FFF7ED', color: '#7C2D12', borderColor: 'rgba(229,228,226,0.5)' }}
                data-testid={`stale-deposit-banner-${tx.tx_id}`}
              >
                ⚠️ Dépôt en cours depuis {ageMin} min. Si votre compte n'est toujours pas crédité, contactez{' '}
                <a href="mailto:depot@japapmessenger.com" className="underline font-bold">depot@japapmessenger.com</a>
                {' '}avec votre {depositProvider(tx) === 'hubtel' ? "capture d'écran Hubtel" : 'code hash de transaction'}.
              </div>
            )}
            </div>
            );
          })}
          {transactions.length === 0 && (
            <div className="px-6 py-16 text-center font-['Manrope'] text-sm" style={{ color: 'var(--jp-text-muted)' }}>
              Aucune transaction pour le moment
            </div>
          )}
        </div>
        {totalTx > 20 && (
          <div className="flex justify-center gap-2 px-6 py-4 border-t" style={{ borderColor: 'var(--jp-border)' }}>
            <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="jp-btn jp-btn-ghost jp-btn-sm" style={{ opacity: page <= 1 ? 0.3 : 1 }}>Precedent</button>
            <span className="px-4 py-1.5 text-sm font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>Page {page}</span>
            <button disabled={page * 20 >= totalTx} onClick={() => setPage(p => p + 1)} className="jp-btn jp-btn-ghost jp-btn-sm" style={{ opacity: page * 20 >= totalTx ? 0.3 : 1 }}>Suivant</button>
          </div>
        )}
      </div>

      {/* KYC Submission Modal */}
      {showKyc && (
        <KycSubmissionModal
          onClose={() => setShowKyc(false)}
          onSubmitted={() => { setShowKyc(false); loadGating(); toast.success('Documents soumis — revue sous 24h'); }}
        />
      )}
    </div>
  );
}

function KycSubmissionModal({ onClose, onSubmitted }) {
  const { t } = useTranslation();
  const [form, setForm] = useState({ full_name: '', id_type: 'national_id', id_number: '' });
  const [idPhoto, setIdPhoto] = useState(null);
  const [idBackPhoto, setIdBackPhoto] = useState(null);
  const [selfie, setSelfie] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState('');

  const isDualSide = form.id_type === 'national_id' || form.id_type === 'drivers_license';

  // Light client-side blur heuristic — warn if the chosen image is too
  // small or the user picks a clearly screenshot-sized file. Real check
  // runs server-side but this tightens UX.
  const checkLocalImage = (file) => {
    if (!file) return null;
    if (file.size < 30 * 1024) return t('wallet.la_photo_semble_trop_petite_30_kb_r');
    if (file.size > 12 * 1024 * 1024) return 'Photo trop volumineuse (> 12 MB).';
    return null;
  };

  const submit = async (e) => {
    e.preventDefault(); setErr(''); setSubmitting(true);
    if (!idPhoto || !selfie) {
      setErr('Photo de la pièce et selfie obligatoires'); setSubmitting(false); return;
    }
    if (isDualSide && !idBackPhoto) {
      setErr('Le verso de la pièce est requis pour ce type de document.');
      setSubmitting(false); return;
    }
    for (const [f, lbl] of [[idPhoto,'recto'], [idBackPhoto,'verso'], [selfie,'selfie']]) {
      const w = checkLocalImage(f);
      if (w) { setErr(`${lbl}: ${w}`); setSubmitting(false); return; }
    }
    try {
      const fd = new FormData();
      fd.append('full_name', form.full_name);
      fd.append('id_type', form.id_type);
      fd.append('id_number', form.id_number);
      fd.append('id_photo', idPhoto);
      if (isDualSide && idBackPhoto) fd.append('id_back_photo', idBackPhoto);
      fd.append('selfie', selfie);
      const { data } = await axios.post(`${API}/api/kyc/submit`, fd, {
        withCredentials: true, headers: { 'Content-Type': 'multipart/form-data' },
      });
      // Surface AI alerts up-front so the user can immediately understand
      // why their submission may be slow to approve.
      if (data?.ai_alerts?.length > 0 && data.ai_risk_score !== 'low') {
        toast.warning(`Pré-vérif: ${data.ai_alerts.slice(0, 2).join(' · ')}`,
                       { duration: 6000 });
      }
      onSubmitted();
    } catch (e) {
      setErr(e.response?.data?.detail || 'Erreur');
    } finally { setSubmitting(false); }
  };

  const FileField = ({ label, hint, file, onPick, testid }) => (
    <div>
      <label className="jp-label">{label}</label>
      <input type="file" accept="image/*" required={file === null && !!testid}
        onChange={e => onPick(e.target.files?.[0] || null)}
        className="jp-input text-sm" data-testid={testid} />
      {hint && (
        <p className="text-[11px] mt-1" style={{ color: 'var(--jp-text-muted)' }}>
          {hint}
        </p>
      )}
      {file && (
        <div className="text-[11px] mt-1" style={{ color: 'var(--jp-success)' }}>
          ✓ {file.name} · {(file.size / 1024).toFixed(0)} KB
        </div>
      )}
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 overflow-y-auto"
      style={{ background: 'rgba(0,0,0,0.6)' }} data-testid="kyc-modal">
      <div className="jp-card-elevated max-w-md w-full p-6 jp-animate-scaleIn my-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-['Outfit'] text-lg font-bold">Vérification d'identité</h3>
          <button onClick={onClose} className="p-1 rounded-lg" style={{ color: 'var(--jp-text-muted)' }} data-testid="kyc-close">
            <X size={18} />
          </button>
        </div>
        <p className="text-xs mb-3" style={{ color: 'var(--jp-text-muted)' }}>
          Soumettez les documents demandés pour débloquer les retraits. Vos
          photos sont compressées localement et chiffrées en transit.
        </p>
        <form onSubmit={submit} className="space-y-3">
          <div>
            <label className="jp-label">Nom complet</label>
            <input className="jp-input text-sm" required value={form.full_name}
              onChange={e => setForm(f => ({ ...f, full_name: e.target.value }))}
              data-testid="kyc-full-name" />
          </div>
          <div>
            <label className="jp-label">Type de pièce</label>
            <select className="jp-input text-sm" value={form.id_type}
              onChange={e => setForm(f => ({ ...f, id_type: e.target.value }))}
              data-testid="kyc-id-type">
              <option value="national_id">{t('wallet.carte_d_identite_nationale')}</option>
              <option value="passport">{t('wallet.passeport')}</option>
              <option value="drivers_license">{t('wallet.permis_de_conduire')}</option>
            </select>
          </div>
          <div>
            <label className="jp-label">Numéro de pièce</label>
            <input className="jp-input text-sm" required value={form.id_number}
              onChange={e => setForm(f => ({ ...f, id_number: e.target.value }))}
              data-testid="kyc-id-number" />
          </div>

          {/* Dynamic document upload section */}
          {form.id_type === 'passport' ? (
            <FileField
              label="Photo du passeport (page d'identité)"
              hint="Assurez-vous que le nom, le numéro, la photo et la date d'expiration sont parfaitement lisibles."
              file={idPhoto}
              onPick={setIdPhoto}
              testid="kyc-id-photo"
            />
          ) : (
            <>
              <FileField
                label="Photo de la pièce — recto"
                hint="Toutes les informations doivent être lisibles : nom, numéro, photo et date d'expiration."
                file={idPhoto}
                onPick={setIdPhoto}
                testid="kyc-id-photo"
              />
              <FileField
                label="Photo de la pièce — verso"
                hint="Le verso doit aussi être net, sans reflet ni coupure."
                file={idBackPhoto}
                onPick={setIdBackPhoto}
                testid="kyc-id-back-photo"
              />
            </>
          )}

          <FileField
            label="Selfie tenant la pièce"
            hint="Tenez votre pièce d'identité dans la main gauche, avec votre visage et toutes les informations clairement visibles. Bonne lumière, pas de flou."
            file={selfie}
            onPick={setSelfie}
            testid="kyc-selfie"
          />

          {err && <div className="jp-alert jp-alert-error text-xs" data-testid="kyc-err">{err}</div>}
          <button type="submit" disabled={submitting} className="jp-btn jp-btn-primary w-full"
            data-testid="kyc-submit-form">
            {submitting ? 'Envoi en cours…' : t('wallet.soumettre_pour_verification')}
          </button>
        </form>
      </div>
    </div>
  );
}
