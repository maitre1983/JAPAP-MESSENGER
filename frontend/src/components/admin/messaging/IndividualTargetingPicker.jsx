/**
 * IndividualTargetingPicker — manual email input + live user search with
 * multi-select, status badges, dedupe.
 *
 * Controlled component:
 *   value   = { user_ids: [], emails: [], resolved: { [user_id]: userObj, [email]: true } }
 *   onChange(next)
 *
 * Rules:
 *  - A user already in user_ids cannot be re-added via email input (dedupe by email)
 *  - Unsubscribed users are flagged with a warning badge
 *  - Migration-pending users get a badge so admin knows before sending
 */
import { useState, useEffect, useRef } from 'react';
import { MagnifyingGlass, Plus, X, UserCircle, WarningCircle, Crown } from '@phosphor-icons/react';
import { msgApi } from './messagingApi';

const EMAIL_RE = /^[a-z0-9!#$%&'*+/=?^_`{|}~.-]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$/i;

export default function IndividualTargetingPicker({ value, onChange, disabled = false }) {
  const [emailInput, setEmailInput] = useState('');
  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const debounceRef = useRef(null);

  const resolved = value.resolved || {};
  const selectedUserIds = value.user_ids || [];
  const selectedEmails = value.emails || [];
  const totalSelected = selectedUserIds.length + selectedEmails.length;

  useEffect(() => {
    if (!query.trim()) { setSearchResults([]); return; }
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      setSearching(true);
      try { setSearchResults(await msgApi.searchUsers(query.trim())); }
      catch { setSearchResults([]); }
      finally { setSearching(false); }
    }, 250);
    return () => debounceRef.current && clearTimeout(debounceRef.current);
  }, [query]);

  const selectedEmailsLower = new Set([
    ...selectedEmails.map((e) => e.toLowerCase()),
    ...selectedUserIds.map((uid) => (resolved[uid]?.email || '').toLowerCase()).filter(Boolean),
  ]);

  const addManualEmail = () => {
    const e = emailInput.trim().toLowerCase();
    if (!EMAIL_RE.test(e)) return;
    if (selectedEmailsLower.has(e)) { setEmailInput(''); return; }
    onChange({
      ...value,
      emails: [...selectedEmails, e],
      resolved: { ...resolved, [e]: { email: e, external: true } },
    });
    setEmailInput('');
  };

  const addUser = (u) => {
    if (selectedUserIds.includes(u.user_id)) return;
    if (selectedEmailsLower.has(u.email.toLowerCase())) return;
    onChange({
      ...value,
      user_ids: [...selectedUserIds, u.user_id],
      resolved: { ...resolved, [u.user_id]: u },
    });
  };

  const removeUser = (uid) => {
    const { [uid]: _, ...rest } = resolved;
    onChange({ ...value, user_ids: selectedUserIds.filter((x) => x !== uid), resolved: rest });
  };
  const removeEmail = (em) => {
    const { [em]: _, ...rest } = resolved;
    onChange({ ...value, emails: selectedEmails.filter((x) => x !== em), resolved: rest });
  };

  return (
    <div className="space-y-3" data-testid="individual-targeting-picker">
      {/* Selected chips */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] font-bold uppercase tracking-wide"
                style={{ color: 'var(--jp-text-muted)' }}>
            Destinataires sélectionnés
          </span>
          <span className="text-[11px] font-bold" style={{ color: '#F7931A' }}>
            {totalSelected}
          </span>
        </div>
        {totalSelected === 0 && (
          <p className="text-[11px] p-2 rounded-lg"
             style={{ background: 'var(--jp-surface-secondary)', color: 'var(--jp-text-muted)', border: '1px dashed var(--jp-border)' }}>
            Aucun destinataire. Ajoutez un email manuellement ou recherchez un utilisateur ci-dessous.
          </p>
        )}
        <div className="flex flex-wrap gap-1.5" data-testid="targeting-selected-chips">
          {selectedUserIds.map((uid) => {
            const u = resolved[uid] || { email: uid, name: 'Utilisateur' };
            return (
              <Chip key={uid} onRemove={() => removeUser(uid)}
                    testid={`chip-user-${uid}`} disabled={disabled}>
                <UserCircle size={12} weight="fill" />
                <span className="font-semibold">{u.name}</span>
                <span className="opacity-60">{u.email}</span>
                {u.email_subscribed === false && (
                  <WarningCircle size={11} weight="fill" style={{ color: '#b45309' }} title="Désabonné" />
                )}
                {u.migration_pending && (
                  <span className="text-[8px] font-bold px-1 rounded"
                        style={{ background: 'rgba(247,147,26,0.2)', color: '#b45309' }}>MIG</span>
                )}
                {u.is_pro && <Crown size={11} weight="fill" style={{ color: '#F7931A' }} />}
              </Chip>
            );
          })}
          {selectedEmails.map((em) => (
            <Chip key={em} onRemove={() => removeEmail(em)}
                  testid={`chip-email-${em}`} disabled={disabled}>
              <span className="opacity-60">📧</span>
              <span className="font-semibold">{em}</span>
              <span className="text-[9px] opacity-70">externe</span>
            </Chip>
          ))}
        </div>
      </div>

      {/* Manual email input */}
      {!disabled && (
        <div>
          <label className="block text-[10px] font-bold uppercase tracking-wide mb-1"
                 style={{ color: 'var(--jp-text-muted)' }}>
            Envoyer à un email spécifique
          </label>
          <div className="flex gap-2">
            <input value={emailInput} onChange={(e) => setEmailInput(e.target.value)}
                   onKeyDown={(e) => e.key === 'Enter' && addManualEmail()}
                   type="email" placeholder="user@example.com"
                   className="jp-input flex-1"
                   data-testid="targeting-manual-email-input" />
            <button onClick={addManualEmail}
                    disabled={!EMAIL_RE.test(emailInput.trim().toLowerCase())}
                    className="jp-btn jp-btn-secondary text-xs flex items-center gap-1 disabled:opacity-40"
                    data-testid="targeting-manual-email-add">
              <Plus size={13} weight="bold" /> Ajouter
            </button>
          </div>
        </div>
      )}

      {/* User search */}
      {!disabled && (
        <div>
          <label className="block text-[10px] font-bold uppercase tracking-wide mb-1"
                 style={{ color: 'var(--jp-text-muted)' }}>
            Rechercher un utilisateur existant
          </label>
          <div className="flex items-center gap-2 px-3 py-2 rounded-xl"
               style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}>
            <MagnifyingGlass size={14} style={{ opacity: 0.6 }} />
            <input value={query} onChange={(e) => setQuery(e.target.value)}
                   placeholder="Email, prénom, nom ou username…"
                   className="bg-transparent outline-none flex-1 text-sm"
                   data-testid="targeting-user-search" />
            {searching && <span className="text-[10px] opacity-60">…</span>}
          </div>
          {searchResults.length > 0 && (
            <ul className="mt-2 max-h-56 overflow-y-auto rounded-xl"
                style={{ background: 'var(--jp-surface-secondary)', border: '1px solid var(--jp-border)' }}
                data-testid="targeting-search-results">
              {searchResults.map((u) => {
                const already = selectedUserIds.includes(u.user_id) ||
                                selectedEmailsLower.has(u.email.toLowerCase());
                return (
                  <li key={u.user_id}>
                    <button onClick={() => addUser(u)} disabled={already}
                            className="w-full flex items-center gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-white/5 disabled:opacity-50 disabled:cursor-not-allowed"
                            data-testid={`targeting-search-result-${u.user_id}`}>
                      <UserCircle size={18} weight="fill" style={{ color: '#3B82F6', flexShrink: 0 }} />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="font-semibold truncate">{u.name}</span>
                          {u.is_pro && (
                            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full flex items-center gap-0.5"
                                  style={{ background: 'rgba(247,147,26,0.15)', color: '#F7931A' }}>
                              <Crown size={8} weight="fill" /> PRO
                            </span>
                          )}
                          {!u.is_active && (
                            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full"
                                  style={{ background: 'rgba(113,113,122,0.15)', color: '#71717a' }}>
                              INACTIF
                            </span>
                          )}
                          {!u.email_subscribed && (
                            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full inline-flex items-center gap-0.5"
                                  style={{ background: 'rgba(185,28,28,0.15)', color: '#b91c1c' }}>
                              <WarningCircle size={9} weight="fill" /> DÉSABONNÉ
                            </span>
                          )}
                          {u.migration_pending && (
                            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full"
                                  style={{ background: 'rgba(247,147,26,0.15)', color: '#b45309' }}>
                              MIGRATION
                            </span>
                          )}
                        </div>
                        <div className="opacity-60 truncate">{u.email}{u.username && ` · @${u.username}`}</div>
                      </div>
                      {already
                        ? <span className="text-[10px]" style={{ color: '#059669' }}>✓ ajouté</span>
                        : <Plus size={14} weight="bold" style={{ color: '#F7931A', flexShrink: 0 }} />}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {query.trim() && !searching && searchResults.length === 0 && (
            <p className="text-[11px] mt-2 opacity-60">Aucun résultat pour « {query} ».</p>
          )}
        </div>
      )}
    </div>
  );
}

function Chip({ children, onRemove, testid, disabled }) {
  return (
    <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px]"
          style={{ background: 'rgba(247,147,26,0.1)', border: '1px solid rgba(247,147,26,0.3)' }}
          data-testid={testid}>
      {children}
      {!disabled && (
        <button onClick={onRemove} className="ml-0.5 opacity-60 hover:opacity-100"
                aria-label="Retirer">
          <X size={10} weight="bold" />
        </button>
      )}
    </span>
  );
}
