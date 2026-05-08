/**
 * JAPAP Messenger — Call Provider (Sprint B + C — LiveKit)
 * =========================================================
 * Audio/Video 1-1 + Group calls. LiveKit for media, Socket.io for ringing.
 *
 *   Sprint B  ← audio 1-1 ringing flow (caller ↔ callee)
 *   Sprint C  ← video publish/subscribe, group calls (join-live via banner),
 *               active speaker highlight, camera switch
 *   Sprint D  ← record start/stop + AI summary modal (host-only)
 *
 * Video grid participants shape :
 *   [{ sid, identity, name, avatar, videoOn, muted, isSpeaking, isLocal }]
 *
 * Mobile-first: VideoCallGrid uses 1 col <480px / 2 col 480-960 / auto-fit desktop.
 */
import { createContext, useContext, useEffect, useRef, useState, useCallback } from 'react';
import { io } from 'socket.io-client';
import axios from 'axios';
import { toast } from 'sonner';
import { Room, RoomEvent, Track } from 'livekit-client';
import { useAuth } from '@/context/AuthContext';
import IncomingCallOverlay from '@/components/calls/IncomingCallOverlay';
import InCallScreen from '@/components/calls/InCallScreen';
import VideoCallGrid from '@/components/calls/VideoCallGrid';
import CallSummaryModal from '@/components/calls/CallSummaryModal';
import { useTranslation } from 'react-i18next';

const API = process.env.REACT_APP_BACKEND_URL;

const CallContext = createContext(null);

export function CallProvider({ children }) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [state, setState] = useState('idle');              // idle | calling | ringing | in-call
  const [callInfo, setCallInfo] = useState(null);
  const [muted, setMuted] = useState(false);
  const [speakerOn, setSpeakerOn] = useState(true);
  const [videoOn, setVideoOn] = useState(false);           // Sprint C — local camera state
  const [connectionStatus, setConnectionStatus] = useState('connecting'); // connecting | connected | reconnecting | failed
  const [callStartedAt, setCallStartedAt] = useState(null);
  const [participants, setParticipants] = useState([]);    // Sprint C — video grid state
  const [liveGroupCalls, setLiveGroupCalls] = useState({}); // { conv_id: { session_id, mode, host_name, title } }

  const socketRef = useRef(null);
  const roomRef = useRef(null);
  const remoteAudioRef = useRef(null);
  const ringtoneRef = useRef(null);
  const callInfoRef = useRef(null);
  const videoRefsRef = useRef({});   // { sid: <video> } updated via ref callback in VideoCallGrid
  const facingModeRef = useRef('user');
  const ringTimeoutRef = useRef(null);   // iter193 — 45s ring timeout
  const [recording, setRecording] = useState(false);        // Sprint D
  const [recordingId, setRecordingId] = useState(null);
  const [endedSessionId, setEndedSessionId] = useState(null); // for auto CallSummaryModal

  useEffect(() => { callInfoRef.current = callInfo; }, [callInfo]);

  // ───── Ringtone (caller + callee) ─────
  useEffect(() => {
    const shouldRing = state === 'calling' || state === 'ringing';
    if (shouldRing) {
      if (!ringtoneRef.current) {
        const a = new Audio('/sounds/ringtone.wav');
        a.loop = true;
        a.volume = 0.6;
        ringtoneRef.current = a;
      }
      ringtoneRef.current.play().catch(() => { /* autoplay blocked — non-fatal */ });
    } else if (ringtoneRef.current) {
      try { ringtoneRef.current.pause(); ringtoneRef.current.currentTime = 0; } catch {}
    }
  }, [state]);

  // Sprint D — Record toggle (host-only enforced server-side)
  const toggleRecord = useCallback(async () => {
    const info = callInfoRef.current;
    if (!info?.session_id) return;
    try {
      if (!recording) {
        const { data } = await axios.post(`${API}/api/calls/${info.session_id}/record/start`, {}, { withCredentials: true });
        setRecordingId(data.recording_id);
        setRecording(true);
        toast.success('Enregistrement démarré');
      } else {
        await axios.post(`${API}/api/calls/${info.session_id}/record/stop`, {}, { withCredentials: true });
        setRecording(false);
        toast.info('Enregistrement arrêté — la synthèse IA sera prête dans 1-2 min.');
      }
    } catch (e) {
      const status = e?.response?.status;
      const detail = e?.response?.data?.detail;
      if (status === 503) toast.info(detail || 'Enregistrement non disponible (LiveKit non configuré).');
      else toast.error(detail || 'Erreur d\'enregistrement');
    }
  }, [recording]);

  // ───── Cleanup ─────
  const cleanup = useCallback(() => {
    if (ringTimeoutRef.current) {
      clearTimeout(ringTimeoutRef.current);
      ringTimeoutRef.current = null;
    }
    if (roomRef.current) {
      try { roomRef.current.disconnect(); } catch {}
      roomRef.current = null;
    }
    setMuted(false);
    setSpeakerOn(true);
    setVideoOn(false);
    setConnectionStatus('connecting');
    setCallStartedAt(null);
    setRecording(false);
    setRecordingId(null);
    setParticipants([]);
    videoRefsRef.current = {};
    facingModeRef.current = 'user';
  }, []);

  const endCall = useCallback(async (reason = 'ended') => {
    const info = callInfoRef.current;
    const hadRecording = recordingId && info?.session_id;
    if (info?.call_id) {
      callLog('call_ended', {
        call_id: info.call_id, room_id: info.room_name, meta: { reason },
      });
    }
    if (info?.session_id) {
      try { await axios.post(`${API}/api/calls/${info.session_id}/leave`, {}, { withCredentials: true }); } catch {}
    }
    if (info?.call_id && socketRef.current?.connected) {
      socketRef.current.emit('call_end', { call_id: info.call_id, peer_id: info.peer_id });
    }
    if (info?.kind === 'group' && socketRef.current?.connected) {
      // Informs the rest of the conv that we left; server will emit 'group_call_ended'
      // once the session is fully terminated (last participant left).
      socketRef.current.emit('group_call_leave', { conv_id: info.conv_id, session_id: info.session_id });
    }
    if (hadRecording) setEndedSessionId({ session_id: info.session_id, conv_id: info.conv_id || null });
    cleanup();
    setState('idle');
    setCallInfo(null);
  }, [cleanup, recordingId]);

  // ───── Participant state helpers (Sprint C) ─────
  const upsertParticipant = useCallback((patch) => {
    setParticipants((prev) => {
      const idx = prev.findIndex((p) => p.sid === patch.sid);
      if (idx === -1) return [...prev, { videoOn: false, muted: false, isSpeaking: false, ...patch }];
      const next = [...prev];
      next[idx] = { ...next[idx], ...patch };
      return next;
    });
  }, []);

  const removeParticipant = useCallback((sid) => {
    setParticipants((prev) => prev.filter((p) => p.sid !== sid));
    delete videoRefsRef.current[sid];
  }, []);

  // ───── LiveKit Room setup ─────
  const connectToRoom = useCallback(async (sessionId, { publishVideo = false } = {}) => {
    callLog('token_requested', { call_id: sessionId });
    const { data: tok } = await axios.post(`${API}/api/calls/token`, { session_id: sessionId }, { withCredentials: true });
    callLog('livekit_connecting', {
      call_id: sessionId, room_id: tok.room,
      meta: { ws_url: tok.ws_url, publishVideo },
    });
    const room = new Room({
      adaptiveStream: true,
      dynacast: true,
      publishDefaults: { audioPreset: { maxBitrate: 32000 } },
    });
    roomRef.current = room;
    // iter193b — Attach passive telemetry (connected/disconnected/
    // reconnecting/trackSubscribed). Returns an unsubscribe function we
    // store on the room ref so cleanup() can strip our listeners.
    const detachTelemetry = attachRoomTelemetry(room, {
      call_id: sessionId, room_id: tok.room,
    });
    roomRef.current._detachTelemetry = detachTelemetry;

    const displayName = (p) => (p.name || p.identity || '');

    // Remote track handling (audio & video)
    room.on(RoomEvent.TrackSubscribed, (track, publication, remote) => {
      if (track.kind === Track.Kind.Audio) {
        if (remoteAudioRef.current) track.attach(remoteAudioRef.current);
        else track.attach();
      } else if (track.kind === Track.Kind.Video) {
        upsertParticipant({
          sid: remote.sid, identity: remote.identity, name: displayName(remote),
          videoOn: true, muted: publication.isMuted, isLocal: false,
        });
        const el = videoRefsRef.current[remote.sid];
        if (el) track.attach(el);
      }
    });
    room.on(RoomEvent.TrackUnsubscribed, (track, publication, remote) => {
      if (track.kind === Track.Kind.Video) {
        upsertParticipant({ sid: remote.sid, videoOn: false });
      }
    });
    room.on(RoomEvent.TrackMuted, (publication, p) => {
      if (publication.kind === Track.Kind.Audio) upsertParticipant({ sid: p.sid, muted: true });
      if (publication.kind === Track.Kind.Video) upsertParticipant({ sid: p.sid, videoOn: false });
    });
    room.on(RoomEvent.TrackUnmuted, (publication, p) => {
      if (publication.kind === Track.Kind.Audio) upsertParticipant({ sid: p.sid, muted: false });
      if (publication.kind === Track.Kind.Video) upsertParticipant({ sid: p.sid, videoOn: true });
    });
    room.on(RoomEvent.ParticipantConnected, (p) => {
      upsertParticipant({ sid: p.sid, identity: p.identity, name: displayName(p), isLocal: false });
    });
    room.on(RoomEvent.ParticipantDisconnected, (p) => removeParticipant(p.sid));
    room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
      const activeSids = new Set(speakers.map((s) => s.sid));
      setParticipants((prev) => prev.map((pp) => ({ ...pp, isSpeaking: activeSids.has(pp.sid) })));
    });
    room.on(RoomEvent.Disconnected, () => endCall('ended'));
    room.on(RoomEvent.ConnectionStateChanged, (st) => {
      if (st === 'connected') setConnectionStatus('connected');
      else if (st === 'reconnecting') setConnectionStatus('reconnecting');
      else if (st === 'failed') { setConnectionStatus('failed'); toast.error('Connexion à l\'appel perdue.'); }
    });

    await room.connect(tok.ws_url, tok.token);

    // Local participant tile (prepended so it renders first)
    upsertParticipant({
      sid: room.localParticipant.sid,
      identity: room.localParticipant.identity,
      name: 'Vous',
      videoOn: publishVideo,
      muted: false,
      isLocal: true,
    });
    // Mirror already-connected remote participants (joining an in-progress group call)
    room.remoteParticipants.forEach((p) => {
      upsertParticipant({
        sid: p.sid, identity: p.identity, name: displayName(p), isLocal: false,
        videoOn: Array.from(p.videoTrackPublications.values()).some((pub) => pub.isSubscribed && !pub.isMuted),
        muted: Array.from(p.audioTrackPublications.values()).every((pub) => pub.isMuted),
      });
    });

    // Publish local mic
    try {
      await room.localParticipant.setMicrophoneEnabled(true);
    } catch (e) {
      callLog('media_device_error', {
        call_id: sessionId, error: e, meta: { kind: 'microphone' },
      });
      toast.error('Accès au microphone refusé');
      await endCall('failed');
      throw e;
    }
    if (publishVideo) {
      try {
        await room.localParticipant.setCameraEnabled(true, { facingMode: 'user' });
        setVideoOn(true);
      } catch (e) {
        callLog('media_device_error', {
          call_id: sessionId, error: e, meta: { kind: 'camera' },
        });
        toast.error('Accès à la caméra refusé');
      }
    }
    try { await axios.post(`${API}/api/calls/${sessionId}/join`, {}, { withCredentials: true }); } catch {}
    setCallStartedAt(Date.now());
    setConnectionStatus('connected');
    setState('in-call');
    callLog('livekit_connected', {
      call_id: sessionId, room_id: tok.room,
      meta: { publishVideo, participants: room.remoteParticipants.size + 1 },
    });
  }, [endCall, upsertParticipant, removeParticipant]);

  // ───── START CALL (caller, p2p) ─────
  const startCall = useCallback(async (peer) => {
    if (state !== 'idle') return;
    callLog('call_button_clicked', {
      meta: { type: peer?.type || 'audio', peer_id: peer?.user_id?.slice(-6) },
    });
    // iter193 — Guard 1: require a connected signaling socket BEFORE
    // allocating a LiveKit session. Without this, the `call_invite` emit
    // below was silently dropped when the socket hadn't reconnected yet
    // (e.g. background tab, post-PWA-wakeup), leaving the caller stuck on
    // "calling..." forever with no visible error. This was the #1 cause
    // of "bouton appeler ne réagit pas / bloqué sur connecting" reports.
    if (!socketRef.current?.connected) {
      callLog('socket_not_connected', { meta: { stage: 'startCall' } });
      toast.error(
        "Connexion temps-réel non établie. Réessayez dans un instant.",
        { duration: 4000 },
      );
      return;
    }
    // iter193 — Guard 2: request mic/cam permission in the user-gesture
    // handler, BEFORE any await. iOS Safari strictly requires permission
    // prompts to happen synchronously inside a click handler, otherwise
    // the prompt never appears and setMicrophoneEnabled(true) later fails
    // silently with `NotAllowedError`. We only probe — release immediately.
    callLog('permission_prompt_opened', {
      meta: { stage: 'startCall', type: peer?.type || 'audio' },
    });
    try {
      const probeConstraints = peer.type === 'video'
        ? { audio: true, video: { facingMode: 'user' } }
        : { audio: true };
      const stream = await navigator.mediaDevices.getUserMedia(probeConstraints);
      stream.getTracks().forEach((t) => t.stop());
      callLog('permission_granted', {
        meta: { stage: 'startCall', type: peer?.type || 'audio' },
      });
    } catch (err) {
      const name = err?.name || '';
      callLog('permission_denied', {
        error: err,
        meta: { stage: 'startCall', type: peer?.type || 'audio' },
      });
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        toast.error(
          peer.type === 'video'
            ? t('call_context.acces_camera_micro_refuse_autorisez')
            : t('call_context.acces_micro_refuse_autorisez_le_dan'),
          { duration: 5000 },
        );
      } else if (name === 'NotFoundError' || name === 'OverconstrainedError') {
        toast.error("Aucun micro/caméra détecté sur cet appareil.");
      } else {
        toast.error("Impossible d'accéder au micro/caméra.");
      }
      return;
    }
    try {
      const mode = peer.type === 'video' ? 'video' : 'audio';
      const { data: sess } = await axios.post(`${API}/api/calls/session`,
        { mode, kind: 'p2p', callee_id: peer.user_id },
        { withCredentials: true },
      );
      const info = {
        session_id: sess.session_id,
        room_name: sess.room_name,
        call_id: sess.session_id,
        peer_id: peer.user_id,
        peer_name: peer.name,
        peer_avatar: peer.avatar,
        type: mode,
        kind: 'p2p',
        conv_id: peer.conv_id || null,   // ← captured from ChatPage for share-back
        direction: 'outgoing',
      };
      setCallInfo(info);
      setState('calling');
      socketRef.current.emit('call_invite', {
        call_id: info.call_id,
        session_id: info.session_id,
        room_name: info.room_name,
        callee_id: peer.user_id,
        type: info.type,
        caller_name: `${user?.first_name || ''} ${user?.last_name || ''}`.trim() || user?.username || '',
        caller_avatar: user?.avatar || '',
      });
      callLog('call_invite_sent', {
        call_id: info.call_id, room_id: info.room_name,
        meta: { type: info.type, kind: 'p2p' },
      });
      // iter193 — Guard 3: ring timeout. If the callee doesn't pick up
      // within 45s, hang up gracefully. Clears in cleanup() as well.
      if (ringTimeoutRef.current) clearTimeout(ringTimeoutRef.current);
      ringTimeoutRef.current = setTimeout(() => {
        if (callInfoRef.current && callInfoRef.current.session_id === info.session_id) {
          callLog('ring_timeout', { call_id: info.call_id });
          toast.info('Pas de réponse — appel annulé.');
          endCall('missed');
        }
      }, 45000);
    } catch (e) {
      const status = e?.response?.status;
      const detail = e?.response?.data?.detail;
      callLog('call_error', {
        error: e, meta: { stage: 'startCall_session', status },
      });
      if (status === 503) toast.info(detail || 'Appels non disponibles pour le moment.');
      else toast.error(detail || 'Impossible de démarrer l\'appel.');
      cleanup();
      setState('idle');
      setCallInfo(null);
    }
  }, [state, user, cleanup, endCall]);

  // ───── ACCEPT CALL (callee, p2p) ─────
  const acceptCall = useCallback(async () => {
    const info = callInfoRef.current;
    if (!info?.session_id) return;
    callLog('call_accepted', {
      call_id: info.call_id, room_id: info.room_name,
      meta: { type: info.type },
    });
    // iter193 — Same mic/cam permission probe inside the user-gesture
    // handler as startCall. Without it, Safari iOS silently denies the
    // prompt on "Accepter" clicks post-background.
    callLog('permission_prompt_opened', {
      call_id: info.call_id, meta: { stage: 'acceptCall', type: info.type },
    });
    try {
      const probe = info.type === 'video'
        ? { audio: true, video: { facingMode: 'user' } }
        : { audio: true };
      const s = await navigator.mediaDevices.getUserMedia(probe);
      s.getTracks().forEach((t) => t.stop());
      callLog('permission_granted', {
        call_id: info.call_id, meta: { stage: 'acceptCall' },
      });
    } catch (err) {
      const name = err?.name || '';
      callLog('permission_denied', {
        call_id: info.call_id, error: err, meta: { stage: 'acceptCall' },
      });
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        toast.error(info.type === 'video'
          ? t('call_context.acces_camera_micro_refuse')
          : t('call_context.acces_micro_refuse'));
      } else {
        toast.error("Impossible d'accéder au micro/caméra.");
      }
      // Still signal a reject so the caller UI moves on
      if (socketRef.current?.connected) {
        socketRef.current.emit('call_reject',
          { call_id: info.call_id, caller_id: info.peer_id });
      }
      cleanup();
      setState('idle');
      setCallInfo(null);
      return;
    }
    try {
      if (socketRef.current?.connected) {
        socketRef.current.emit('call_accept', {
          call_id: info.call_id, session_id: info.session_id, caller_id: info.peer_id,
        });
      }
      await connectToRoom(info.session_id, { publishVideo: info.type === 'video' });
    } catch (e) {
      const detail = e?.response?.data?.detail;
      toast.error(detail || 'Impossible de rejoindre l\'appel.');
      await endCall('failed');
    }
  }, [connectToRoom, endCall, cleanup]);

  // ───── START GROUP CALL (host, Sprint C) ─────
  const startGroupCall = useCallback(async ({ conv_id, title, mode = 'audio', max_participants = 12 }) => {
    if (state !== 'idle') return;
    try {
      const { data: sess } = await axios.post(`${API}/api/calls/session`,
        { mode, kind: 'group', conv_id, max_participants },
        { withCredentials: true },
      );
      const info = {
        session_id: sess.session_id,
        room_name: sess.room_name,
        call_id: sess.session_id,
        peer_id: null,
        peer_name: title || 'Appel de groupe',
        peer_avatar: '',
        type: mode,
        direction: 'outgoing',
        kind: 'group',
        conv_id,
        title,
      };
      setCallInfo(info);
      // Connect to the LiveKit room FIRST. Only announce to the conv once the
      // host's media is actually live — this prevents phantom "Rejoindre"
      // banners if the host's handshake fails (permissions, network…).
      await connectToRoom(sess.session_id, { publishVideo: mode === 'video' });
      if (socketRef.current?.connected) {
        socketRef.current.emit('group_call_announce', {
          conv_id, session_id: sess.session_id, mode, title,
          host_name: `${user?.first_name || ''} ${user?.last_name || ''}`.trim() || user?.username || '',
        });
      }
    } catch (e) {
      const detail = e?.response?.data?.detail;
      if (e?.response?.status === 503) toast.info(detail || 'Appels non disponibles pour le moment.');
      else toast.error(detail || 'Impossible de démarrer l\'appel de groupe.');
      cleanup(); setState('idle'); setCallInfo(null);
    }
  }, [state, connectToRoom, cleanup, user]);

  // ───── JOIN A LIVE GROUP CALL (peer, Sprint C) ─────
  const joinGroupCall = useCallback(async ({ conv_id, session_id, mode = 'audio', title }) => {
    if (state !== 'idle') return;
    try {
      const info = {
        session_id,
        room_name: `japap_${session_id}`,
        call_id: session_id,
        peer_id: null,
        peer_name: title || 'Appel de groupe',
        peer_avatar: '',
        type: mode,
        direction: 'incoming',
        kind: 'group',
        conv_id,
        title,
      };
      setCallInfo(info);
      await connectToRoom(session_id, { publishVideo: mode === 'video' });
    } catch (e) {
      const status = e?.response?.status;
      const detail = e?.response?.data?.detail;
      if (status === 410) toast.info('Cet appel est déjà terminé.');
      else if (status === 403) toast.error('Vous n\'êtes pas membre de cette conversation.');
      else toast.error(detail || 'Impossible de rejoindre l\'appel.');
      cleanup(); setState('idle'); setCallInfo(null);
    }
  }, [state, connectToRoom, cleanup]);

  const rejectCall = useCallback(() => {
    const info = callInfoRef.current;
    if (info && socketRef.current?.connected) {
      socketRef.current.emit('call_reject', { call_id: info.call_id, caller_id: info.peer_id });
    }
    cleanup();
    setState('idle');
    setCallInfo(null);
  }, [cleanup]);

  // ───── Controls ─────
  const toggleMute = useCallback(async () => {
    const room = roomRef.current;
    if (!room?.localParticipant) return;
    const next = !muted;
    try {
      await room.localParticipant.setMicrophoneEnabled(!next);
      setMuted(next);
      upsertParticipant({ sid: room.localParticipant.sid, muted: next });
    } catch (e) { console.warn('mute toggle failed', e); }
  }, [muted, upsertParticipant]);

  const toggleSpeaker = useCallback(() => {
    const next = !speakerOn;
    setSpeakerOn(next);
    if (remoteAudioRef.current) remoteAudioRef.current.volume = next ? 1.0 : 0.35;
  }, [speakerOn]);

  // Sprint C — toggle local camera on/off
  const toggleVideo = useCallback(async () => {
    const room = roomRef.current;
    if (!room?.localParticipant) return;
    const next = !videoOn;
    try {
      await room.localParticipant.setCameraEnabled(next, { facingMode: facingModeRef.current });
      setVideoOn(next);
      upsertParticipant({ sid: room.localParticipant.sid, videoOn: next });
    } catch (e) {
      toast.error('Accès à la caméra refusé');
    }
  }, [videoOn, upsertParticipant]);

  // Sprint C — switch front/rear camera (best-effort, mobile)
  const switchCamera = useCallback(async () => {
    const room = roomRef.current;
    if (!room?.localParticipant) return;
    const next = facingModeRef.current === 'user' ? 'environment' : 'user';
    facingModeRef.current = next;
    try {
      // Disable then re-enable with new facing mode; LiveKit replaces the track.
      await room.localParticipant.setCameraEnabled(false);
      await room.localParticipant.setCameraEnabled(true, { facingMode: next });
      setVideoOn(true);
      upsertParticipant({ sid: room.localParticipant.sid, videoOn: true });
    } catch (e) {
      toast.info('Impossible de changer de caméra sur cet appareil.');
    }
  }, [upsertParticipant]);

  // ───── Socket.io ringing listener ─────
  useEffect(() => {
    if (!user) return;
    const sock = io(API, {
      path: '/api/socket.io',
      reconnection: true, reconnectionAttempts: Infinity,
      reconnectionDelay: 800, reconnectionDelayMax: 5000,
    });
    socketRef.current = sock;
    // Server auto-authenticates via httpOnly cookie on connect (see server.py).
    // Legacy explicit authenticate kept as fallback for edge cases.
    sock.on('connect', () => {
      const token = document.cookie.split(';').find(c => c.trim().startsWith('access_token='))?.split('=')[1];
      if (token) sock.emit('authenticate', { token });
    });
    sock.on('call_incoming', (d) => {
      if (callInfoRef.current) return;
      callLog('call_incoming_received', {
        call_id: d.call_id, room_id: d.room_name,
        meta: { type: d.type || 'audio', from: String(d.caller_id || '').slice(-6) },
      });
      setCallInfo({
        session_id: d.session_id,
        room_name: d.room_name,
        call_id: d.call_id,
        peer_id: d.caller_id,
        peer_name: d.caller_name || 'Inconnu',
        peer_avatar: d.caller_avatar || '',
        type: d.type || 'audio',
        kind: 'p2p',
        direction: 'incoming',
      });
      setState('ringing');
    });
    sock.on('call_unavailable', () => {
      callLog('call_error', { meta: { reason: 'call_unavailable' } });
      toast.info('Le contact est hors ligne.');
      endCall('failed');
    });
    sock.on('call_accepted', async (d) => {
      const info = callInfoRef.current;
      if (!info) return;
      // iter193 — clear the ring timeout as soon as the callee picks up.
      if (ringTimeoutRef.current) {
        clearTimeout(ringTimeoutRef.current);
        ringTimeoutRef.current = null;
      }
      try {
        await connectToRoom(d.session_id || info.session_id, { publishVideo: info.type === 'video' });
      } catch (e) {
        callLog('livekit_failed', {
          call_id: info.call_id, room_id: info.room_name,
          error: e, meta: { stage: 'call_accepted_connect' },
        });
        toast.error(e?.response?.data?.detail || 'Connexion à l\'appel impossible.');
        await endCall('failed');
      }
    });
    sock.on('call_rejected', () => {
      callLog('call_rejected', { meta: { side: 'caller' } });
      toast.info('Appel refusé.');
      endCall('rejected');
    });
    sock.on('call_ended', () => endCall('ended'));
    sock.on('call_cancelled', () => { cleanup(); setState('idle'); setCallInfo(null); });

    // Sprint C — group-call "live" announcements
    sock.on('group_call_live', (d) => {
      if (!d?.conv_id || !d?.session_id) return;
      setLiveGroupCalls((prev) => ({
        ...prev,
        [d.conv_id]: {
          session_id: d.session_id,
          mode: d.mode || 'audio',
          host_name: d.host_name || '',
          title: d.title || '',
          started_at: Date.now(),
        },
      }));
    });
    sock.on('group_call_ended', (d) => {
      if (!d?.conv_id) return;
      setLiveGroupCalls((prev) => {
        const next = { ...prev };
        delete next[d.conv_id];
        return next;
      });
    });
    return () => { try { sock.disconnect(); } catch {} };
  }, [user, connectToRoom, endCall, cleanup]);

  const isHost = state === 'in-call' && callInfo?.direction === 'outgoing';
  const callSession = callInfo ? { session_id: callInfo.session_id, started_at: callStartedAt } : null;
  const showVideoGrid = state === 'in-call' && callInfo && (callInfo.type === 'video' || callInfo.kind === 'group');

  return (
    <CallContext.Provider value={{
      state, callInfo, startCall, startGroupCall, joinGroupCall, acceptCall, rejectCall, endCall,
      toggleMute, toggleVideo, toggleSpeaker, toggleRecord, switchCamera,
      muted, speakerOn, videoOn, recording, participants, liveGroupCalls,
    }}>
      {children}
      <audio ref={remoteAudioRef} autoPlay playsInline />

      {state === 'ringing' && callInfo?.direction === 'incoming' && (
        <IncomingCallOverlay
          call={{
            caller_name: callInfo.peer_name,
            caller_avatar: callInfo.peer_avatar,
            mode: callInfo.type,
          }}
          onAccept={acceptCall}
          onDecline={rejectCall}
        />
      )}

      {state === 'calling' && callInfo?.direction === 'outgoing' && (
        <InCallScreen
          session={{ session_id: callInfo.session_id, started_at: null }}
          peerName={callInfo.peer_name}
          peerAvatar={callInfo.peer_avatar}
          muted={false}
          speakerOn={speakerOn}
          recording={false}
          canRecord={false}
          connectionStatus="connecting"
          onToggleMute={() => {}}
          onToggleSpeaker={toggleSpeaker}
          onHangup={() => endCall('cancelled')}
        />
      )}

      {/* Video + Group calls use VideoCallGrid */}
      {showVideoGrid && (
        <VideoCallGrid
          session={callSession}
          participants={participants}
          videoRefs={videoRefsRef.current}
          muted={muted}
          videoOn={videoOn}
          recording={recording}
          canRecord={isHost}
          onToggleMute={toggleMute}
          onToggleVideo={toggleVideo}
          onSwitchCamera={switchCamera}
          onToggleRecord={toggleRecord}
          onHangup={() => endCall('ended')}
        />
      )}

      {/* Audio-only 1-1 uses InCallScreen */}
      {state === 'in-call' && callInfo && !showVideoGrid && (
        <InCallScreen
          session={callSession}
          peerName={callInfo.peer_name}
          peerAvatar={callInfo.peer_avatar}
          muted={muted}
          speakerOn={speakerOn}
          recording={recording}
          canRecord={isHost}
          connectionStatus={connectionStatus}
          onToggleMute={toggleMute}
          onToggleSpeaker={toggleSpeaker}
          onHangup={() => endCall('ended')}
          onToggleRecord={toggleRecord}
        />
      )}

      {endedSessionId && (
        <CallSummaryModal
          sessionId={endedSessionId.session_id || endedSessionId}
          convId={endedSessionId.conv_id || null}
          onClose={() => setEndedSessionId(null)}
        />
      )}
    </CallContext.Provider>
  );
}

export function useCall() {
  const ctx = useContext(CallContext);
  if (!ctx) throw new Error('useCall must be used inside CallProvider');
  return ctx;
}
