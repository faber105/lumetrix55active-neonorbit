import { useCallback, useEffect, useState } from 'react';
import { api, ensureAuth } from '../api/client';

export function useSignals(filters, enabled = true) {
  const [signals, setSignals] = useState([]); const [loading, setLoading] = useState(Boolean(enabled)); const [error, setError] = useState('');
  const load = useCallback(async () => { if (!enabled) { setSignals([]); setLoading(false); setError(''); return []; } setLoading(true); try { await ensureAuth(); const { data } = await api.get('/signals/active', { params: filters }); setSignals(data); setError(''); return data; } catch (err) { setError(err.response?.data?.detail || err.message); return []; } finally { setLoading(false); } }, [enabled, filters]);
  useEffect(() => { if (!enabled) { setSignals([]); setLoading(false); setError(''); return undefined; } load(); const timer = window.setInterval(load, 15000); return () => window.clearInterval(timer); }, [enabled, load]);
  return { signals, loading, error, reload: load };
}
