/**
 * TasksContext — lightweight global store for "Mes tâches" badge + sync.
 *
 * Responsibilities:
 *   - On login, fetch /api/tasks/my/count and keep it up to date
 *   - Listen to Socket.io message_updated + new_message and refresh counts
 *   - Expose { pending, done, total, refresh }
 *
 * Used by the sidebar badge + the MyTasksPage header badge.
 */
import { createContext, useContext, useEffect, useRef, useState, useCallback } from 'react';
import axios from 'axios';
import io from 'socket.io-client';
import { useAuth } from '@/context/AuthContext';

const API = process.env.REACT_APP_BACKEND_URL;
const Ctx = createContext({ pending: 0, done: 0, total: 0, refresh: () => {} });

export function TasksProvider({ children }) {
  const { user } = useAuth();
  const [state, setState] = useState({ pending: 0, done: 0, total: 0 });
  const sockRef = useRef(null);
  const timerRef = useRef(null);

  const refresh = useCallback(async () => {
    if (!user) return;
    try {
      const { data } = await axios.get(`${API}/api/tasks/my/count`,
        { withCredentials: true });
      setState({
        pending: data.pending || 0,
        done: data.done || 0,
        total: data.total || 0,
      });
    } catch { /* silent */ }
  }, [user]);

  // Initial fetch + poll every 5 min as a safety net
  useEffect(() => {
    if (!user) return;
    refresh();
    timerRef.current = setInterval(refresh, 5 * 60 * 1000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, [user, refresh]);

  // Debounced realtime refresh on relevant socket events
  useEffect(() => {
    if (!user) return;
    const sock = io(API, { path: '/api/socket.io', reconnection: true });
    sockRef.current = sock;
    let pending = null;
    const kick = () => {
      if (pending) clearTimeout(pending);
      pending = setTimeout(refresh, 400);
    };
    sock.on('message_updated', kick);
    sock.on('new_message', (msg) => {
      if (msg?.message_type === 'call_summary') kick();
    });
    sock.on('notification', (n) => {
      if (n?.type === 'call_task_assigned' || n?.type === 'call_task_reassigned') kick();
    });
    return () => { try { sock.disconnect(); } catch {} };
  }, [user, refresh]);

  return <Ctx.Provider value={{ ...state, refresh }}>{children}</Ctx.Provider>;
}

export const useTasks = () => useContext(Ctx);
