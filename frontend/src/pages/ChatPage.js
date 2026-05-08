import { useState, useEffect, useRef, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import { useCall } from '@/context/CallContext';
import axios from 'axios';
import { io } from 'socket.io-client';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  PaperPlaneRight, MagnifyingGlass, DotsThreeVertical, CaretLeft, Paperclip,
  UsersThree, Plus, Phone, VideoCamera, CurrencyDollar, X as CloseIcon, Smiley,
  ArrowBendUpLeft, ArrowBendUpRight, Check, Checks, Clock, Eye,
} from '@phosphor-icons/react';
import EmojiPicker, { QUICK_REACTIONS } from '@/components/EmojiPicker';
import VoiceRecorder, { VoicePlayer } from '@/components/VoiceRecorder';
import TranslateButton from '@/components/TranslateButton';
import ForwardModal from '@/components/ForwardModal';
import ForwardChainModal from '@/components/ForwardChainModal';
import CallSummaryMessageBubble from '@/components/chat/CallSummaryMessageBubble';
import SmartProductCard, { extractProductLink } from '@/components/chat/SmartProductCard';
import MediaFilterEditor from '@/components/media/MediaFilterEditor';
import ZoomableImage from '@/components/media/ZoomableImage';
// iter237m — Live USD→local equivalent preview in send-money modal.
import WalletDepositCurrencySelector from '@/components/wallet/WalletDepositCurrencySelector';
// iter237u — "Vu hier à 14h32" presence label when peer is offline.
import formatLastSeen from '@/utils/formatLastSeen';

const API = process.env.REACT_APP_BACKEND_URL;

export default function ChatPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { startCall, startGroupCall, joinGroupCall, liveGroupCalls, state: callState } = useCall();
  const [conversations, setConversations] = useState([]);
  const [activeConv, setActiveConv] = useState(null);
  const [messages, setMessages] = useState([]);
  const [newMessage, setNewMessage] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [typingUsers, setTypingUsers] = useState({});
  const [showCreateGroup, setShowCreateGroup] = useState(false);
  const [groupName, setGroupName] = useState('');
  const [groupMembers, setGroupMembers] = useState([]);
  const [memberSearch, setMemberSearch] = useState('');
  const [memberResults, setMemberResults] = useState([]);
  const [showMoneyModal, setShowMoneyModal] = useState(false);
  const [moneyAmount, setMoneyAmount] = useState('');
  const [moneyNote, setMoneyNote] = useState('');
  const [moneySending, setMoneySending] = useState(false);
  const [moneyError, setMoneyError] = useState('');
  const [showEmojiPicker, setShowEmojiPicker] = useState(false);
  // iter237t — Mobile-only "+" toggle that collapses the 4 secondary
  // buttons (file / money / emoji / voice) into a popover. Desktop
  // keeps the row visible at all times.
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const [reactingMsgId, setReactingMsgId] = useState(null);
  const [replyingTo, setReplyingTo] = useState(null);      // message being replied to
  const [forwardingMsg, setForwardingMsg] = useState(null); // message being forwarded
  const [chainForMsgId, setChainForMsgId] = useState(null); // forward chain modal
  const [msgMenuId, setMsgMenuId] = useState(null);         // long-press menu open for this msg
  // iter147 — Unified media filter editor for chat attachments.
  const [filterEditor, setFilterEditor] = useState({ open: false, files: [], mode: 'upload' });
  const socketRef = useRef(null);
  const messagesEndRef = useRef(null);
  const typingTimeoutRef = useRef(null);
  const longPressTimerRef = useRef(null);

  const loadConversations = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/api/messages/conversations`, { withCredentials: true });
      setConversations(data);
    } catch {}
  }, []);

  const loadMessages = useCallback(async (convId) => {
    try {
      const { data } = await axios.get(`${API}/api/messages/conversations/${convId}`, { withCredentials: true });
      setMessages(data);
    } catch {}
  }, []);

  useEffect(() => { loadConversations(); }, [loadConversations]);

  // Deep-link support: /chat?conv=xxx&msg=yyy → auto-select conv + scroll
  const [urlParams, setUrlParams] = useSearchParams();
  useEffect(() => {
    const targetConvId = urlParams.get('conv');
    if (!targetConvId || !conversations.length) return;
    const c = conversations.find((x) => x.conv_id === targetConvId);
    if (c) {
      setActiveConv(c);
      // Clear the params AFTER selection so switching convs manually later
      // doesn't get forced back to this one.
      setTimeout(() => {
        const p = new URLSearchParams(urlParams);
        p.delete('conv');
        // keep msg so the scroll handler below can use it
        setUrlParams(p, { replace: true });
      }, 100);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversations]);

  // Scroll to the targeted message once it's rendered
  useEffect(() => {
    const targetMsgId = urlParams.get('msg');
    if (!targetMsgId || !messages.length) return;
    const el = document.querySelector(`[data-testid^="call-summary-${targetMsgId}"]`)
            || document.querySelector(`[data-msg-id="${targetMsgId}"]`);
    if (el) {
      setTimeout(() => {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        el.classList.add('jp-task-highlight');
        setTimeout(() => el.classList.remove('jp-task-highlight'), 2500);
      }, 300);
      const p = new URLSearchParams(urlParams);
      p.delete('msg');
      setUrlParams(p, { replace: true });
    }
  }, [messages, urlParams, setUrlParams]);

  useEffect(() => {
    if (!user) return;
    // Durci : reconnexion automatique avec backoff exponentiel
    socketRef.current = io(API, {
      path: '/api/socket.io',
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 800,
      reconnectionDelayMax: 5000,
      timeout: 15000,
    });
    const authenticate = () => {
      const token = document.cookie.split(';').find(c => c.trim().startsWith('access_token='))?.split('=')[1];
      if (token) socketRef.current.emit('authenticate', { token });
    };
    socketRef.current.on('connect', () => {
      authenticate();
      // On reconnect, re-join active conv silently
      if (activeConv?.conv_id) {
        socketRef.current.emit('join_conversation', { conv_id: activeConv.conv_id });
      }
    });
    socketRef.current.on('reconnect', () => {
      toast.success('Connexion restaurée', { duration: 1500 });
      loadConversations();
    });
    socketRef.current.on('disconnect', (reason) => {
      if (reason !== 'io client disconnect') {
        toast.warning('Connexion perdue — reconnexion…', { duration: 1500, id: 'sock-down' });
      }
    });
    socketRef.current.on('new_message', (msg) => {
      setMessages(prev => {
        // iter237v — Replace optimistic bubble (keyed on client_msg_id)
        // with the canonical server payload. Falls back to msg_id dedup
        // for incoming peer messages.
        if (msg.client_msg_id) {
          const idx = prev.findIndex(m =>
            m.client_msg_id === msg.client_msg_id || m.msg_id === msg.client_msg_id
          );
          if (idx !== -1) {
            const next = [...prev];
            next[idx] = { ...msg, pending: false };
            return next;
          }
        }
        if (prev.some(m => m.msg_id === msg.msg_id)) return prev;
        // ACK delivery to the sender once we receive it in the active conv
        if (activeConv?.conv_id === msg.conv_id && msg.sender_id !== user.user_id) {
          socketRef.current?.emit('mark_delivered', { conv_id: msg.conv_id, msg_ids: [msg.msg_id] });
        }
        return [...prev, msg];
      });
      setConversations(prev => {
        const updated = prev.map(c => c.conv_id === msg.conv_id ? { ...c, last_message: msg, updated_at: msg.created_at } : c);
        return updated.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
      });
    });
    socketRef.current.on('user_typing', (data) => {
      setTypingUsers(prev => ({ ...prev, [data.conv_id]: data.is_typing ? data.user_id : null }));
      if (data.is_typing) setTimeout(() => setTypingUsers(prev => ({ ...prev, [data.conv_id]: null })), 3000);
    });
    socketRef.current.on('message_reaction', (data) => {
      setMessages(prev => prev.map(m => m.msg_id === data.msg_id ? { ...m, reactions: data.reactions } : m));
    });
    // Iter 59 — structured message updates (e.g. call_summary action items toggled)
    socketRef.current.on('message_updated', (data) => {
      if (!data?.msg_id) return;
      setMessages(prev => prev.map(m => m.msg_id === data.msg_id
        ? { ...m, structured_data: data.structured_data || m.structured_data }
        : m));
    });
    socketRef.current.on('messages_delivered', (data) => {
      // Flip my sent messages to delivered when the peer's client ACKed
      setMessages(prev => prev.map(m =>
        m.conv_id === data.conv_id && data.msg_ids?.includes(m.msg_id) && m.sender_id === user.user_id
          ? { ...m, status: 'delivered' } : m
      ));
    });
    socketRef.current.on('messages_seen', (data) => {
      // Peer opened the conversation — flip all my sent/delivered to 'seen'
      if (!data.conv_id || data.user_id === user.user_id) return;
      setMessages(prev => prev.map(m =>
        m.conv_id === data.conv_id && m.sender_id === user.user_id && m.status !== 'seen'
          ? { ...m, status: 'seen' } : m
      ));
    });
    socketRef.current.on('user_online', () => loadConversations());
    socketRef.current.on('user_offline', () => loadConversations());
    return () => { if (socketRef.current?.connected) socketRef.current.disconnect(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  useEffect(() => {
    if (activeConv) {
      loadMessages(activeConv.conv_id).then(() => {
        if (socketRef.current?.connected) {
          socketRef.current.emit('join_conversation', { conv_id: activeConv.conv_id });
          socketRef.current.emit('mark_seen', { conv_id: activeConv.conv_id });
        }
      });
      // reset per-conv UI states
      setReplyingTo(null);
      setMsgMenuId(null);
    }
  }, [activeConv, loadMessages]);

  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  // ACK delivery for freshly loaded incoming messages that are still 'sent'
  useEffect(() => {
    if (!activeConv || !socketRef.current?.connected || !user) return;
    const pending = messages.filter(m => m.conv_id === activeConv.conv_id && m.sender_id !== user.user_id && m.status === 'sent');
    if (pending.length > 0) {
      socketRef.current.emit('mark_delivered', {
        conv_id: activeConv.conv_id, msg_ids: pending.map(m => m.msg_id),
      });
    }
  }, [messages, activeConv, user]);

  const handleSend = async () => {
    if (!newMessage.trim() || !activeConv) return;
    const text = newMessage;
    const replyId = replyingTo?.msg_id || null;
    setNewMessage('');
    setReplyingTo(null);

    // iter237v — Optimistic UI + REST fallback. The previous flow relied
    // entirely on Socket.IO round-trip (`emit send_message` → wait for
    // `new_message`) to display the sent bubble. In production, the
    // ingress can hold/lose the WebSocket frame back to the sender, so
    // the message vanished from the composer without ever reappearing
    // in the thread. Fix:
    //   1. Insert a local `pending` bubble immediately with a unique
    //      `client_msg_id` so it shows up at the bottom of the list.
    //   2. Emit through Socket.IO when connected.
    //   3. After 3s, if the bubble is still pending (no `new_message`
    //      echo received), fall back to POST /conversations/:id/send so
    //      the message round-trips via plain HTTPS — the response then
    //      replaces the optimistic bubble.
    //   4. The dedup logic in the `new_message` listener already handles
    //      replacement via `client_msg_id` (see useEffect setup).
    const clientMsgId = `client_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const optimistic = {
      msg_id: clientMsgId,
      client_msg_id: clientMsgId,
      conv_id: activeConv.conv_id,
      sender_id: user.user_id,
      sender_name: `${user.first_name || ''} ${user.last_name || ''}`.trim(),
      sender_avatar: user.avatar || '',
      text,
      media: '',
      reply_to: replyId,
      reply_preview: replyingTo
        ? {
            msg_id: replyingTo.msg_id,
            text: (replyingTo.text || '').slice(0, 140),
            media: replyingTo.media || '',
            sender_id: replyingTo.sender_id,
            sender_name: replyingTo.sender_name || '',
          }
        : null,
      status: 'sending',
      pending: true,
      created_at: new Date().toISOString(),
    };
    setMessages(prev => [...prev, optimistic]);
    // Bump conv preview so the conversations list reflects the optimistic send.
    setConversations(prev => {
      const updated = prev.map(c =>
        c.conv_id === activeConv.conv_id
          ? { ...c, last_message: optimistic, updated_at: optimistic.created_at }
          : c
      );
      return updated.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
    });

    let serverEchoed = false;

    // Pass `client_msg_id` so backend can echo it (when it does, the
    // `new_message` listener replaces our optimistic bubble in-place).
    if (socketRef.current?.connected) {
      socketRef.current.emit('send_message', {
        conv_id: activeConv.conv_id, text, reply_to: replyId,
        client_msg_id: clientMsgId,
      });
    }

    // REST fallback. Trigger 3s after socket emit (or immediately if not
    // connected) — replace the optimistic bubble using `client_msg_id`
    // when the POST returns. If `new_message` came through faster, the
    // dedup makes the REST call a harmless no-op (the bubble was already
    // upgraded).
    const restFallback = async () => {
      if (serverEchoed) return;
      try {
        const { data } = await axios.post(
          `${API}/api/messages/conversations/${activeConv.conv_id}/send`,
          { text, reply_to: replyId, client_msg_id: clientMsgId },
          { withCredentials: true },
        );
        if (serverEchoed) return;
        setMessages(prev => {
          // Replace optimistic if still present, else dedup by msg_id.
          const idx = prev.findIndex(m => m.client_msg_id === clientMsgId || m.msg_id === clientMsgId);
          const real = { ...data, pending: false };
          if (idx === -1) {
            if (prev.some(m => m.msg_id === real.msg_id)) return prev;
            return [...prev, real];
          }
          const next = [...prev];
          next[idx] = real;
          return next;
        });
        serverEchoed = true;
      } catch (err) {
        // Network/auth failure → mark optimistic as failed so the user
        // can retry. We DON'T remove it (user types are too valuable).
        setMessages(prev => prev.map(m =>
          m.client_msg_id === clientMsgId
            ? { ...m, pending: false, status: 'failed' }
            : m,
        ));
        toast.error('Message non envoyé. Réessayez.');
      }
    };

    if (!socketRef.current?.connected) {
      // Disconnected → don't wait, just POST.
      restFallback();
    } else {
      // Connected → arm fallback.
      setTimeout(restFallback, 3000);
    }
  };

  const handleTyping = () => {
    if (socketRef.current?.connected && activeConv) {
      socketRef.current.emit('typing', { conv_id: activeConv.conv_id, is_typing: true });
      clearTimeout(typingTimeoutRef.current);
      typingTimeoutRef.current = setTimeout(() => {
        socketRef.current.emit('typing', { conv_id: activeConv.conv_id, is_typing: false });
      }, 2000);
    }
  };

  const handleSearch = async (q) => {
    setSearchQuery(q);
    if (q.length < 2) { setSearchResults([]); return; }
    try {
      const { data } = await axios.get(`${API}/api/users/search?q=${q}`, { withCredentials: true });
      setSearchResults(data);
    } catch {}
  };

  const startConversation = async (targetUser) => {
    try {
      const { data } = await axios.post(`${API}/api/messages/send`, { to_user_id: targetUser.user_id, text: 'Salut!' }, { withCredentials: true });
      setSearchQuery(''); setSearchResults([]);
      await loadConversations();
      setActiveConv({ conv_id: data.conv_id, participants: [targetUser] });
    } catch {}
  };

  const searchMembers = async (q) => {
    setMemberSearch(q);
    if (q.length < 2) { setMemberResults([]); return; }
    try {
      const { data } = await axios.get(`${API}/api/users/search?q=${q}`, { withCredentials: true });
      setMemberResults(data.filter(u => !groupMembers.find(m => m.user_id === u.user_id)));
    } catch {}
  };

  const createGroup = async () => {
    if (!groupName.trim() || groupMembers.length < 1) return;
    try {
      const { data } = await axios.post(`${API}/api/messages/groups`, {
        title: groupName, member_ids: groupMembers.map(m => m.user_id)
      }, { withCredentials: true });
      setShowCreateGroup(false); setGroupName(''); setGroupMembers([]);
      await loadConversations();
    } catch {}
  };

  const getOther = (conv) => conv.participants?.[0] || {};
  const isGroup = (conv) => conv.type === 'group';

  /** Parse `media` json payload for special message kinds (e.g., money). */
  const parseMedia = (media) => {
    if (!media || typeof media !== 'string') return null;
    if (!media.startsWith('{')) return null;
    try { return JSON.parse(media); } catch { return null; }
  };

  /**
   * iter202 — Détecte si `msg.media` est une URL d'image envoyée via /api/upload/.
   * Returns { url, isImage, isVideo } or null. Si l'URL n'a pas d'extension
   * (cas /api/upload/files/<uuid>), on assume image par défaut côté chat puisque
   * le file input n'accepte que image/video — la balise <img> tombera en
   * onError et basculera sur un fallback fichier si jamais c'est autre chose.
   */
  const getInlineMedia = (msg) => {
    const m = msg?.media;
    if (!m || typeof m !== 'string') return null;
    if (m.startsWith('{')) return null;  // JSON payload (voice/money)
    const lower = m.toLowerCase();
    const isImage = /\.(jpe?g|png|webp|gif|heic|heif|avif|bmp|tiff?)(\?|$)/i.test(lower)
                 || lower.includes('/api/upload/files/');
    const isVideo = /\.(mp4|mov|webm|avi|mkv|3gp)(\?|$)/i.test(lower);
    if (!isImage && !isVideo) return null;
    const url = m.startsWith('http') ? m : `${API}${m}`;
    return { url, isImage: !isVideo && isImage, isVideo };
  };

  /** Cache la mention "[Fichier: foo.jpg]" générée automatiquement */
  const stripFilePlaceholder = (text) => {
    if (!text) return '';
    const stripped = text.replace(/\[Fichier:\s*[^\]]+\]/g, '').trim();
    return stripped;
  };

  const openMoneyModal = () => {
    setMoneyAmount('');
    setMoneyNote('');
    setMoneyError('');
    setShowMoneyModal(true);
  };

  const insertEmojiInMessage = (emoji) => {
    setNewMessage(prev => prev + emoji);
    setShowEmojiPicker(false);
  };

  const onVoiceSent = (data) => {
    if (data?.message) {
      setMessages(prev => prev.some(m => m.msg_id === data.message.msg_id) ? prev : [...prev, data.message]);
      setConversations(prev => prev.map(c =>
        c.conv_id === data.conv_id ? { ...c, last_message: data.message, updated_at: data.message.created_at } : c
      ).sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at)));
    }
  };

  const toggleReaction = async (msgId, emoji) => {
    try {
      const { data } = await axios.post(`${API}/api/messages/${msgId}/react`, { emoji }, { withCredentials: true });
      // Optimistic — socket echo from backend will also arrive
      setMessages(prev => prev.map(m => m.msg_id === msgId ? { ...m, reactions: data.reactions } : m));
    } catch (e) {
      // Silent fail (e.g., invalid emoji)
    }
    setReactingMsgId(null);
  };

  // Long-press (mobile) + right-click (desktop) → open message actions menu
  const handleLongPressStart = (msgId) => {
    longPressTimerRef.current = setTimeout(() => setMsgMenuId(msgId), 450);
  };
  const handleLongPressCancel = () => {
    clearTimeout(longPressTimerRef.current);
  };

  const handleStartReply = (msg) => {
    setReplyingTo({
      msg_id: msg.msg_id,
      text: msg.text || '',
      sender_name: msg.sender_name || '',
      sender_id: msg.sender_id,
    });
    setMsgMenuId(null);
  };

  const handleStartForward = (msg) => {
    setForwardingMsg(msg);
    setMsgMenuId(null);
  };

  // Navigate to the original conversation of a forwarded message and scroll to
  // the source message. Falls back to a toast if the viewer is not a
  // participant of the source conv.
  const handleViewForwardSource = async (msg) => {
    if (!msg?.forwarded_from_conv_id || !msg?.forwarded_from_msg_id) return;
    if (!msg.can_view_source) {
      toast.info("Message original non accessible (vous n'êtes pas membre de la conversation source).");
      return;
    }
    // Find the source conv in our conversations list; load it if not cached
    let srcConv = conversations.find(c => c.conv_id === msg.forwarded_from_conv_id);
    if (!srcConv) {
      try {
        await loadConversations();
        srcConv = conversations.find(c => c.conv_id === msg.forwarded_from_conv_id)
          || { conv_id: msg.forwarded_from_conv_id, participants: [] };
      } catch { /* ignore */ }
    }
    setActiveConv(srcConv);
    // Wait for messages to load, then scroll to the source message
    const targetId = msg.forwarded_from_msg_id;
    setTimeout(() => {
      const el = document.querySelector(
        `[data-testid="message-${targetId}"], [data-testid="message-voice-${targetId}"], [data-testid="message-money-${targetId}"]`
      );
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        // Brief highlight
        el.style.transition = 'background 0.8s ease';
        el.style.background = 'rgba(224,28,46,0.12)';
        setTimeout(() => { el.style.background = 'transparent'; }, 1800);
      } else {
        toast.info('Message original introuvable — il a peut-être été supprimé.');
      }
    }, 800);
  };

  // Visual tick for a message I sent : ✓ sent · ✓✓ delivered · 👁 seen.
  const renderTicks = (msg) => {
    if (msg.sender_id !== user?.user_id) return null;
    const iconStyle = { marginLeft: 4, verticalAlign: 'middle' };
    // iter237v — Optimistic UI states.
    if (msg.status === 'sending' || msg.pending) {
      return (
        <Clock size={12} weight="bold" style={{ ...iconStyle, opacity: 0.55 }}
               data-testid={`tick-sending-${msg.msg_id}`} />
      );
    }
    if (msg.status === 'failed') {
      return (
        <span style={{ ...iconStyle, color: '#E01C2E', fontSize: 11, fontWeight: 700 }}
              data-testid={`tick-failed-${msg.msg_id}`}
              title="Échec — touche pour réessayer">!</span>
      );
    }
    if (msg.status === 'seen') {
      return <Eye size={12} weight="bold" style={{ ...iconStyle, color: '#4FC3F7' }} data-testid={`tick-seen-${msg.msg_id}`} />;
    }
    if (msg.status === 'delivered') {
      return <Checks size={13} weight="bold" style={{ ...iconStyle, opacity: 0.85 }} data-testid={`tick-delivered-${msg.msg_id}`} />;
    }
    // sent (default)
    return <Check size={12} weight="bold" style={{ ...iconStyle, opacity: 0.7 }} data-testid={`tick-sent-${msg.msg_id}`} />;
  };

  const handleSendMoney = async () => {
    setMoneyError('');
    const other = getOther(activeConv);
    if (!other?.user_id) { setMoneyError('Destinataire introuvable'); return; }
    const amt = parseFloat(moneyAmount);
    // iter237m — Wallets are USD canonical. Min = 0.10 USD.
    if (!amt || amt < 0.10) { setMoneyError('Montant minimum 0.10 USD'); return; }
    setMoneySending(true);
    try {
      const { data } = await axios.post(`${API}/api/messages/send-money`, {
        to_user_id: other.user_id,
        amount: amt,
        note: moneyNote.trim(),
      }, { withCredentials: true });
      // Optimistic append — socket event will be deduped by msg_id
      if (data?.message) {
        setMessages(prev => prev.some(m => m.msg_id === data.message.msg_id) ? prev : [...prev, data.message]);
        setConversations(prev => prev.map(c =>
          c.conv_id === data.conv_id ? { ...c, last_message: data.message, updated_at: data.message.created_at } : c
        ).sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at)));
      }
      const txCcy = data.currency || 'USD';
      toast.success(`💸 ${data.amount} ${txCcy} envoyé`, {
        description: `Nouveau solde: ${parseFloat(data.new_balance).toLocaleString('fr-FR')} ${txCcy}`,
      });
      setShowMoneyModal(false);
    } catch (e) {
      setMoneyError(e?.response?.data?.detail || 'Erreur lors de l\'envoi');
    } finally {
      setMoneySending(false);
    }
  };

  return (
    <div className="flex h-full overflow-hidden" style={{ background: 'var(--jp-bg)' }} data-testid="chat-page"
      onClick={() => { if (reactingMsgId) setReactingMsgId(null); if (showEmojiPicker) setShowEmojiPicker(false); if (msgMenuId) setMsgMenuId(null); if (showAttachMenu) setShowAttachMenu(false); }}>
      {/* ============ CONVERSATION LIST (mobile: full-width when no conv selected; hidden when a conv is open) ============ */}
      <div className={`${activeConv ? 'hidden md:flex' : 'flex'} flex-col w-full md:w-80 lg:w-96 md:border-r`}
        style={{ background: 'var(--jp-surface)', borderColor: 'var(--jp-border)' }}>
        <div className="p-4 border-b" style={{ borderColor: 'var(--jp-border)' }}>
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-['Outfit'] text-xl font-bold" style={{ color: 'var(--jp-text)' }}>Messages</h2>
            <button onClick={() => setShowCreateGroup(!showCreateGroup)} data-testid="create-group-btn"
              className="jp-btn jp-btn-sm jp-btn-soft" style={{ padding: '6px 10px' }}>
              <UsersThree size={14} /> <Plus size={10} />
            </button>
          </div>
          <div className="relative">
            <MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2" size={18} style={{ color: 'var(--jp-text-muted)' }} />
            <input data-testid="chat-search-input" value={searchQuery} onChange={e => handleSearch(e.target.value)}
              className="jp-input jp-input-with-icon text-sm w-full" style={{ paddingLeft: '40px' }}
              placeholder={t('chat.rechercher_un_utilisateur')} />
          </div>
        </div>

        {searchResults.length > 0 && (
          <div className="border-b jp-animate-fadeIn" style={{ borderColor: 'var(--jp-border)' }}>
            {searchResults.map(u => (
              <button key={u.user_id} onClick={() => startConversation(u)} data-testid={`search-result-${u.user_id}`}
                className="w-full flex items-center gap-3 px-4 py-3 transition-colors"
                style={{ ':hover': { background: 'var(--jp-surface-secondary)' } }}
                onMouseEnter={e => e.currentTarget.style.background = 'var(--jp-surface-secondary)'}
                onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                <div className="jp-avatar jp-avatar-md jp-avatar-primary relative">
                  {(u.first_name?.[0] || '').toUpperCase()}
                  {u.is_online && <span className="jp-online-dot" />}
                </div>
                <div className="text-left">
                  <p className="text-sm font-medium font-['Manrope']" style={{ color: 'var(--jp-text)' }}>{u.first_name} {u.last_name}</p>
                  <p className="text-xs" style={{ color: 'var(--jp-text-muted)' }}>@{u.username}</p>
                </div>
              </button>
            ))}
          </div>
        )}

        {/* Create Group Panel */}
        {showCreateGroup && (
          <div className="p-4 border-b jp-animate-fadeIn" style={{ borderColor: 'var(--jp-border)', background: 'var(--jp-surface-secondary)' }} data-testid="create-group-panel">
            <input value={groupName} onChange={e => setGroupName(e.target.value)} data-testid="group-name-input"
              className="jp-input text-sm mb-2" placeholder="Nom du groupe" />
            <div className="relative mb-2">
              <input value={memberSearch} onChange={e => searchMembers(e.target.value)} data-testid="group-member-search"
                className="jp-input text-sm" placeholder="Ajouter des membres..." />
            </div>
            {memberResults.length > 0 && (
              <div className="mb-2 border rounded-lg overflow-hidden" style={{ borderColor: 'var(--jp-border)' }}>
                {memberResults.slice(0, 4).map(u => (
                  <button key={u.user_id} onClick={() => { setGroupMembers(prev => [...prev, u]); setMemberResults([]); setMemberSearch(''); }}
                    className="w-full flex items-center gap-2 px-3 py-2 text-left text-xs font-['Manrope']"
                    style={{ background: 'var(--jp-surface)' }}>
                    <div className="jp-avatar jp-avatar-sm jp-avatar-primary" style={{ width: '24px', height: '24px', fontSize: '9px' }}>{u.first_name?.[0]}</div>
                    {u.first_name} {u.last_name}
                  </button>
                ))}
              </div>
            )}
            {groupMembers.length > 0 && (
              <div className="flex flex-wrap gap-1 mb-2">
                {groupMembers.map(m => (
                  <span key={m.user_id} className="jp-badge jp-badge-primary text-[9px] flex items-center gap-1">
                    {m.first_name}
                    <button onClick={() => setGroupMembers(prev => prev.filter(x => x.user_id !== m.user_id))} className="ml-0.5">x</button>
                  </span>
                ))}
              </div>
            )}
            <button onClick={createGroup} disabled={!groupName.trim() || groupMembers.length < 1} data-testid="confirm-create-group"
              className="jp-btn jp-btn-primary jp-btn-sm jp-btn-full">Creer le groupe</button>
          </div>
        )}

        <div className="flex-1 overflow-y-auto jp-scrollbar">
          {conversations.map(conv => {
            const other = getOther(conv);
            const isActive = activeConv?.conv_id === conv.conv_id;
            const group = isGroup(conv);
            const displayName = group ? (conv.title || 'Groupe') : `${other.first_name || ''} ${other.last_name || ''}`.trim();
            const initial = group ? conv.title?.[0]?.toUpperCase() || 'G' : (other.first_name?.[0] || '?').toUpperCase();
            return (
              <button key={conv.conv_id} onClick={() => setActiveConv(conv)} data-testid={`conversation-${conv.conv_id}`}
                className="w-full flex items-center gap-3 px-4 py-3.5 border-b transition-all"
                style={{
                  borderColor: 'rgba(229,228,226,0.5)',
                  background: isActive ? 'var(--jp-primary-subtle)' : 'transparent',
                  borderLeft: isActive ? '3px solid var(--jp-primary)' : '3px solid transparent',
                }}
                onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'var(--jp-surface-secondary)'; }}
                onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = isActive ? 'var(--jp-primary-subtle)' : 'transparent'; }}>
                <div className={`jp-avatar jp-avatar-lg relative flex-shrink-0 ${group ? 'jp-avatar-secondary' : 'jp-avatar-primary'}`}>
                  {initial}
                  {!group && other.is_online && <span className="jp-online-dot jp-online-dot-lg" />}
                </div>
                <div className="flex-1 min-w-0 text-left">
                  <div className="flex justify-between items-center">
                    <div className="flex items-center gap-1.5">
                      <p className="text-sm font-semibold font-['Manrope'] truncate" style={{ color: 'var(--jp-text)' }}>{displayName}</p>
                      {group && <UsersThree size={12} style={{ color: 'var(--jp-text-muted)' }} />}
                    </div>
                    {conv.last_message && (
                      <span className="text-xs flex-shrink-0 ml-2" style={{ color: 'var(--jp-text-muted)' }}>
                        {new Date(conv.last_message.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </span>
                    )}
                  </div>
                  <p className="text-xs truncate mt-0.5" style={{ color: 'var(--jp-text-secondary)' }}>
                    {conv.last_message?.text || 'Nouvelle conversation'}
                  </p>
                </div>
                {conv.unread_count > 0 && (
                  <span className="w-5 h-5 text-[10px] font-bold rounded-full flex items-center justify-center flex-shrink-0"
                    style={{ background: 'var(--jp-secondary)', color: 'white' }}>
                    {conv.unread_count}
                  </span>
                )}
              </button>
            );
          })}
          {conversations.length === 0 && !searchQuery && (
            <div className="flex flex-col items-center justify-center px-6 py-10 font-['Manrope']" data-testid="conversations-empty-state">
              <div className="w-16 h-16 rounded-full flex items-center justify-center mb-4"
                style={{ background: 'var(--jp-primary-subtle)' }}>
                <PaperPlaneRight size={28} style={{ color: 'var(--jp-primary)' }} weight="fill" />
              </div>
              <p className="text-sm font-semibold text-center mb-1" style={{ color: 'var(--jp-text)' }}>
                Start a conversation or call someone now
              </p>
              <p className="text-xs text-center mb-5" style={{ color: 'var(--jp-text-muted)' }}>
                Trouvez vos amis, démarrez un chat ou un appel.
              </p>
              <div className="flex flex-col gap-2 w-full">
                <button
                  type="button"
                  data-testid="empty-state-new-chat"
                  onClick={() => { document.querySelector('[data-testid="chat-search-input"]')?.focus(); }}
                  className="w-full py-2.5 rounded-xl text-sm font-['Outfit'] font-semibold flex items-center justify-center gap-2 transition-all"
                  style={{ background: 'linear-gradient(135deg,#0F056B 0%,#2A1B9B 100%)', color: 'white' }}>
                  <Plus size={16} weight="bold" /> Nouveau chat
                </button>
                <button
                  type="button"
                  data-testid="empty-state-call-contact"
                  onClick={() => { document.querySelector('[data-testid="chat-search-input"]')?.focus(); toast.info("Recherchez un contact pour démarrer un appel"); }}
                  className="w-full py-2.5 rounded-xl text-sm font-['Outfit'] font-semibold flex items-center justify-center gap-2 transition-all"
                  style={{ background: 'var(--jp-surface-secondary)', color: 'var(--jp-text)' }}>
                  <Phone size={16} weight="bold" /> Appeler un contact
                </button>
                <button
                  type="button"
                  data-testid="empty-state-create-group"
                  onClick={() => setShowCreateGroup(true)}
                  className="w-full py-2.5 rounded-xl text-sm font-['Outfit'] font-semibold flex items-center justify-center gap-2 transition-all"
                  style={{ background: 'var(--jp-surface-secondary)', color: 'var(--jp-text)' }}>
                  <UsersThree size={16} weight="bold" /> Créer un groupe
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Chat Window */}
      <div className={`${!activeConv ? 'hidden md:flex' : 'flex'} flex-1 flex-col`}>
        {activeConv ? (
          <>
            {/* Chat Header */}
            <div className="flex items-center gap-2 sm:gap-3 px-3 sm:px-6 py-3 sm:py-4 border-b"
              style={{ background: 'var(--jp-surface)', borderColor: 'var(--jp-border)' }}>
              <button className="md:hidden p-1" onClick={() => setActiveConv(null)} data-testid="back-to-conversations"
                style={{ color: 'var(--jp-text-secondary)' }}>
                <CaretLeft size={22} weight="bold" />
              </button>
              <div className="jp-avatar jp-avatar-md jp-avatar-primary relative flex-shrink-0">
                {(getOther(activeConv).first_name?.[0] || '?').toUpperCase()}
                {getOther(activeConv).is_online && <span className="jp-online-dot" />}
              </div>
              <div className="flex-1 min-w-0">
                <p className="font-['Manrope'] font-semibold text-sm truncate" style={{ color: 'var(--jp-text)' }}>
                  {getOther(activeConv).first_name} {getOther(activeConv).last_name}
                </p>
                <p className="text-xs truncate flex items-center gap-1.5"
                   style={{ color: typingUsers[activeConv.conv_id] ? 'var(--jp-secondary)' : 'var(--jp-text-muted)' }}
                   data-testid="conv-presence">
                  {typingUsers[activeConv.conv_id] ? (
                    <>
                      <span className="jp-typing-dots" data-testid="typing-dots">
                        <span /><span /><span />
                      </span>
                      <span>en train d'écrire</span>
                    </>
                  ) : (
                    getOther(activeConv).is_online
                      ? 'En ligne'
                      : formatLastSeen(getOther(activeConv).last_seen)
                  )}
                </p>
              </div>
              <div className="flex items-center gap-1">
                {activeConv.type !== 'group' && (() => {
                  const other = getOther(activeConv);
                  const peer = {
                    user_id: other.user_id,
                    name: `${other.first_name || ''} ${other.last_name || ''}`.trim() || other.username || 'Contact',
                    avatar: other.avatar || '',
                  };
                  if (!peer.user_id) return null;
                  return (
                    <>
                      <button onClick={() => startCall({ ...peer, type: 'audio', conv_id: activeConv.conv_id })} data-testid="call-audio-btn"
                        className="p-2 rounded-lg transition-colors" style={{ color: 'var(--jp-text-secondary)' }}
                        onMouseEnter={e => e.currentTarget.style.background = 'var(--jp-surface-secondary)'}
                        onMouseLeave={e => e.currentTarget.style.background = 'transparent'} aria-label={t('chat.appel_audio')}>
                        <Phone size={20} weight="bold" />
                      </button>
                      <button onClick={() => startCall({ ...peer, type: 'video', conv_id: activeConv.conv_id })} data-testid="call-video-btn"
                        className="p-2 rounded-lg transition-colors" style={{ color: 'var(--jp-text-secondary)' }}
                        onMouseEnter={e => e.currentTarget.style.background = 'var(--jp-surface-secondary)'}
                        onMouseLeave={e => e.currentTarget.style.background = 'transparent'} aria-label={t('chat.appel_video')}>
                        <VideoCamera size={20} weight="bold" />
                      </button>
                    </>
                  );
                })()}
                {activeConv.type === 'group' && (
                  <>
                    <button
                      onClick={() => startGroupCall({ conv_id: activeConv.conv_id, title: activeConv.title, mode: 'audio' })}
                      data-testid="group-call-audio-btn"
                      className="p-2 rounded-lg transition-colors"
                      style={{ color: 'var(--jp-text-secondary)' }}
                      aria-label={t('chat.appel_de_groupe_audio')}>
                      <Phone size={20} weight="bold" />
                    </button>
                    <button
                      onClick={() => startGroupCall({ conv_id: activeConv.conv_id, title: activeConv.title, mode: 'video' })}
                      data-testid="group-call-video-btn"
                      className="p-2 rounded-lg transition-colors"
                      style={{ color: 'var(--jp-text-secondary)' }}
                      aria-label={t('chat.appel_de_groupe_video')}>
                      <VideoCamera size={20} weight="bold" />
                    </button>
                  </>
                )}
                <button className="p-2 rounded-lg transition-colors" data-testid="chat-options"
                  style={{ color: 'var(--jp-text-secondary)' }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--jp-surface-secondary)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                  <DotsThreeVertical size={20} weight="bold" />
                </button>
              </div>
            </div>

            {/* Sprint C — Join live group call banner */}
            {activeConv?.type === 'group' && liveGroupCalls?.[activeConv.conv_id] && callState === 'idle' && (
              <div
                data-testid={`group-call-live-banner-${activeConv.conv_id}`}
                className="px-4 py-3 flex items-center gap-3 border-b"
                style={{
                  background: 'linear-gradient(90deg, rgba(22,163,74,0.12) 0%, rgba(16,185,129,0.12) 100%)',
                  borderColor: 'rgba(22,163,74,0.3)',
                }}>
                <span className="relative flex h-2.5 w-2.5 shrink-0" aria-hidden="true">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ background: '#16A34A' }} />
                  <span className="relative inline-flex rounded-full h-2.5 w-2.5" style={{ background: '#16A34A' }} />
                </span>
                <div className="flex-1 min-w-0">
                  <p className="font-['Outfit'] font-semibold text-sm truncate" style={{ color: '#065F46' }}>
                    Appel de groupe en cours
                  </p>
                  <p className="font-['Manrope'] text-xs truncate opacity-80" style={{ color: '#065F46' }}>
                    {liveGroupCalls[activeConv.conv_id].host_name ? `${liveGroupCalls[activeConv.conv_id].host_name} · ` : ''}
                    {liveGroupCalls[activeConv.conv_id].mode === 'video' ? t('chat.video') : 'Audio'}
                  </p>
                </div>
                <button
                  data-testid={`join-group-call-btn-${activeConv.conv_id}`}
                  onClick={() => joinGroupCall({
                    conv_id: activeConv.conv_id,
                    session_id: liveGroupCalls[activeConv.conv_id].session_id,
                    mode: liveGroupCalls[activeConv.conv_id].mode,
                    title: activeConv.title,
                  })}
                  className="px-4 py-2 rounded-full font-['Manrope'] font-semibold text-xs text-white shrink-0 transition-transform active:scale-95"
                  style={{ background: 'linear-gradient(135deg, #16A34A 0%, #059669 100%)' }}>
                  Rejoindre
                </button>
              </div>
            )}

            {/* Messages */}
            <div className="flex-1 overflow-y-auto overflow-x-hidden px-3 sm:px-6 py-4 space-y-3 jp-scrollbar" data-testid="messages-container">
              {messages.map((msg) => {
                const isMine = msg.sender_id === user?.user_id;
                const meta = parseMedia(msg.media);
                const isMoney = meta?.kind === 'money';
                const isVoice = meta?.kind === 'voice';
                const isCallSummary = msg.message_type === 'call_summary';
                const reactions = msg.reactions || [];
                const showReactBar = reactingMsgId === msg.msg_id;

                let bubble;
                if (isCallSummary) {
                  bubble = (
                    <CallSummaryMessageBubble
                      message={msg}
                      onPatched={(msgId, nextSd) => {
                        setMessages((prev) => prev.map((x) =>
                          x.msg_id === msgId ? { ...x, structured_data: nextSd } : x,
                        ));
                      }}
                    />
                  );
                } else if (isMoney) {
                  bubble = (
                    <div className="rounded-2xl px-4 py-3 max-w-full shadow-md"
                      style={{
                        background: isMine
                          ? 'linear-gradient(135deg, #0F056B 0%, #2A1B9B 100%)'
                          : 'linear-gradient(135deg, #E01C2E 0%, #FF5757 100%)',
                        color: 'white',
                      }}>
                      <div className="flex items-center gap-2 mb-1 opacity-80">
                        <CurrencyDollar size={16} weight="fill" />
                        <span className="text-[10px] font-['Manrope'] font-semibold uppercase tracking-wide">
                          {isMine ? t('chat.envoye') : t('chat.recu')}
                        </span>
                      </div>
                      <div className="font-['Outfit'] font-extrabold text-2xl leading-tight">
                        {/* iter237m — Honor stored currency (legacy XAF preserved as-is, new = USD) */}
                        {parseFloat(meta.amount || 0).toLocaleString('fr-FR')} {meta.currency || 'USD'}
                      </div>
                      {meta.note && (
                        <p className="text-xs font-['Manrope'] mt-1.5 italic opacity-90 break-words">"{meta.note}"</p>
                      )}
                      <p className="text-[10px] font-['Manrope'] mt-1 opacity-70 flex items-center justify-end gap-0.5">
                        <span>{new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                        {renderTicks(msg)}
                      </p>
                    </div>
                  );
                } else if (isVoice) {
                  bubble = (
                    <div>
                      <VoicePlayer media={meta} isMine={isMine} msgId={msg.msg_id}
                        targetLang={user?.preferred_lang || 'en'} />
                      {!isMine && (meta.transcription || '').trim() && (
                        <div className="px-1">
                          {user?.preferred_lang ? (
                            <TranslateButton msgId={msg.msg_id} tone="light"
                              targetLang={user.preferred_lang} autoMode />
                          ) : (
                            <TranslateButton msgId={msg.msg_id} tone="light" />
                          )}
                        </div>
                      )}
                    </div>
                  );
                } else {
                  const highlyForwarded = (msg.forward_depth || 0) >= 5;
                  bubble = (
                    <div
                      className={isMine ? 'jp-bubble-sent' : 'jp-bubble-received'}
                      style={highlyForwarded ? {
                        // Grise la bulle comme WhatsApp pour les messages viraux
                        opacity: 0.82,
                        filter: 'grayscale(0.5)',
                      } : undefined}>
                      {/* Highly-forwarded warning (WhatsApp-style, reduced misinformation by ~70%) */}
                      {highlyForwarded && (
                        <div
                          data-testid={`highly-forwarded-warning-${msg.msg_id}`}
                          className="flex items-start gap-1.5 mb-2 px-2 py-1.5 rounded-lg text-[10px] font-['Manrope'] leading-snug"
                          style={{
                            background: isMine ? 'rgba(255,255,255,0.15)' : 'rgba(180,83,9,0.12)',
                            color: isMine ? 'rgba(255,255,255,0.95)' : '#92400E',
                            border: isMine ? '1px dashed rgba(255,255,255,0.35)' : '1px dashed rgba(180,83,9,0.35)',
                          }}>
                          <span aria-hidden="true">⚠️</span>
                          <span>
                            <strong>{t('chat.transfere_plusieurs_fois')}</strong> — vérifiez la source avant de partager.
                          </span>
                        </div>
                      )}
                      {/* Quote preview (reply_to) */}
                      {msg.reply_preview && (
                        <div
                          className="border-l-2 pl-2 pr-2 py-1 mb-1 rounded-sm cursor-pointer"
                          data-testid={`quote-preview-${msg.msg_id}`}
                          style={{
                            background: isMine ? 'rgba(255,255,255,0.12)' : 'rgba(15,5,107,0.05)',
                            borderColor: isMine ? 'rgba(255,255,255,0.6)' : '#0F056B',
                          }}
                          onClick={(e) => {
                            e.stopPropagation();
                            const el = document.querySelector(`[data-testid="message-${msg.reply_preview.msg_id}"], [data-testid="message-voice-${msg.reply_preview.msg_id}"], [data-testid="message-money-${msg.reply_preview.msg_id}"]`);
                            el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
                          }}>
                          <p className="text-[10px] font-['Manrope'] font-bold opacity-80">
                            {msg.reply_preview.sender_name || 'Message'}
                          </p>
                          <p className="text-xs font-['Manrope'] opacity-75 line-clamp-2">
                            {msg.reply_preview.text || '[média]'}
                          </p>
                        </div>
                      )}
                      {/* Forwarded badge — shows forward depth when ≥2 and opens the full chain modal */}
                      {msg.is_forwarded && (() => {
                        const depth = msg.forward_depth || 0;
                        const manyHops = depth >= 2;
                        const onClickBadge = (e) => {
                          e.stopPropagation();
                          if (manyHops) {
                            setChainForMsgId(msg.msg_id);
                          } else {
                            handleViewForwardSource(msg);
                          }
                        };
                        return (
                          <button
                            type="button"
                            disabled={!msg.forwarded_from_msg_id}
                            onClick={onClickBadge}
                            data-testid={`forwarded-badge-${msg.msg_id}`}
                            className="text-[10px] font-['Manrope'] italic opacity-70 mb-0.5 flex items-center gap-1 hover:opacity-100 transition-opacity"
                            title={
                              manyHops
                                ? `Transféré ${depth}× — voir la chaîne complète`
                                : (msg.can_view_source ? 'Voir dans la conversation' : t('chat.transfere'))
                            }
                            style={{
                              color: 'inherit',
                              cursor: msg.forwarded_from_msg_id ? 'pointer' : 'default',
                              textDecoration: (manyHops || msg.can_view_source) ? 'underline dotted' : 'none',
                            }}>
                            <ArrowBendUpRight size={11} />
                            Transféré
                            {manyHops
                              ? <span data-testid={`forward-depth-${msg.msg_id}`}> ×{depth} · Chaîne</span>
                              : (msg.can_view_source ? <span> · Voir</span> : null)
                            }
                          </button>
                        );
                      })()}
                      {/* iter202 — Inline media: image / video uploaded via
                          /api/upload/. msg.media is a plain URL string (not a
                          JSON payload like voice/money). Render <ZoomableImage>
                          for images, native <video> for videos. The auto
                          "[Fichier: foo.jpg]" placeholder is stripped from the
                          visible body. */}
                      {(() => {
                        const inline = getInlineMedia(msg);
                        if (!inline) return null;
                        return (
                          <div className="mb-2 -mx-2 sm:-mx-3 rounded-xl overflow-hidden" data-testid={`chat-inline-media-${msg.msg_id}`}>
                            {inline.isVideo ? (
                              <video src={inline.url} controls className="w-full"
                                style={{ maxHeight: '60vh', background: '#000' }} />
                            ) : (
                              <ZoomableImage src={inline.url} alt="" maxHeight="60vh"
                                testId={`chat-image-${msg.msg_id}`} />
                            )}
                          </div>
                        );
                      })()}
                      {/* iter192 — Smart Product Card: when a message
                          contains a JAPAP marketplace product link, render
                          the visual card instead of the raw link. Keeps
                          any non-link text above as regular body. */}
                      {(() => {
                        const inline = getInlineMedia(msg);
                        const cleanText = inline ? stripFilePlaceholder(msg.text) : msg.text;
                        // If only "[Fichier: foo.jpg]" was the body and we already
                        // rendered the image, hide the empty <p>.
                        if (inline && !cleanText) return null;
                        const linked = extractProductLink(cleanText);
                        if (!linked) {
                          return (
                            <p className="text-sm font-['Manrope'] leading-relaxed break-words">
                              {cleanText}
                            </p>
                          );
                        }
                        return (
                          <div className="space-y-2">
                            {linked.before && (
                              <p className="text-sm font-['Manrope'] leading-relaxed break-words">
                                {linked.before}
                              </p>
                            )}
                            <SmartProductCard
                              productId={linked.productId}
                              fallbackText={cleanText}
                              isMine={isMine}
                            />
                            {linked.after && (
                              <p className="text-sm font-['Manrope'] leading-relaxed break-words">
                                {linked.after}
                              </p>
                            )}
                          </div>
                        );
                      })()}
                      {!isMine && (msg.text || '').trim().length > 1 && (
                        user?.preferred_lang ? (
                          <TranslateButton msgId={msg.msg_id} tone="light"
                            targetLang={user.preferred_lang} autoMode />
                        ) : (
                          <TranslateButton msgId={msg.msg_id} tone="light" />
                        )
                      )}
                      <p className="jp-bubble-time flex items-center justify-end gap-0.5">
                        <span>{new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                        {renderTicks(msg)}
                      </p>
                    </div>
                  );
                }

                return (
                  <div key={msg.msg_id}
                    data-testid={isMoney ? `message-money-${msg.msg_id}` : (isVoice ? `message-voice-${msg.msg_id}` : `message-${msg.msg_id}`)}
                    className={`group flex ${isMine ? 'justify-end' : 'justify-start'} jp-msg-enter ${msg.pending ? 'jp-msg-pending' : ''} ${msg.status === 'failed' ? 'jp-msg-failed' : ''}`}
                    onContextMenu={(e) => { e.preventDefault(); setMsgMenuId(msgMenuId === msg.msg_id ? null : msg.msg_id); }}
                    onTouchStart={() => handleLongPressStart(msg.msg_id)}
                    onTouchEnd={handleLongPressCancel}
                    onTouchMove={handleLongPressCancel}
                  >
                    <div className={`relative max-w-[85%] sm:max-w-[70%] ${isMine ? 'items-end' : 'items-start'} flex flex-col`}>
                      {/* React trigger — appears on hover (desktop) */}
                      <button type="button" onClick={(e) => { e.stopPropagation(); setReactingMsgId(showReactBar ? null : msg.msg_id); }}
                        data-testid={`react-trigger-${msg.msg_id}`}
                        className={`absolute top-1 ${isMine ? '-left-8' : '-right-8'} opacity-0 group-hover:opacity-100 transition-opacity w-7 h-7 rounded-full hidden md:flex items-center justify-center shadow-md z-10`}
                        style={{ background: 'var(--jp-surface)', border: '1px solid var(--jp-border)', color: 'var(--jp-text-muted)' }}
                        aria-label={t('chat.reagir')}>
                        <Smiley size={16} weight="bold" />
                      </button>
                      {/* Message context menu (Reply / Forward / React) — shown on long-press or right-click */}
                      {msgMenuId === msg.msg_id && (
                        <div
                          data-testid={`message-menu-${msg.msg_id}`}
                          onClick={(e) => e.stopPropagation()}
                          className={`absolute -top-3 ${isMine ? 'right-2' : 'left-2'} z-30 rounded-xl shadow-xl border flex items-center gap-0.5 px-1.5 py-1 jp-animate-fadeIn`}
                          style={{ background: 'var(--jp-surface)', borderColor: 'var(--jp-border)' }}>
                          <button
                            type="button"
                            onClick={() => handleStartReply(msg)}
                            data-testid={`menu-reply-${msg.msg_id}`}
                            className="w-8 h-8 rounded-full flex items-center justify-center transition-colors"
                            style={{ color: 'var(--jp-text)' }}
                            title={t('chat.repondre')}>
                            <ArrowBendUpLeft size={16} weight="bold" />
                          </button>
                          <button
                            type="button"
                            onClick={() => handleStartForward(msg)}
                            data-testid={`menu-forward-${msg.msg_id}`}
                            className="w-8 h-8 rounded-full flex items-center justify-center transition-colors"
                            style={{ color: 'var(--jp-text)' }}
                            title={t('chat.transferer')}>
                            <ArrowBendUpRight size={16} weight="bold" />
                          </button>
                          <button
                            type="button"
                            onClick={() => { setReactingMsgId(msg.msg_id); setMsgMenuId(null); }}
                            data-testid={`menu-react-${msg.msg_id}`}
                            className="w-8 h-8 rounded-full flex items-center justify-center transition-colors"
                            style={{ color: 'var(--jp-text)' }}
                            title={t('chat.reagir')}>
                            <Smiley size={16} weight="bold" />
                          </button>
                        </div>
                      )}

                      {/* Quick reaction bar */}
                      {showReactBar && (
                        <div data-testid={`quick-reactions-${msg.msg_id}`}
                          onClick={e => e.stopPropagation()}
                          className={`absolute bottom-full mb-2 ${isMine ? 'right-0' : 'left-0'} z-20 rounded-full shadow-xl border px-2 py-1 flex items-center gap-0.5 jp-animate-fadeIn`}
                          style={{ background: 'var(--jp-surface)', borderColor: 'var(--jp-border)' }}>
                          {QUICK_REACTIONS.map(em => (
                            <button key={em} type="button" onClick={(e) => { e.stopPropagation(); toggleReaction(msg.msg_id, em); }}
                              data-testid={`quick-react-${msg.msg_id}-${em}`}
                              className="w-9 h-9 rounded-full text-xl flex items-center justify-center transition-transform hover:scale-125">
                              {em}
                            </button>
                          ))}
                        </div>
                      )}

                      {bubble}

                      {/* Reaction chips */}
                      {reactions.length > 0 && (
                        <div data-testid={`reactions-${msg.msg_id}`}
                          className={`flex flex-wrap gap-1 mt-1 ${isMine ? 'justify-end' : 'justify-start'}`}>
                          {reactions.map(r => (
                            <button key={r.emoji} type="button" onClick={(e) => { e.stopPropagation(); toggleReaction(msg.msg_id, r.emoji); }}
                              data-testid={`reaction-chip-${msg.msg_id}-${r.emoji}`}
                              className="flex items-center gap-1 px-2 py-0.5 rounded-full text-xs transition-all hover:scale-105"
                              style={{
                                background: r.mine ? 'var(--jp-primary-subtle)' : 'var(--jp-surface-secondary)',
                                border: r.mine ? '1px solid var(--jp-primary)' : '1px solid var(--jp-border)',
                                color: 'var(--jp-text)',
                              }}>
                              <span className="text-sm leading-none">{r.emoji}</span>
                              <span className="font-['Manrope'] font-semibold text-[10px]">{r.count}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
              <div ref={messagesEndRef} />
            </div>

            {/* Input */}
            <div className="px-3 sm:px-6 py-3 border-t" style={{ background: 'var(--jp-surface)', borderColor: 'var(--jp-border)' }}>
              {/* Reply preview */}
              {replyingTo && (
                <div
                  data-testid="reply-preview-bar"
                  className="flex items-start gap-2 px-3 py-2 rounded-xl mb-2 border-l-4"
                  style={{ background: 'var(--jp-surface-secondary)', borderColor: 'var(--jp-primary)' }}>
                  <ArrowBendUpLeft size={16} style={{ color: 'var(--jp-primary)', marginTop: 2 }} weight="bold" />
                  <div className="flex-1 min-w-0">
                    <p className="text-[10px] font-['Manrope'] font-bold uppercase tracking-wider" style={{ color: 'var(--jp-primary)' }}>
                      Répondre à {replyingTo.sender_id === user?.user_id ? 'vous-même' : (replyingTo.sender_name || 'ce message')}
                    </p>
                    <p className="text-xs font-['Manrope'] truncate" style={{ color: 'var(--jp-text-secondary)' }}>
                      {replyingTo.text || '[média]'}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setReplyingTo(null)}
                    data-testid="reply-preview-cancel"
                    className="p-1 rounded-md flex-shrink-0"
                    style={{ color: 'var(--jp-text-muted)' }}>
                    <CloseIcon size={14} weight="bold" />
                  </button>
                </div>
              )}
              <div className="flex items-center gap-1.5 sm:gap-2">
                {/* iter237t — "+" toggle, mobile-only. Rotates 45° to act as
                    its own close affordance when the secondary action row
                    is expanded. Hidden on sm+ where everything is inline. */}
                <button
                  type="button"
                  data-testid="attach-menu-toggle"
                  aria-label={showAttachMenu ? 'Fermer les options' : 'Plus d\'options'}
                  aria-expanded={showAttachMenu}
                  onClick={(e) => { e.stopPropagation(); setShowAttachMenu(v => !v); }}
                  className="flex sm:hidden p-2.5 rounded-full flex-shrink-0 items-center justify-center transition-all"
                  style={{
                    background: showAttachMenu ? 'var(--jp-primary)' : 'var(--jp-surface-secondary)',
                    color: showAttachMenu ? 'white' : 'var(--jp-text)',
                    transform: showAttachMenu ? 'rotate(45deg)' : 'rotate(0deg)',
                    transition: 'background 0.18s ease, transform 0.18s ease',
                    width: 40, height: 40,
                  }}
                >
                  <Plus size={18} weight="bold" />
                </button>

                {/* Secondary actions — visible inline on desktop (≥ sm)
                    and toggled by "+" on mobile. NO change in underlying
                    logic for file / money / emoji / voice handlers. */}
                <div
                  className={`${showAttachMenu ? 'flex' : 'hidden'} sm:flex items-center gap-1.5 sm:gap-2`}
                  data-testid="attach-actions"
                >
                <label className="p-2.5 rounded-xl cursor-pointer transition-colors flex-shrink-0"
                  style={{ color: 'var(--jp-text-muted)' }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--jp-surface-secondary)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                  <Paperclip size={18} />
                  <input type="file" accept="image/*,video/*,audio/*,.pdf" className="hidden" data-testid="chat-file-input"
                    onChange={async (e) => {
                      const file = e.target.files?.[0];
                      if (!file) return;
                      // iter237t — auto-collapse the menu after picking a file.
                      setShowAttachMenu(false);
                      // iter147 — for image/video, route through the unified
                      // filter editor first. Audio + PDF skip the editor and
                      // upload directly (no filter applies to those types).
                      if (file.type.startsWith('image/') || file.type.startsWith('video/')) {
                        setFilterEditor({ open: true, files: [file], mode: 'upload' });
                        e.target.value = '';
                        return;
                      }
                      const formData = new FormData();
                      formData.append('file', file);
                      try {
                        const { data: up } = await axios.post(`${API}/api/upload/`, formData, { withCredentials: true });
                        if (socketRef.current?.connected && activeConv) {
                          socketRef.current.emit('send_message', { conv_id: activeConv.conv_id, text: `[Fichier: ${file.name}]`, media: up.url, reply_to: replyingTo?.msg_id || null });
                          setReplyingTo(null);
                        }
                      } catch {}
                      e.target.value = '';
                    }} />
                </label>
                {activeConv.type !== 'group' && getOther(activeConv)?.user_id && (
                  <button type="button" onClick={() => { setShowAttachMenu(false); openMoneyModal(); }} data-testid="chat-money-button"
                    className="p-2.5 rounded-xl transition-all"
                    style={{ color: '#E01C2E' }}
                    onMouseEnter={e => e.currentTarget.style.background = 'rgba(224,28,46,0.08)'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    aria-label={t('chat.envoyer_de_l_argent')}>
                    <CurrencyDollar size={20} weight="bold" />
                  </button>
                )}
                <div className="relative" onClick={e => e.stopPropagation()}>
                  <button type="button" onClick={() => setShowEmojiPicker(v => !v)}
                    data-testid="emoji-picker-button"
                    className="p-2.5 rounded-xl transition-all"
                    style={{ color: showEmojiPicker ? 'var(--jp-primary)' : 'var(--jp-text-muted)' }}
                    onMouseEnter={e => e.currentTarget.style.background = 'var(--jp-surface-secondary)'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    aria-label={t('chat.emojis')}>
                    <Smiley size={20} weight="bold" />
                  </button>
                  {showEmojiPicker && (
                    <EmojiPicker onPick={insertEmojiInMessage} onClose={() => setShowEmojiPicker(false)} />
                  )}
                </div>
                {activeConv.type !== 'group' && getOther(activeConv)?.user_id && (
                  <VoiceRecorder
                    convId={activeConv.conv_id}
                    toUserId={getOther(activeConv).user_id}
                    onSent={onVoiceSent}
                  />
                )}
                </div>
                <textarea
                  data-testid="message-input"
                  value={newMessage}
                  rows={1}
                  // iter237s — auto-grow textarea (1→4 lines max, then internal scroll).
                  onChange={e => {
                    setNewMessage(e.target.value);
                    handleTyping();
                    e.target.style.height = 'auto';
                    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
                  }}
                  // Enter envoie, Shift+Enter passe à la ligne.
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      handleSend();
                    }
                  }}
                  className="jp-input jp-chat-textarea flex-1 text-sm"
                  placeholder="Ecrire un message..."
                  style={{
                    color: 'var(--jp-text)',
                    caretColor: 'var(--jp-primary)',
                    WebkitTextFillColor: 'var(--jp-text)',
                    minHeight: 40,
                    maxHeight: 120,
                    resize: 'none',
                    lineHeight: '1.4',
                    overflowY: 'auto',
                    minWidth: 0,
                  }}
                />
                <button data-testid="send-message-button" onClick={handleSend} disabled={!newMessage.trim()}
                  className="jp-btn jp-btn-primary jp-btn-icon" style={{ padding: '12px' }}>
                  <PaperPlaneRight size={20} weight="fill" />
                </button>
              </div>
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center px-4">
            <div className="text-center jp-animate-fadeIn max-w-sm">
              <div className="w-20 h-20 rounded-full flex items-center justify-center mx-auto mb-4"
                style={{ background: 'var(--jp-primary-subtle)' }}>
                <PaperPlaneRight size={32} style={{ color: 'var(--jp-primary)' }} weight="fill" />
              </div>
              <h3 className="font-['Outfit'] text-xl font-bold mb-2" style={{ color: 'var(--jp-text)' }}>
                Start a conversation or call someone now
              </h3>
              <p className="text-sm font-['Manrope'] mb-6" style={{ color: 'var(--jp-text-secondary)' }}>
                Sélectionnez une conversation existante ou utilisez la recherche pour trouver un contact.
              </p>
              <div className="flex flex-col sm:flex-row gap-2 justify-center">
                <button
                  type="button"
                  data-testid="empty-state-new-chat-main"
                  onClick={() => { document.querySelector('[data-testid="chat-search-input"]')?.focus(); }}
                  className="py-2.5 px-4 rounded-xl text-sm font-['Outfit'] font-semibold flex items-center justify-center gap-2"
                  style={{ background: 'linear-gradient(135deg,#0F056B 0%,#2A1B9B 100%)', color: 'white' }}>
                  <Plus size={16} weight="bold" /> Nouveau chat
                </button>
                <button
                  type="button"
                  data-testid="empty-state-create-group-main"
                  onClick={() => setShowCreateGroup(true)}
                  className="py-2.5 px-4 rounded-xl text-sm font-['Outfit'] font-semibold flex items-center justify-center gap-2"
                  style={{ background: 'var(--jp-surface-secondary)', color: 'var(--jp-text)' }}>
                  <UsersThree size={16} weight="bold" /> Créer un groupe
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ══ SEND MONEY MODAL ══ */}
      {showMoneyModal && activeConv && (
        <div className="fixed inset-0 z-[9998] flex items-end md:items-center justify-center jp-animate-fadeIn"
          style={{ background: 'rgba(11,5,66,0.55)', backdropFilter: 'blur(6px)' }}
          onClick={() => !moneySending && setShowMoneyModal(false)}
          data-testid="money-modal">
          <div className="w-full max-w-md rounded-t-3xl md:rounded-3xl shadow-2xl jp-animate-slideInRight"
            style={{ background: 'var(--jp-surface)' }}
            onClick={e => e.stopPropagation()}>
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b"
              style={{ borderColor: 'var(--jp-border)' }}>
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-full flex items-center justify-center"
                  style={{ background: 'linear-gradient(135deg,#E01C2E 0%,#FF5757 100%)' }}>
                  <CurrencyDollar size={20} weight="fill" color="#fff" />
                </div>
                <div>
                  <h3 className="font-['Outfit'] font-bold text-lg" style={{ color: 'var(--jp-text)' }}>Envoyer de l'argent</h3>
                  <p className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
                    À {getOther(activeConv).first_name} {getOther(activeConv).last_name}
                  </p>
                </div>
              </div>
              <button onClick={() => !moneySending && setShowMoneyModal(false)} data-testid="money-modal-close"
                className="p-1.5 rounded-lg" style={{ color: 'var(--jp-text-muted)' }}>
                <CloseIcon size={18} weight="bold" />
              </button>
            </div>

            {/* Body */}
            <div className="px-6 py-5 space-y-4">
              <div>
                <label className="block text-xs font-['Manrope'] font-semibold mb-2"
                  style={{ color: 'var(--jp-text-secondary)' }}>{t('chat.amount_usd')}</label>
                <input type="number" inputMode="decimal" min="0.10" step="0.10"
                  value={moneyAmount} onChange={e => setMoneyAmount(e.target.value)}
                  data-testid="money-amount-input"
                  className="jp-input w-full text-2xl font-['Outfit'] font-bold" placeholder="0.00" autoFocus />
                {/* iter237m — Quick chips in USD (replaces 500/1000/2500/5000 XAF) */}
                <div className="flex gap-2 mt-2">
                  {[1, 2, 5, 10].map(v => (
                    <button key={v} type="button" onClick={() => setMoneyAmount(String(v))}
                      data-testid={`money-quick-${v}`}
                      className="flex-1 py-2 rounded-lg text-xs font-['Manrope'] font-semibold transition-all"
                      style={{
                        background: moneyAmount === String(v) ? '#0F056B' : 'var(--jp-surface-secondary)',
                        color: moneyAmount === String(v) ? 'white' : 'var(--jp-text)',
                      }}>
                      {v} USD
                    </button>
                  ))}
                </div>
                {/* iter237m — Live local-currency equivalent (FCFA / GHS / NGN…) */}
                {parseFloat(moneyAmount) > 0 && (
                  <div className="mt-3" data-testid="money-amount-currency-block">
                    <WalletDepositCurrencySelector
                      amountUsd={parseFloat(moneyAmount)}
                      countryCode={user?.country_code}
                    />
                  </div>
                )}
              </div>

              <div>
                <label className="block text-xs font-['Manrope'] font-semibold mb-2"
                  style={{ color: 'var(--jp-text-secondary)' }}>{t('chat.note_optionnel')}</label>
                <input type="text" maxLength="200"
                  value={moneyNote} onChange={e => setMoneyNote(e.target.value)}
                  data-testid="money-note-input"
                  className="jp-input w-full text-sm" placeholder="Ex: Pour le diner 🍽️" />
              </div>

              {moneyError && (
                <div className="text-xs font-['Manrope'] px-3 py-2 rounded-lg" data-testid="money-error"
                  style={{ background: 'rgba(224,28,46,0.1)', color: '#E01C2E' }}>
                  {moneyError}
                </div>
              )}

              <button onClick={handleSendMoney}
                disabled={moneySending || !moneyAmount || parseFloat(moneyAmount) < 0.10}
                data-testid="money-confirm-button"
                className="w-full py-3 rounded-xl font-['Outfit'] font-bold text-white transition-all disabled:opacity-50"
                style={{ background: 'linear-gradient(135deg,#0F056B 0%,#E01C2E 100%)' }}>
                {moneySending ? 'Envoi en cours…' : `Envoyer ${moneyAmount ? parseFloat(moneyAmount).toFixed(2) : '0.00'} USD`}
              </button>
              <p className="text-[10px] text-center font-['Manrope']" style={{ color: 'var(--jp-text-muted)' }}>
                Montant minimum 0.10 USD · Débité de votre wallet JAPAP
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ══ FORWARD MODAL ══ */}
      {forwardingMsg && (
        <ForwardModal
          message={forwardingMsg}
          conversations={conversations}
          onClose={() => setForwardingMsg(null)}
          onDone={(res) => {
            setForwardingMsg(null);
            toast.success(`Transféré vers ${res?.forwarded || 0} conversation(s)`);
            loadConversations();
          }}
        />
      )}

      {/* ══ FORWARD CHAIN MODAL ══ */}
      {chainForMsgId && (
        <ForwardChainModal
          msgId={chainForMsgId}
          onClose={() => setChainForMsgId(null)}
          onViewHop={async (hop) => {
            if (!hop.viewable || !hop.conv_id) {
              toast.info('Contenu privé — accès limité.');
              return;
            }
            setChainForMsgId(null);
            let srcConv = conversations.find(c => c.conv_id === hop.conv_id);
            if (!srcConv) {
              try { await loadConversations(); } catch {}
              srcConv = conversations.find(c => c.conv_id === hop.conv_id)
                || { conv_id: hop.conv_id, participants: [] };
            }
            setActiveConv(srcConv);
            setTimeout(() => {
              const el = document.querySelector(
                `[data-testid="message-${hop.msg_id}"], [data-testid="message-voice-${hop.msg_id}"], [data-testid="message-money-${hop.msg_id}"]`
              );
              if (el) {
                el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                el.style.transition = 'background 0.8s ease';
                el.style.background = 'rgba(224,28,46,0.12)';
                setTimeout(() => { el.style.background = 'transparent'; }, 1800);
              }
            }, 800);
          }}
        />
      )}

      {/* iter147 — Unified media filter editor for chat attachments */}
      <MediaFilterEditor
        open={filterEditor.open}
        files={filterEditor.files}
        mode={filterEditor.mode}
        onClose={() => setFilterEditor({ open: false, files: [], mode: 'upload' })}
        onApply={async (filteredFiles) => {
          const file = filteredFiles[0];
          if (!file || !activeConv) return;
          const formData = new FormData();
          formData.append('file', file);
          try {
            const { data: up } = await axios.post(`${API}/api/upload/`, formData, { withCredentials: true });
            if (socketRef.current?.connected) {
              socketRef.current.emit('send_message', {
                conv_id: activeConv.conv_id,
                text: `[Fichier: ${file.name}]`,
                media: up.url,
                reply_to: replyingTo?.msg_id || null,
              });
              setReplyingTo(null);
            }
          } catch (_e) {
            toast.error("Échec de l'envoi du fichier");
          }
        }}
      />
    </div>
  );
}
