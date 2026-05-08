/**
 * iter198 — JAPAP Crypto Staking App STUB
 * ========================================
 * L'ancien module de staking Firebase a été retiré avec les 7 dépendances
 * legacy (bootstrap, react-bootstrap, react-toastify, react-icons, moment,
 * react-redux-firebase, redux-firestore) pour réduire le bundle initial de
 * ~700KB. Le composant est maintenu ici en tant que placeholder pour ne
 * pas casser l'import existant dans ServicesPage.js.
 *
 * Lorsque le nouveau module staking sera prêt (roadmap P2), il remplacera
 * ce stub.
 */
export default function JapapStakingApp() {
  return (
    <div
      data-testid="staking-stub"
      className="rounded-2xl p-6 text-center"
      style={{
        background: 'rgba(255, 255, 255, 0.03)',
        border: '1px solid rgba(255, 255, 255, 0.08)',
        color: 'rgba(255, 255, 255, 0.7)',
      }}
    >
      <div className="text-2xl mb-2">🔒</div>
      <div className="text-base font-semibold mb-1" style={{ color: 'white' }}>
        Staking — Bientôt disponible
      </div>
      <div className="text-xs opacity-70">
        Le module de staking JAPAP est en refonte.
        Revenez très bientôt pour staker vos MIR.
      </div>
    </div>
  );
}
