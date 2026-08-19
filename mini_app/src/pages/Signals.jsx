import { AlertTriangle, RefreshCw, Sparkles } from 'lucide-react';
import { useMemo, useState } from 'react';
import { api, ensureAuth } from '../api/client';
import CooldownOverlay from '../components/CooldownOverlay';
import FilterBar from '../components/FilterBar';
import MarketAnalysisOverlay from '../components/MarketAnalysisOverlay';
import SessionModal from '../components/SessionModal';
import SessionPanel from '../components/SessionPanel';
import SignalCard from '../components/SignalCard';
import SignalResultModal from '../components/SignalResultModal';
import TimeframeModal from '../components/TimeframeModal';
import AssetCategoryPage from './AssetCategoryPage';
import { CATEGORY_META } from '../data/assets';
import { useSession } from '../hooks/useSession';
import { useSignals } from '../hooks/useSignals';
import { useTelegram } from '../hooks/useTelegram';

export default function Signals() {
  const { haptic } = useTelegram();
  const sessionApi = useSession(haptic);
  const [filters, setFilters] = useState({});
  const [assetPage, setAssetPage] = useState(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [timeframeModalOpen, setTimeframeModalOpen] = useState(false);
  const [resultModalOpen, setResultModalOpen] = useState(false);
  const [marked, setMarked] = useState({});
  const [goalBanner, setGoalBanner] = useState(true);
  const [analysisRequested, setAnalysisRequested] = useState(false);
  const [analysisSignal, setAnalysisSignal] = useState(null);
  const [analysisMessage, setAnalysisMessage] = useState('');
  const [analysisError, setAnalysisError] = useState('');
  const [analyzing, setAnalyzing] = useState(false);
  const stableFilters = useMemo(() => filters, [filters]);
  const { signals, loading, error, reload } = useSignals(stableFilters, analysisRequested);

  function clearAnalysisState() {
    setAnalysisRequested(false); setAnalysisSignal(null); setAnalysisMessage(''); setAnalysisError(''); setResultModalOpen(false);
  }
  function updateFilters(next) { setFilters(next); clearAnalysisState(); }
  async function mark(signalId, result) { const response = await sessionApi.markSignal(signalId, result); if (response) { setMarked((value) => ({ ...value, [signalId]: result })); setGoalBanner(true); } }
  function selectAsset(category, asset) { setFilters({ category, asset, timeframe: undefined }); clearAnalysisState(); setAssetPage(null); setTimeframeModalOpen(true); haptic?.selection(); }
  function selectCategory(category) { updateFilters({ category, asset: undefined, timeframe: undefined }); setAssetPage(null); haptic?.selection(); }

  async function takeAnalysis(timeframeOverride = filters.timeframe) {
    const nextFilters = { ...filters, timeframe: timeframeOverride };
    if (!nextFilters.category || !nextFilters.asset || !nextFilters.timeframe || analyzing) return;
    setFilters(nextFilters); setTimeframeModalOpen(false); setResultModalOpen(false); setAnalyzing(true); setAnalysisError(''); setAnalysisMessage('');
    try {
      await ensureAuth();
      const [response] = await Promise.all([
        api.post('/signals/analyze', { category: nextFilters.category, asset: nextFilters.asset, timeframe: nextFilters.timeframe }),
        new Promise((resolve) => window.setTimeout(resolve, 2200))
      ]);
      const { data } = response;
      setAnalysisSignal(data.signal || null); setAnalysisMessage(data.message); setAnalysisRequested(true); setResultModalOpen(data.status === 'SIGNAL' && Boolean(data.signal));
      if (data.status === 'SIGNAL') haptic?.success(); else haptic?.warning();
    } catch (err) {
      setAnalysisError(err.response?.data?.detail || err.message); setResultModalOpen(false); haptic?.warning();
    } finally { setAnalyzing(false); }
  }

  function repeatAnalysis() { if (!filters.timeframe) { setTimeframeModalOpen(true); return; } takeAnalysis(filters.timeframe); }
  function chooseNewPair() { const category = filters.category; clearAnalysisState(); setFilters(category ? { category } : {}); setTimeframeModalOpen(false); setAssetPage(category || null); haptic?.selection(); }

  if (assetPage) return <AssetCategoryPage category={assetPage} selectedAsset={filters.asset} onBack={() => setAssetPage(null)} onSelect={selectAsset} onSelectCategory={selectCategory} />;

  const selectedMarket = filters.category ? CATEGORY_META[filters.category]?.title : 'Все рынки';
  const visibleSignals = analysisSignal ? [analysisSignal, ...signals.filter((signal) => signal.id !== analysisSignal.id)] : signals;

  return (
    <div className="pb-24">
      <SessionPanel session={sessionApi.session} onStart={() => setModalOpen(true)} onEnd={sessionApi.endSession} />
      <FilterBar filters={filters} onChange={updateFilters} onOpenCategory={setAssetPage} />

      {sessionApi.session?.goal_reached && goalBanner && <div className="mx-4 mt-4 rounded-lg border border-terminal-green bg-terminal-green/10 p-4"><div className="font-bold text-terminal-green">Цель достигнута</div><div className="mt-1 text-sm text-terminal-muted">Можно завершить сессию или продолжить торговлю.</div><div className="mt-3 grid grid-cols-2 gap-2"><button onClick={sessionApi.endSession} className="h-10 rounded-lg bg-terminal-green font-bold text-black">Завершить</button><button onClick={() => setGoalBanner(false)} className="h-10 rounded-lg bg-terminal-card font-bold">Продолжить</button></div></div>}

      <main className="space-y-3 px-4 py-4">
        <div className="flex items-center justify-between gap-3"><div><h1 className="text-2xl font-extrabold">Сигналы</h1><p className="mt-1 text-sm text-terminal-muted">{filters.asset ? `${filters.asset} · ${filters.timeframe || 'выбери время'}` : selectedMarket}</p></div><button onClick={reload} className="grid h-11 w-11 place-items-center rounded-lg bg-terminal-card" title="Обновить"><RefreshCw size={19} /></button></div>
        {analysisMessage && <div className="flex items-center gap-3 rounded-lg border border-terminal-green/50 bg-terminal-green/10 p-3 text-sm text-terminal-green"><Sparkles size={18} /> {analysisMessage}</div>}
        {analysisError && <div className="flex gap-3 rounded-lg border border-terminal-red/50 bg-terminal-red/10 p-3 text-sm text-terminal-red"><AlertTriangle size={18} /> {String(analysisError)}</div>}
        {error && <div className="flex gap-3 rounded-lg border border-terminal-red/50 bg-terminal-red/10 p-3 text-sm text-terminal-red"><AlertTriangle size={18} /> {String(error)}</div>}
        {!analysisRequested && !analysisSignal && <div className="rounded-lg border border-terminal-border bg-terminal-card p-4 text-sm leading-6 text-terminal-muted">Сначала выбери актив. После этого откроется окно таймфрейма и AlphaPulse запустит анализ.</div>}
        {loading && <div className="rounded-lg border border-terminal-border bg-terminal-card p-4 text-terminal-muted">Загрузка сигналов...</div>}
        {analysisRequested && !loading && !visibleSignals.length && !error && !analysisError && <div className="rounded-lg border border-terminal-border bg-terminal-card p-4 text-terminal-muted">По выбранному активу сейчас нет активного сигнала. Попробуй другой таймфрейм.</div>}
        {visibleSignals.map((signal) => <SignalCard key={signal.id} signal={signal} disabled={!sessionApi.session || sessionApi.cooldown > 0} marked={marked[signal.id]} onMark={mark} />)}
      </main>

      <CooldownOverlay seconds={sessionApi.cooldown} />
      <TimeframeModal open={timeframeModalOpen && Boolean(filters.asset)} asset={filters.asset} marketTitle={filters.category ? CATEGORY_META[filters.category]?.title : 'Рынок'} selectedTimeframe={filters.timeframe} analyzing={analyzing} onClose={() => setTimeframeModalOpen(false)} onSelect={takeAnalysis} />
      <MarketAnalysisOverlay open={analyzing} asset={filters.asset} timeframe={filters.timeframe} />
      <SignalResultModal open={resultModalOpen && !analyzing} signal={analysisSignal} message={analysisMessage} onRepeat={repeatAnalysis} onNewPair={chooseNewPair} onClose={() => setResultModalOpen(false)} />
      <SessionModal open={modalOpen} onClose={() => setModalOpen(false)} onSubmit={sessionApi.startSession} />
    </div>
  );
}
