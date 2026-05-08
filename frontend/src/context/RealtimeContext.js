/**
 * JAPAP Messenger — Realtime Notification Context
 * ================================================
 * Opens a single global Socket.IO connection for realtime events
 * (tip, like, comment, money). Displays sonner toasts with anti-spam
 * coalescing on low-priority likes and exposes a live unread count
 * for the bottom-nav badge.
 */
import { createContext, useContext, useEffect, useRef, useState, useCallback } from 'react';
import { io } from 'socket.io-client';
import { toast } from 'sonner';
import axios from 'axios';
import { useAuth } from '@/context/AuthContext';

const API = process.env.REACT_APP_BACKEND_URL;

const RealtimeContext = createContext(null);

/**
 * Build a short "Xxx + N autres" label for coalesced like events.
 */
function coalesceLabel(senders) {
  const names = senders.map(s => s.name).filter(Boolean);
  if (names.length === 0) return 'Quelqu\'un';
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} et ${names[1]}`;
  return `${names[0]} et ${names.length - 1} autres`;
}

export function RealtimeProvider({ children }) {
  const { user } = useAuth();
  const [unreadCount, setUnreadCount] = useState(0);
  const socketRef = useRef(null);
  // Group likes per post within a 3s window to avoid spam
  const likeBufferRef = useRef(new Map()); // target_id -> { timer, senders: [] }

  /** Refresh unread count from server (used on mount + after marking read). */
  const refreshUnread = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/notifications/?page=1&limit=1`, { withCredentials: true });
      setUnreadCount(data.unread || 0);
    } catch {}
  }, []);

  const flushLikeBuffer = useCallback((targetId) => {
    const buf = likeBufferRef.current.get(targetId);
    if (!buf) return;
    likeBufferRef.current.delete(targetId);
    const n = buf.senders.length;
    const label = coalesceLabel(buf.senders);
    toast(
      n > 1 ? `${label} ont aimé votre publication` : `${label} a aimé votre publication`,
      { icon: '❤️', duration: 3500 }
    );
  }, []);

  useEffect(() => {
    if (!user) return;
    const sock = io(API, { path: '/api/socket.io', transports: ['websocket', 'polling'] });
    socketRef.current = sock;

    sock.on('connect', () => {
      const token = document.cookie.split(';').find(c => c.trim().startsWith('access_token='))?.split('=')[1];
      if (token) sock.emit('authenticate', { token });
    });

    // High priority: Tip received
    sock.on('notify_tip', (d) => {
      const name = d?.sender?.name || 'Quelqu\'un';
      const amount = d?.amount || '0';
      toast.success(`${name} vous a envoyé ${amount} XAF 🎁`, {
        description: d?.message ? `"${d.message}"` : 'Tip reçu sur votre contenu',
        duration: 6000,
      });
      setUnreadCount(c => c + 1);
    });

    // High priority: Chat money received
    sock.on('notify_money', (d) => {
      const name = d?.sender?.name || 'Quelqu\'un';
      const amount = d?.amount || '0';
      toast.success(`💸 ${name} vous a envoyé ${amount} XAF`, {
        description: d?.note ? `"${d.note}"` : 'Argent reçu dans Messenger',
        duration: 6000,
      });
      setUnreadCount(c => c + 1);
    });

    // High priority: JAPAP Connect revshare credit received
    sock.on('notify_connect_revshare', (d) => {
      const ccy = d?.currency || 'USD';
      const amountLocal = d?.amount_local;
      const display = amountLocal && ccy !== 'USD'
        ? `${amountLocal} ${ccy}`
        : `$${d?.amount_usd || '0'}`;
      const plan = d?.plan_name ? ` (${d.plan_name})` : '';
      const pctLabel = d?.pct ? `${d.pct}%` : 'votre part';
      toast.success(`🎉 Nouveau gain reçu — +${display}${plan}`, {
        description: `${pctLabel} JAPAP Pro via Connect. Continuez à partager votre WiFi !`,
        duration: 7000,
      });
      setUnreadCount(c => c + 1);
    });

    // Medium priority: Comment
    sock.on('notify_comment', (d) => {
      const name = d?.sender?.name || 'Quelqu\'un';
      toast(`${name} a commenté votre publication`, {
        icon: '💬',
        description: d?.preview || '',
        duration: 4500,
      });
      setUnreadCount(c => c + 1);
    });

    // Low priority: Like — coalesce per target within 3s
    sock.on('notify_like', (d) => {
      const targetId = d?.target_id;
      if (!targetId) return;
      const sender = { name: d?.sender?.name || 'Quelqu\'un' };
      const existing = likeBufferRef.current.get(targetId);
      if (existing) {
        existing.senders.push(sender);
      } else {
        const timer = setTimeout(() => flushLikeBuffer(targetId), 3000);
        likeBufferRef.current.set(targetId, { timer, senders: [sender] });
      }
      setUnreadCount(c => c + 1);
    });

    return () => {
      // Flush any pending like buffers before unmount
      likeBufferRef.current.forEach(buf => clearTimeout(buf.timer));
      likeBufferRef.current.clear();
      try { sock.disconnect(); } catch {}
    };
  }, [user, flushLikeBuffer]);

  // Initial + periodic fallback sync
  useEffect(() => {
    if (!user) { setUnreadCount(0); return; }
    refreshUnread();
    const id = setInterval(refreshUnread, 60000);
    return () => clearInterval(id);
  }, [user, refreshUnread]);

  return (
    <RealtimeContext.Provider value={{ unreadCount, refreshUnread, setUnreadCount }}>
      {children}
    </RealtimeContext.Provider>
  );
}

export function useRealtime() {
  const ctx = useContext(RealtimeContext);
  if (!ctx) return { unreadCount: 0, refreshUnread: () => {}, setUnreadCount: () => {} };
  return ctx;
}
