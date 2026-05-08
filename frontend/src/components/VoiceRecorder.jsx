/**
 * JAPAP Messenger — Voice Recorder
 * ================================
 * Press-and-hold (or toggle) microphone button. Records via MediaRecorder,
 * uploads to /api/messages/voice, waits for Whisper transcription, and
 * shows an inline spinner during the whole flow.
 */
import { useState, useRef, useCallback, useEffect } from 'react';
import { Microphone, Stop, X as CloseIcon } from '@phosphor-icons/react';
import axios from 'axios';
import { toast } from 'sonner';

const API = process.env.REACT_APP_BACKEND_URL;
const MAX_DURATION_MS = 60_000; // 60 seconds cap

export default function VoiceRecorder({ convId, toUserId, onSent }) {
  const [recording, setRecording] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);
  const streamRef = useRef(null);
  const timerRef = useRef(null);
  const startedAtRef = useRef(0);
  const cancelledRef = useRef(false);

  const cleanup = useCallback(() => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    if (streamRef.current) { streamRef.current.getTracks().forEach(t => t.stop()); streamRef.current = null; }
    chunksRef.current = [];
    setElapsed(0);
    setRecording(false);
  }, []);

  const startRecording = useCallback(async () => {
    if (recording || uploading) return;
    cancelledRef.current = false;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      // Prefer webm/opus — universally supported and small
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus'
                 : MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm'
                 : MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' : '';
      const mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      mediaRecorderRef.current = mr;
      chunksRef.current = [];
      mr.ondataavailable = (e) => { if (e.data && e.data.size > 0) chunksRef.current.push(e.data); };
      mr.onstop = async () => {
        const duration = Math.round((Date.now() - startedAtRef.current) / 1000);
        const blob = new Blob(chunksRef.current, { type: mr.mimeType || 'audio/webm' });
        cleanup();
        if (cancelledRef.current || blob.size < 300) return; // discard tiny blobs (<0.3KB)
        await uploadVoice(blob, duration);
      };
      startedAtRef.current = Date.now();
      mr.start();
      setRecording(true);
      timerRef.current = setInterval(() => {
        const e = Math.floor((Date.now() - startedAtRef.current) / 1000);
        setElapsed(e);
        if (Date.now() - startedAtRef.current >= MAX_DURATION_MS) stopRecording();
      }, 200);
    } catch (e) {
      toast.error('Micro refusé ou inaccessible');
      cleanup();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recording, uploading, cleanup]);

  const stopRecording = useCallback(() => {
    const mr = mediaRecorderRef.current;
    if (mr && mr.state !== 'inactive') {
      try { mr.stop(); } catch {}
    } else {
      cleanup();
    }
  }, [cleanup]);

  const cancelRecording = useCallback(() => {
    cancelledRef.current = true;
    stopRecording();
  }, [stopRecording]);

  // Hard cleanup on unmount to release microphone even mid-recording
  useEffect(() => () => {
    cancelledRef.current = true;
    try {
      const mr = mediaRecorderRef.current;
      if (mr && mr.state !== 'inactive') mr.stop();
    } catch {}
    if (timerRef.current) clearInterval(timerRef.current);
    if (streamRef.current) streamRef.current.getTracks().forEach(t => t.stop());
  }, []);

  const uploadVoice = async (blob, duration) => {
    setUploading(true);
    try {
      const extFromMime = (blob.type || '').includes('mp4') ? 'mp4' : 'webm';
      const file = new File([blob], `voice.${extFromMime}`, { type: blob.type || 'audio/webm' });
      const fd = new FormData();
      fd.append('file', file);
      if (convId) fd.append('conv_id', convId);
      if (toUserId) fd.append('to_user_id', toUserId);
      fd.append('duration', String(duration));
      const { data } = await axios.post(`${API}/api/messages/voice`, fd, {
        withCredentials: true,
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      onSent?.(data);
      if (data?.transcription) {
        toast.success('🎤 Message vocal envoyé', { description: `"${data.transcription.slice(0, 80)}"` });
      } else {
        toast.success('🎤 Message vocal envoyé');
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Échec de l\'envoi vocal');
    } finally {
      setUploading(false);
    }
  };

  const fmt = (s) => `${Math.floor(s/60).toString().padStart(2,'0')}:${(s%60).toString().padStart(2,'0')}`;

  if (uploading) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 rounded-xl" data-testid="voice-uploading"
        style={{ background: 'var(--jp-surface-secondary)' }}>
        <div className="w-4 h-4 border-2 border-t-transparent rounded-full animate-spin"
          style={{ borderColor: 'var(--jp-primary)', borderTopColor: 'transparent' }} />
        <span className="text-xs font-['Manrope']" style={{ color: 'var(--jp-text-secondary)' }}>
          Transcription…
        </span>
      </div>
    );
  }

  if (recording) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 rounded-xl jp-animate-fadeIn" data-testid="voice-recording"
        style={{ background: 'rgba(224,28,46,0.08)', border: '1px solid rgba(224,28,46,0.3)' }}>
        <span className="w-2.5 h-2.5 rounded-full animate-pulse" style={{ background: '#E01C2E' }} />
        <span className="text-xs font-['Manrope'] font-semibold" style={{ color: '#E01C2E' }} data-testid="voice-elapsed">
          {fmt(elapsed)}
        </span>
        <button type="button" onClick={cancelRecording} data-testid="voice-cancel"
          className="p-1 rounded-full" style={{ color: 'var(--jp-text-muted)' }}
          aria-label="Annuler">
          <CloseIcon size={14} weight="bold" />
        </button>
        <button type="button" onClick={stopRecording} data-testid="voice-stop"
          className="p-1.5 rounded-full transition-transform hover:scale-110"
          style={{ background: '#E01C2E', color: '#fff' }}
          aria-label="Envoyer">
          <Stop size={14} weight="fill" />
        </button>
      </div>
    );
  }

  return (
    <button type="button" onClick={startRecording} data-testid="voice-record-button"
      className="p-2.5 rounded-xl transition-all"
      style={{ color: 'var(--jp-text-muted)' }}
      onMouseEnter={e => e.currentTarget.style.background = 'var(--jp-surface-secondary)'}
      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
      aria-label="Enregistrer un message vocal">
      <Microphone size={20} weight="bold" />
    </button>
  );
}

/**
 * VoicePlayer — renders a voice message bubble with play/pause + duration + transcription.
 * For messages ≥30s or long transcriptions, shows a "TL;DR" button that calls
 * POST /api/messages/{msg_id}/summarize and renders the 2-line summary inline.
 */
export function VoicePlayer({ media, isMine, msgId, targetLang = 'en' }) {
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [showTranscription, setShowTranscription] = useState(false);
  const [summary, setSummary] = useState(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const audioRef = useRef(null);

  const url = `${API}${media.url}`;
  const duration = media.duration || 0;
  const transcription = media.transcription || '';
  const isLong = duration >= 30 || transcription.length >= 400;

  const toggle = () => {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) { a.play(); setPlaying(true); }
    else { a.pause(); setPlaying(false); }
  };

  const onTime = () => {
    const a = audioRef.current;
    if (!a || !a.duration) return;
    setProgress((a.currentTime / a.duration) * 100);
  };

  const onEnd = () => { setPlaying(false); setProgress(0); };

  const loadSummary = async () => {
    if (summary || summaryLoading || !msgId) return;
    setSummaryLoading(true);
    try {
      const { data } = await axios.post(
        `${API}/api/messages/${msgId}/summarize`,
        { target_lang: targetLang },
        { withCredentials: true }
      );
      setSummary(data);
    } catch {
      // Silent fail — user can retry
    } finally {
      setSummaryLoading(false);
    }
  };

  return (
    <div className="rounded-2xl px-3 py-2.5 max-w-[85%] shadow-sm"
      style={{ background: isMine ? 'var(--jp-primary)' : 'var(--jp-surface-secondary)', color: isMine ? 'white' : 'var(--jp-text)' }}>
      <audio ref={audioRef} src={url} onTimeUpdate={onTime} onEnded={onEnd} preload="metadata" />
      <div className="flex items-center gap-2.5">
        <button type="button" onClick={toggle} data-testid="voice-play-toggle"
          className="w-9 h-9 rounded-full flex items-center justify-center transition-transform hover:scale-105 flex-shrink-0"
          style={{ background: isMine ? 'rgba(255,255,255,0.2)' : 'var(--jp-primary)', color: '#fff' }}
          aria-label={playing ? 'Pause' : 'Play'}>
          {playing ? <Stop size={14} weight="fill" /> : <Microphone size={14} weight="fill" />}
        </button>
        <div className="flex-1 min-w-[110px]">
          <div className="h-1 rounded-full overflow-hidden" style={{ background: isMine ? 'rgba(255,255,255,0.2)' : 'rgba(15,5,107,0.15)' }}>
            <div className="h-full rounded-full transition-all" style={{ width: `${progress}%`, background: isMine ? '#fff' : 'var(--jp-primary)' }} />
          </div>
          <div className="flex items-center justify-between mt-1 gap-2 flex-wrap">
            <span className="text-[10px] font-['Manrope']" style={{ opacity: 0.75 }}>{duration}s</span>
            <div className="flex items-center gap-2">
              {isLong && msgId && !summary && (
                <button type="button" onClick={loadSummary} data-testid={`voice-summary-btn-${msgId}`}
                  className="text-[10px] font-['Manrope'] font-semibold underline"
                  style={{ opacity: 0.85 }} disabled={summaryLoading}>
                  {summaryLoading ? '…' : 'TL;DR'}
                </button>
              )}
              {transcription && (
                <button type="button" onClick={() => setShowTranscription(v => !v)}
                  data-testid="voice-transcription-toggle"
                  className="text-[10px] font-['Manrope'] font-semibold underline"
                  style={{ opacity: 0.85 }}>
                  {showTranscription ? 'Hide' : 'Transcript'}
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
      {summary && (
        <div className="mt-2 pt-2 border-t" data-testid={`voice-summary-${msgId}`}
          style={{ borderColor: isMine ? 'rgba(255,255,255,0.25)' : 'rgba(15,5,107,0.15)' }}>
          <p className="text-[9px] font-['Manrope'] font-semibold uppercase tracking-wide mb-0.5"
            style={{ opacity: 0.7 }}>TL;DR {summary.cached && '· cached'}</p>
          <p className="text-xs font-['Manrope'] leading-relaxed break-words"
            style={{ opacity: 0.95 }}>{summary.summary}</p>
        </div>
      )}
      {showTranscription && transcription && (
        <p className="text-xs font-['Manrope'] mt-2 pt-2 border-t italic break-words"
          data-testid="voice-transcription-text"
          style={{ borderColor: isMine ? 'rgba(255,255,255,0.25)' : 'rgba(15,5,107,0.15)', opacity: 0.9 }}>
          "{transcription}"
        </p>
      )}
    </div>
  );
}
