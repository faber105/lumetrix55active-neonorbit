import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ensureAuth } from '../api/client';

export function useSession(haptic) {
  const [session, setSession] = useState(null); const [history, setHistory] = useState([]); const [stats, setStats] = useState(null); const [cooldown, setCooldown] = useState(0); const cooldownTimer = useRef(null);
  const startCooldown = useCallback((seconds) => { setCooldown(seconds); window.clearInterval(cooldownTimer.current); cooldownTimer.current = window.setInterval(() => { setCooldown((value) => { if (value <= 1) { window.clearInterval(cooldownTimer.current); haptic?.success(); return 0; } return value - 1; }); }, 1000); }, [haptic]);
  const loadSession = useCallback(async () => { await ensureAuth(); const { data } = await api.get('/sessions/active'); setSession(data); }, []);
  const loadHistory = useCallback(async () => { await ensureAuth(); const [historyRes, statsRes] = await Promise.all([api.get('/sessions/history'),api.get('/sessions/stats')]); setHistory(historyRes.data); setStats(statsRes.data); }, []);
  const startSession = useCallback(async (payload) => { await ensureAuth(); const { data } = await api.post('/sessions/start', payload); setSession(data); haptic?.success(); return data; }, [haptic]);
  const endSession = useCallback(async () => { if (!session) return null; const { data } = await api.post('/sessions/end', { session_id: session.id }); setSession(null); await loadHistory(); haptic?.selection(); return data; }, [haptic, loadHistory, session]);
  const markSignal = useCallback(async (signalId, result) => { if (!session || cooldown > 0) return null; const shouldCooldown = session.total_trades > 0; try { const { data } = await api.post('/sessions/mark', {session_id: session.id, signal_id: signalId, result}); setSession(data.session); if (shouldCooldown) startCooldown(15); haptic?.success(); return data; } catch (err) { const remaining = err.response?.data?.detail?.cooldown_remaining; if (remaining) startCooldown(remaining); haptic?.warning(); throw err; } }, [cooldown, haptic, session, startCooldown]);
  useEffect(() => { loadSession().catch(() => {}); loadHistory().catch(() => {}); return () => window.clearInterval(cooldownTimer.current); }, [loadHistory, loadSession]);
  return { session, history, stats, cooldown, loadHistory, startSession, endSession, markSignal };
}
