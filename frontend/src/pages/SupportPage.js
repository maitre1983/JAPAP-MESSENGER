/**
 * SupportPage — iter90
 *
 * Flow (mobile-first):
 *   1. Greeting + AI chat. User types a question, Claude Sonnet 4.5 answers.
 *   2. A persistent "Contacter un agent" button escalates to a ticket form.
 *   3. Ticket form (subject, category, message) → POST /api/support/ticket,
 *      passes the full AI transcript as context. Email acknowledgement.
 *   4. "Mes tickets" tab shows user's recent tickets with status badge.
 */
import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  PaperPlaneTilt, Headset, Robot, User as UserIcon, CheckCircle, Clock as ClockIcon,
  ChatCircleDots, Ticket, ArrowsClockwise,
} from '@phosphor-icons/react';

const API = process.env.REACT_APP_BACKEND_URL;

const CATEGORIES = [
  { id: 'account', label: 'Compte' },
  { id: 'wallet', label: 'Wallet / Paiement' },
  { id: 'kyc', label: 'KYC / Identité' },
  { id: 'games', label: 'Jeux / Points' },
  { id: 'technical', label: 'Bug technique' },
  { id: 'other', label: 'Autre' },
];

const STATUS_STYLE = {
  open:        { bg: '#FEF3C7', fg: '#9A6700', label: 'Ouvert' },
  in_progress: { bg: '#DBEAFE', fg: '#1E40AF', label: 'En cours' },
  resolved:    { bg: '#D1FAE5', fg: '#047857', label: 'Résolu' },
  closed:      { bg: '#E5E7EB', fg: '#374151', label: 'Fermé' },
};

const GREETER = {
  role: 'assistant',
  content:
    "Bonjour ! 👋 Je suis JAPAP Assist, l'assistant support officiel. Posez-moi votre question — je peux vous aider sur le Wallet, les jeux, le KYC, votre compte, ou tout autre sujet JAPAP. Si je ne peux pas résoudre votre problème, vous pourrez contacter un agent humain.",
};

export default function SupportPage() {
  const { t } = useTranslation();
  const [view, setView] = useState('chat'); // chat | ticket | my-tickets
  const [messages, setMessages] = useState([GREETER]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);

  const scrollRef = useRef(null);
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, sending]);

  const sendAI = async () => {
    const text = input.trim();
    if (!text || sending) return;
    const next = [...messages, { role: 'user', content: text }];
    setMessages(next);
    setInput('');
    setSending(true);
    try {
      const r = await axios.post(
        `${API}/api/support/ai-chat`,
        { messages: next.slice(-10) }, // send max 10 last msgs as context
        { withCredentials: true },
      );
      setMessages((m) => [...m, { role: 'assistant', content: r.data.reply }]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        {
          role: 'assistant',
          content:
            "Je n'arrive pas à répondre pour le moment. Cliquez sur \"Contacter un agent\" ci-dessous pour créer un ticket.",
        },
      ]);
    } finally {
      setSending(false);
    }
  };

  const handleKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendAI();
    }
  };

  return (
    <div className="min-h-[calc(100vh-80px)]" data-testid="support-page">
      <div className="max-w-2xl mx-auto p-4 sm:p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="font-['Outfit'] text-2xl font-bold" style={{ color: 'var(--jp-text)' }}>
              Support JAPAP
            </h1>
            <p className="text-sm" style={{ color: 'var(--jp-text-muted)' }}>
              Assistant IA 24/7 · Agent humain sur demande
            </p>
          </div>
          <Headset size={32} weight="duotone" style={{ color: '#FFD700' }} />
        </div>

        {/* Tabs */}
        <div className="jp-tabs mb-4 flex">
          <button
            onClick={() => setView('chat')}
            data-testid="support-tab-chat"
            className={`jp-tab ${view === 'chat' ? 'jp-tab-active' : ''}`}
          >
            <ChatCircleDots size={14} weight="bold" className="inline mr-1" /> Chat IA
          </button>
          <button
            onClick={() => setView('ticket')}
            data-testid="support-tab-ticket"
            className={`jp-tab ${view === 'ticket' ? 'jp-tab-active' : ''}`}
          >
            <Ticket size={14} weight="bold" className="inline mr-1" /> Nouveau ticket
          </button>
          <button
            onClick={() => setView('my-tickets')}
            data-testid="support-tab-my-tickets"
            className={`jp-tab ${view === 'my-tickets' ? 'jp-tab-active' : ''}`}
          >
            <CheckCircle size={14} weight="bold" className="inline mr-1" /> Mes tickets
          </button>
        </div>

        {/* === Chat view === */}
        {view === 'chat' && (
          <div className="jp-card-elevated p-0 overflow-hidden" data-testid="support-chat-panel">
            <div
              ref={scrollRef}
              className="p-4 space-y-3"
              style={{ maxHeight: '58vh', overflowY: 'auto', minHeight: '40vh' }}
            >
              {messages.map((m, i) => (
                <MessageBubble key={i} role={m.role} content={m.content} />
              ))}
              {sending && <MessageBubble role="assistant" content="…" isTyping />}
            </div>

            {/* Escalation CTA */}
            <div
              className="flex items-center justify-between gap-3 p-3 border-t"
              style={{ borderColor: 'var(--jp-border)', background: 'var(--jp-surface-subtle)' }}
            >
              <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
                Pas satisfait de la réponse ?
              </div>
              <button
                onClick={() => setView('ticket')}
                data-testid="support-escalate-btn"
                className="jp-btn jp-btn-secondary jp-btn-sm"
              >
                <Headset size={14} weight="bold" /> Contacter un agent
              </button>
            </div>

            {/* Input */}
            <div className="flex items-center gap-2 p-3 border-t" style={{ borderColor: 'var(--jp-border)' }}>
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKey}
                disabled={sending}
                data-testid="support-chat-input"
                placeholder={t('support.votre_question')}
                className="jp-input flex-1 text-sm"
                maxLength={2000}
              />
              <button
                onClick={sendAI}
                disabled={sending || !input.trim()}
                data-testid="support-chat-send"
                className="jp-btn jp-btn-primary jp-btn-sm"
              >
                <PaperPlaneTilt size={16} weight="fill" />
              </button>
            </div>
          </div>
        )}

        {/* === Ticket form === */}
        {view === 'ticket' && (
          <TicketForm
            aiTranscript={messages.filter((m, i) => !(i === 0 && m === GREETER))}
            onCreated={() => setView('my-tickets')}
          />
        )}

        {/* === My tickets === */}
        {view === 'my-tickets' && <MyTicketsList />}
      </div>
    </div>
  );
}

/* ───────────── Message Bubble ───────────── */

function MessageBubble({ role, content, isTyping = false }) {
  const isUser = role === 'user';
  return (
    <div className={`flex gap-2 ${isUser ? 'flex-row-reverse' : ''}`}>
      <div
        className="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0"
        style={{
          background: isUser ? 'var(--jp-primary-subtle)' : '#FFF7D1',
          color: isUser ? 'var(--jp-primary)' : '#B45309',
        }}
      >
        {isUser ? <UserIcon size={16} weight="fill" /> : <Robot size={18} weight="fill" />}
      </div>
      <div
        className="flex-1 max-w-[82%]"
        style={{ textAlign: isUser ? 'right' : 'left' }}
      >
        <div
          className="inline-block px-3.5 py-2.5 rounded-2xl text-sm whitespace-pre-wrap"
          style={{
            background: isUser ? 'var(--jp-primary)' : 'var(--jp-surface-subtle)',
            color: isUser ? 'white' : 'var(--jp-text)',
            borderTopRightRadius: isUser ? '4px' : '16px',
            borderTopLeftRadius: isUser ? '16px' : '4px',
            lineHeight: 1.55,
          }}
        >
          {isTyping ? (
            <span className="inline-flex gap-1">
              <span className="jp-typing-dot" />
              <span className="jp-typing-dot" style={{ animationDelay: '0.15s' }} />
              <span className="jp-typing-dot" style={{ animationDelay: '0.3s' }} />
            </span>
          ) : (
            content
          )}
        </div>
      </div>
    </div>
  );
}

/* ───────────── Ticket Form ───────────── */

function TicketForm({ aiTranscript, onCreated }) {
  const { t } = useTranslation();
  const [subject, setSubject] = useState('');
  const [category, setCategory] = useState('other');
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (subject.trim().length < 3 || message.trim().length < 10 || submitting) return;
    setSubmitting(true);
    try {
      const r = await axios.post(
        `${API}/api/support/ticket`,
        {
          subject: subject.trim(),
          category,
          message: message.trim(),
          ai_transcript: aiTranscript && aiTranscript.length > 0 ? aiTranscript.slice(-10) : null,
        },
        { withCredentials: true },
      );
      toast.success(`Ticket #${r.data.ticket_id} créé. Un email vous a été envoyé.`);
      onCreated?.();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Impossible de créer le ticket.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={submit} className="jp-card-elevated p-4 space-y-3" data-testid="support-ticket-form">
      <div>
        <label className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--jp-text-secondary)' }}>
          Catégorie
        </label>
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          data-testid="support-ticket-category"
          className="jp-input w-full mt-1 text-sm"
        >
          {CATEGORIES.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--jp-text-secondary)' }}>
          Sujet
        </label>
        <input
          type="text"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          required
          minLength={3}
          maxLength={300}
          placeholder={t('support.ex_mon_retrait_usdt_est_bloque_depu')}
          data-testid="support-ticket-subject"
          className="jp-input w-full mt-1 text-sm"
        />
      </div>

      <div>
        <label className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--jp-text-secondary)' }}>
          Décrivez votre problème
        </label>
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          required
          minLength={10}
          maxLength={4000}
          rows={6}
          placeholder={t('support.plus_vous_etes_precis_dates_tx_id_c')}
          data-testid="support-ticket-message"
          className="jp-input w-full mt-1 text-sm"
          style={{ resize: 'vertical' }}
        />
        <div className="text-[11px] text-right mt-1" style={{ color: 'var(--jp-text-muted)' }}>
          {message.length} / 4000
        </div>
      </div>

      {aiTranscript && aiTranscript.length > 0 && (
        <div className="p-2.5 rounded-lg text-[11px]" style={{ background: 'var(--jp-primary-subtle)', color: 'var(--jp-text-secondary)' }}>
          <strong>ℹ</strong> L'historique de votre conversation avec l'IA ({aiTranscript.length} messages) sera joint au ticket pour accélérer le traitement.
        </div>
      )}

      <button
        type="submit"
        disabled={submitting}
        data-testid="support-ticket-submit"
        className="jp-btn jp-btn-primary w-full"
      >
        {submitting ? 'Envoi…' : 'Envoyer le ticket'}
      </button>
    </form>
  );
}

/* ───────────── My Tickets List ───────────── */

function MyTicketsList() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/api/support/my-tickets?limit=50`, { withCredentials: true });
      setItems(r.data.items || []);
    } catch {
      toast.error('Chargement impossible');
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, []);

  if (loading) return <div className="text-sm text-center py-8" style={{ color: 'var(--jp-text-muted)' }}>Chargement…</div>;
  if (items.length === 0) {
    return (
      <div className="jp-card-elevated p-8 text-center" data-testid="support-tickets-empty">
        <Ticket size={40} weight="duotone" style={{ color: 'var(--jp-text-muted)' }} className="mx-auto mb-2" />
        <div className="font-semibold text-sm">Aucun ticket pour le moment</div>
        <div className="text-xs mt-1" style={{ color: 'var(--jp-text-muted)' }}>
          Essayez d'abord le chat IA — il résout 70 % des demandes instantanément.
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-2" data-testid="support-tickets-list">
      <div className="flex items-center justify-between">
        <div className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>
          {items.length} ticket(s)
        </div>
        <button onClick={load} className="jp-btn jp-btn-ghost jp-btn-sm">
          <ArrowsClockwise size={12} /> Rafraîchir
        </button>
      </div>
      {items.map((t) => {
        const s = STATUS_STYLE[t.status] || STATUS_STYLE.open;
        return (
          <div key={t.ticket_id} className="jp-card-elevated p-3" data-testid={`support-ticket-${t.ticket_id}`}>
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[11px]" style={{ color: 'var(--jp-text-muted)' }}>
                    #{t.ticket_id}
                  </span>
                  <span
                    className="jp-badge"
                    style={{ background: s.bg, color: s.fg }}
                  >
                    {s.label}
                  </span>
                </div>
                <div className="font-semibold text-sm mt-0.5 truncate">{t.subject}</div>
                <div className="text-[11px] mt-1 flex items-center gap-2" style={{ color: 'var(--jp-text-muted)' }}>
                  <ClockIcon size={11} /> {new Date(t.created_at).toLocaleString('fr-FR')}
                  <span>·</span>
                  <span>{t.category}</span>
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
