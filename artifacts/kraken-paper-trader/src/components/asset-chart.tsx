/**
 * Interactive trading chart — VISUALISATION ONLY.
 * Candlesticks from the same market data the strategy engine uses
 * (Kraken OHLC for crypto, Yahoo COMEX futures for metals), with
 * trade entry/exit markers, open-position levels, indicator overlays
 * and optional strategy-signal points.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  CandlestickSeries, ColorType, HistogramSeries, LineSeries, LineStyle,
  createChart, createSeriesMarkers,
  type IChartApi, type ISeriesApi, type SeriesMarker, type Time, type UTCTimestamp,
} from 'lightweight-charts';
import { Loader2 } from 'lucide-react';
import { useGetChartData, getGetChartDataQueryKey } from '@workspace/api-client-react';
import type { PaperTrade, PaperTraderState } from '@workspace/api-client-react';

type Trade = PaperTrade;
type OpenPosition = NonNullable<PaperTraderState['position']>;

export type ChartRange = '24H' | '7D' | '30D' | '90D';
const RANGES: ChartRange[] = ['24H', '7D', '30D', '90D'];
export type ChartInterval = '15m' | '1h' | '4h';
const INTERVALS: ChartInterval[] = ['15m', '1h', '4h'];
const DEFAULT_INTERVAL: Record<ChartRange, ChartInterval> = { '24H': '15m', '7D': '1h', '30D': '1h', '90D': '4h' };

const CORE_CONDITIONS = ['4h Trend', '1h Trend', 'RSI', 'MACD Momentum', 'Price vs MA', 'Volume'];
const ACTIVE_CONDITIONS = ['15m Trend', '1h Confirmation', 'RSI', 'MACD Momentum', 'Price vs EMA20', 'Volume'];

export interface PotentialSignal {
  strategy: 'CORE' | 'ACTIVE';
  direction: 'LONG' | 'SHORT';
  score: number;
  maxScore: number;
  threshold: number;
}

const UP = '#22c55e';
const DOWN = '#ef4444';
const SHORT_COLOR = '#f97316';
const LONG_COLOR = '#22c55e';

const isTestTrade = (t: { entryMode?: string | null }) =>
  !!t.entryMode && /TEST|DIAGNOSTIC/i.test(t.entryMode);

const fmtMoney = (v: number | null | undefined, currency: string) =>
  v == null ? '—' : `${currency === 'USD' ? '$' : '£'}${v.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const fmtDur = (seconds: number | null | undefined) => {
  if (seconds == null) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
};

/** Bucket an ISO timestamp onto the START time of its containing candle. */
const bucket = (iso: string, intervalSeconds: number): UTCTimestamp =>
  (Math.floor(Date.parse(iso) / 1000 / intervalSeconds) * intervalSeconds) as UTCTimestamp;

interface AssetChartProps {
  asset: string;
  /** Trades for this asset only (already filtered). */
  trades: Trade[];
  position?: OpenPosition | null;
  /** Open position held by the parallel ACTIVE (15m) strategy, if any. */
  activePosition?: OpenPosition | null;
  currentPrice?: number | null;
  defaultRange?: ChartRange;
  /** Trade-review mode: centre the view on this trade. */
  focusTrade?: Trade | null;
  /** Live "considering an entry" markers, shown distinct from executed trades. */
  potentialSignals?: PotentialSignal[];
}

export function AssetChart({ asset, trades, position, activePosition, currentPrice, defaultRange = '7D', focusTrade, potentialSignals }: AssetChartProps) {
  const [range, setRange] = useState<ChartRange>(defaultRange);
  const [interval, setInterval] = useState<ChartInterval>(DEFAULT_INTERVAL[defaultRange]);
  const [showEma20, setShowEma20] = useState(true);
  const [showEma50, setShowEma50] = useState(true);
  const [showVolume, setShowVolume] = useState(false);
  const [showRsi, setShowRsi] = useState(false);
  const [showMacd, setShowMacd] = useState(false);
  const [showSignals, setShowSignals] = useState(false);
  const [selectedTradeId, setSelectedTradeId] = useState<number | null>(focusTrade?.id ?? null);
  const [selectedSignalTs, setSelectedSignalTs] = useState<number | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  const query = useGetChartData(
    { asset: asset as never, range, interval },
    { query: { queryKey: getGetChartDataQueryKey({ asset: asset as never, range, interval }), staleTime: 60_000, refetchInterval: 120_000 } },
  );
  const data = query.data;
  const currency = data?.currency ?? (asset === 'GOLD' || asset === 'SILVER' ? 'USD' : 'GBP');

  // trades that fall inside the loaded candle window
  const visibleTrades = useMemo(() => {
    if (!data || data.candles.length === 0) return [];
    const first = data.candles[0].t * 1000;
    return trades.filter((t) => Date.parse(t.closedAt) >= first);
  }, [data, trades]);

  const selectedTrade = useMemo(
    () => visibleTrades.find((t) => t.id === selectedTradeId) ?? (focusTrade ?? null),
    [visibleTrades, selectedTradeId, focusTrade],
  );
  // Trade-review limitation: chart data is a rolling market window (max 90D),
  // so very old trades may fall outside the retrievable candles.
  const focusOutOfRange = useMemo(() => {
    if (!focusTrade || !data || data.candles.length === 0) return false;
    return Date.parse(focusTrade.openedAt) / 1000 < data.candles[0].t;
  }, [focusTrade, data]);

  const selectedSignal = useMemo(
    () => (selectedSignalTs != null && data ? data.signals.find((s) => s.ts === selectedSignalTs) ?? null : null),
    [selectedSignalTs, data],
  );

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !data || data.candles.length === 0) return;

    const interval = data.intervalSeconds;
    const chart = createChart(el, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: 'hsl(215 15% 55%)',
        fontSize: 11,
        panes: { separatorColor: 'hsl(215 25% 88%)' },
      },
      grid: {
        vertLines: { color: 'hsl(215 25% 92%)' },
        horzLines: { color: 'hsl(215 25% 92%)' },
      },
      timeScale: { timeVisible: true, secondsVisible: false, borderVisible: false },
      rightPriceScale: { borderVisible: false },
      crosshair: { mode: 0 },
      handleScroll: true,
      handleScale: true,
      autoSize: true,
    });
    chartRef.current = chart;

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: UP, downColor: DOWN, borderUpColor: UP, borderDownColor: DOWN,
      wickUpColor: UP, wickDownColor: DOWN,
      priceFormat: { type: 'price', precision: 4, minMove: 0.0001 },
    });
    candleSeries.setData(data.candles.map((c) => ({
      time: c.t as UTCTimestamp, open: c.o, high: c.h, low: c.l, close: c.c,
    })));

    if (showEma20) {
      const s = chart.addSeries(LineSeries, { color: '#3b82f6', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      s.setData(data.candles.filter((c) => c.ema20 != null).map((c) => ({ time: c.t as UTCTimestamp, value: c.ema20 as number })));
    }
    if (showEma50) {
      const s = chart.addSeries(LineSeries, { color: '#a855f7', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
      s.setData(data.candles.filter((c) => c.ema50 != null).map((c) => ({ time: c.t as UTCTimestamp, value: c.ema50 as number })));
    }
    if (showVolume) {
      const s = chart.addSeries(HistogramSeries, {
        priceScaleId: 'volume', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false,
      });
      chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      s.setData(data.candles.map((c) => ({
        time: c.t as UTCTimestamp, value: c.v, color: c.c >= c.o ? 'rgba(34,197,94,0.4)' : 'rgba(239,68,68,0.4)',
      })));
    }

    // sub-panes: RSI and MACD below the price chart
    let paneIndex = 1;
    if (showRsi) {
      const s = chart.addSeries(LineSeries, { color: '#eab308', lineWidth: 1, priceLineVisible: false }, paneIndex);
      s.setData(data.candles.filter((c) => c.rsi != null).map((c) => ({ time: c.t as UTCTimestamp, value: c.rsi as number })));
      s.createPriceLine({ price: 50, color: 'hsl(215 15% 70%)', lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: '' });
      paneIndex += 1;
    }
    if (showMacd) {
      const m = chart.addSeries(LineSeries, { color: '#06b6d4', lineWidth: 1, priceLineVisible: false }, paneIndex);
      m.setData(data.candles.filter((c) => c.macd != null).map((c) => ({ time: c.t as UTCTimestamp, value: c.macd as number })));
      const sig = chart.addSeries(LineSeries, { color: '#f43f5e', lineWidth: 1, priceLineVisible: false }, paneIndex);
      sig.setData(data.candles.filter((c) => c.macdSignal != null).map((c) => ({ time: c.t as UTCTimestamp, value: c.macdSignal as number })));
    }
    // keep the price pane dominant
    const panes = chart.panes();
    if (panes.length > 1) {
      panes[0].setHeight(Math.max(220, el.clientHeight - (panes.length - 1) * 70));
    }

    const candleTimes = new Set(data.candles.map((c) => c.t));
    const snap = (iso: string): UTCTimestamp | null => {
      const t = bucket(iso, interval);
      return candleTimes.has(t) ? t : null;
    };

    // ── trade markers ────────────────────────────────────────────────────
    const markers: (SeriesMarker<Time> & { _kind?: string; _id?: number; _ts?: number })[] = [];
    for (const t of visibleTrades) {
      const test = isTestTrade(t);
      const entryTime = snap(t.openedAt);
      const exitTime = snap(t.closedAt);
      const isLong = t.direction === 'LONG';
      const strat = (t.strategy ?? 'CORE').toUpperCase();
      if (entryTime != null) {
        markers.push({
          time: entryTime,
          position: isLong ? 'belowBar' : 'aboveBar',
          shape: isLong ? 'arrowUp' : 'arrowDown',
          color: isLong ? LONG_COLOR : SHORT_COLOR,
          text: `${test ? 'TEST ' : ''}${strat} ${isLong ? 'LONG' : 'SHORT'}`,
          _kind: 'trade', _id: t.id,
        });
      }
      if (exitTime != null) {
        const reason = (t.exitReason ?? '').toUpperCase();
        const label = reason.includes('TAKE_PROFIT') || reason === 'TP' ? 'TP'
          : reason.includes('STOP_LOSS') || reason === 'SL' ? 'SL' : 'EXIT';
        markers.push({
          time: exitTime,
          position: isLong ? 'aboveBar' : 'belowBar',
          shape: label === 'TP' ? 'circle' : label === 'SL' ? 'square' : 'circle',
          color: label === 'TP' ? '#16a34a' : label === 'SL' ? '#dc2626' : '#64748b',
          text: `${test ? 'TEST ' : ''}${strat} ${label}`,
          _kind: 'trade', _id: t.id,
        });
      }
      // entry → exit connector
      if (entryTime != null && exitTime != null && exitTime > entryTime) {
        const line = chart.addSeries(LineSeries, {
          color: t.profitLoss >= 0 ? 'rgba(34,197,94,0.65)' : 'rgba(239,68,68,0.65)',
          lineWidth: t.id === selectedTradeId ? 3 : 1,
          lineStyle: LineStyle.Dashed,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        });
        line.setData([
          { time: entryTime, value: t.entry },
          { time: exitTime, value: t.exit },
        ]);
      }
    }

    // ── signal-only points ───────────────────────────────────────────────
    if (showSignals && data.signals.length > 0) {
      for (const s of data.signals) {
        const t = (Math.floor(s.ts / interval) * interval) as UTCTimestamp;
        if (!candleTimes.has(t)) continue;
        if (s.executed) continue; // executed signals already appear as trades
        markers.push({
          time: t,
          position: s.direction === 'SHORT' ? 'aboveBar' : 'belowBar',
          shape: 'circle',
          color: '#94a3b8',
          text: `${s.direction ?? ''} SIGNAL`.trim(),
          _kind: 'signal', _ts: s.ts,
        });
      }
    }

    // ── live "potential entry" markers (visually distinct: hollow amber) ──
    if (potentialSignals && potentialSignals.length > 0 && data.candles.length > 0) {
      const lastT = data.candles[data.candles.length - 1].t as UTCTimestamp;
      for (const p of potentialSignals) {
        markers.push({
          time: lastT,
          position: p.direction === 'SHORT' ? 'aboveBar' : 'belowBar',
          shape: 'circle',
          color: '#f59e0b',
          text: `POTENTIAL ${p.strategy} ${p.direction} ${p.score}/${p.maxScore}`,
          _kind: 'potential',
        });
      }
    }

    markers.sort((a, b) => (a.time as number) - (b.time as number));
    createSeriesMarkers(candleSeries, markers);

    // ── current market price line ────────────────────────────────────────
    const livePrice = data.currentPrice ?? currentPrice;
    if (livePrice != null) {
      candleSeries.createPriceLine({
        price: livePrice, color: '#0ea5e9', lineWidth: 1, lineStyle: LineStyle.Dotted, title: 'PRICE',
      });
    }

    // ── open position levels (CORE + ACTIVE) ────────────────────────────
    for (const [label, pos] of [['CORE', position], ['ACTIVE', activePosition]] as const) {
      if (!pos) continue;
      candleSeries.createPriceLine({ price: pos.entry, color: '#3b82f6', lineWidth: 1, lineStyle: LineStyle.Solid, title: `${label} ENTRY ${pos.direction}` });
      candleSeries.createPriceLine({ price: pos.stopLoss, color: '#dc2626', lineWidth: 1, lineStyle: LineStyle.Dashed, title: `${label} SL` });
      candleSeries.createPriceLine({ price: pos.takeProfit, color: '#16a34a', lineWidth: 1, lineStyle: LineStyle.Dashed, title: `${label} TP` });
    }

    // ── click → select marker (trade details / signal reason) ───────────
    chart.subscribeClick((param) => {
      if (param.time == null) return;
      const hit = markers.filter((m) => m.time === param.time);
      const tradeHit = hit.find((m) => m._kind === 'trade');
      if (tradeHit?._id != null) {
        setSelectedTradeId(tradeHit._id);
        setSelectedSignalTs(null);
        return;
      }
      const sigHit = hit.find((m) => m._kind === 'signal');
      if (sigHit?._ts != null) {
        setSelectedSignalTs(sigHit._ts);
        setSelectedTradeId(null);
      }
    });

    // ── initial view ─────────────────────────────────────────────────────
    if (focusTrade && candleTimes.has(bucket(focusTrade.openedAt, interval))) {
      const from = bucket(focusTrade.openedAt, interval) - 24 * interval;
      const to = bucket(focusTrade.closedAt, interval) + 24 * interval;
      chart.timeScale().setVisibleRange({ from: from as UTCTimestamp, to: to as UTCTimestamp });
      // focus-trade stop/target levels
      candleSeries.createPriceLine({ price: focusTrade.entry, color: '#3b82f6', lineWidth: 1, lineStyle: LineStyle.Solid, title: 'ENTRY' });
      candleSeries.createPriceLine({ price: focusTrade.stopLoss, color: '#dc2626', lineWidth: 1, lineStyle: LineStyle.Dashed, title: 'STOP' });
      candleSeries.createPriceLine({ price: focusTrade.takeProfit, color: '#16a34a', lineWidth: 1, lineStyle: LineStyle.Dashed, title: 'TARGET' });
      candleSeries.createPriceLine({ price: focusTrade.exit, color: '#64748b', lineWidth: 1, lineStyle: LineStyle.Dotted, title: 'EXIT' });
    } else {
      chart.timeScale().fitContent();
    }

    return () => {
      chartRef.current = null;
      chart.remove();
    };
  }, [data, visibleTrades, position, activePosition, currentPrice, potentialSignals, showEma20, showEma50, showVolume, showRsi, showMacd, showSignals, focusTrade, selectedTradeId]);

  const toggle = (label: string, on: boolean, set: (v: boolean) => void, dotClass?: string) => (
    <button
      key={label}
      type="button"
      onClick={() => set(!on)}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide transition-colors ${
        on ? 'border-primary/50 bg-primary/10 text-primary' : 'border-border/70 bg-transparent text-muted-foreground'
      }`}
    >
      {dotClass ? <span className={`h-1.5 w-1.5 rounded-full ${dotClass}`} /> : null}
      {label}
    </button>
  );

  const passed = (conditions: string | null | undefined) =>
    (conditions ?? '').split(',').map((s) => s.trim()).filter(Boolean);

  return (
    <div className="space-y-2">
      {/* range + toggles */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex overflow-hidden rounded-lg border border-border/70">
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => { setRange(r); setInterval(DEFAULT_INTERVAL[r]); }}
                className={`px-2.5 py-1 text-[11px] font-semibold ${range === r ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'}`}
              >
                {r}
              </button>
            ))}
          </div>
          <div className="inline-flex overflow-hidden rounded-lg border border-border/70" data-testid={`chart-interval-${asset}`}>
            {INTERVALS.map((iv) => (
              <button
                key={iv}
                type="button"
                onClick={() => setInterval(iv)}
                className={`px-2.5 py-1 text-[11px] font-semibold ${interval === iv ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted'}`}
              >
                {iv}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap gap-1">
          {toggle('EMA20', showEma20, setShowEma20, 'bg-blue-500')}
          {toggle('EMA50', showEma50, setShowEma50, 'bg-purple-500')}
          {toggle('VOL', showVolume, setShowVolume)}
          {toggle('RSI', showRsi, setShowRsi, 'bg-yellow-500')}
          {toggle('MACD', showMacd, setShowMacd, 'bg-cyan-500')}
          {toggle('Signals', showSignals, setShowSignals)}
        </div>
      </div>

      {focusOutOfRange && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-600">
          This trade opened before the oldest retrievable candle (charts cover up to 90 days of market data), so its entry may not be visible on the chart. Trade details below are unaffected.
        </div>
      )}

      {/* chart */}
      <div className="relative h-[300px] w-full touch-pan-x touch-pan-y sm:h-[360px]" ref={containerRef}>
        {query.isLoading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        )}
        {query.isError && (
          <div className="absolute inset-0 z-10 flex items-center justify-center rounded-lg border border-destructive/30 bg-destructive/5 text-xs text-destructive">
            Unable to load chart market data — no prices are invented. Try again shortly.
          </div>
        )}
      </div>
      {data && (
        <p className="text-[10px] leading-tight text-muted-foreground">
          {data.interval ?? (data.intervalSeconds === 14400 ? '4h' : data.intervalSeconds === 900 ? '15m' : '1h')} candles · UTC · {data.dataSource}
          {data.currentPrice != null ? ` · price ${fmtMoney(data.currentPrice, currency)}` : ''}
          {position ? ` · CORE ${position.direction} open — unrealised ${fmtMoney(position.unrealisedPnl, currency)}` : ''}
          {activePosition ? ` · ACTIVE ${activePosition.direction} open — unrealised ${fmtMoney(activePosition.unrealisedPnl, currency)}` : ''}
        </p>
      )}

      {/* selected trade details */}
      {selectedTrade && (
        <div className="rounded-xl border border-border/70 bg-muted/30 p-3 text-xs">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="font-bold">
              {selectedTrade.coin} {selectedTrade.direction}
              <span className={`ml-1.5 rounded px-1 py-0.5 text-[9px] font-bold ${(selectedTrade.strategy ?? 'CORE') === 'ACTIVE' ? 'bg-cyan-500/20 text-cyan-600' : 'bg-blue-500/15 text-blue-600'}`}>
                {selectedTrade.strategy ?? 'CORE'}
              </span>
              {isTestTrade(selectedTrade) && <span className="ml-1.5 rounded bg-amber-500/20 px-1 py-0.5 text-[9px] font-bold text-amber-600">TEST</span>}
            </span>
            <button type="button" className="text-muted-foreground" onClick={() => setSelectedTradeId(null)}>✕</button>
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
            <span>Entry: <b>{fmtMoney(selectedTrade.entry, currency)}</b></span>
            <span>Exit: <b>{fmtMoney(selectedTrade.exit, currency)}</b></span>
            <span>P&amp;L: <b className={selectedTrade.profitLoss >= 0 ? 'text-green-600' : 'text-red-600'}>{fmtMoney(selectedTrade.profitLoss, currency)}</b></span>
            <span>P&amp;L %: <b>{selectedTrade.pnlPct != null ? `${selectedTrade.pnlPct.toFixed(2)}%` : '—'}</b></span>
            <span>R multiple: <b>{selectedTrade.rMultiple != null ? selectedTrade.rMultiple.toFixed(2) : '—'}</b></span>
            <span>Duration: <b>{fmtDur(selectedTrade.durationSeconds)}</b></span>
            <span>Stop: <b>{fmtMoney(selectedTrade.stopLoss, currency)}</b></span>
            <span>Target: <b>{fmtMoney(selectedTrade.takeProfit, currency)}</b></span>
            <span>Exit reason: <b>{selectedTrade.exitReason}</b></span>
          </div>
          {selectedTrade.passCount != null && (
            <div className="mt-2 border-t border-border/60 pt-2">
              <p className="mb-1 font-semibold">Entry conditions — {selectedTrade.passCount}/6 conditions met</p>
              <div className="flex flex-wrap gap-1">
                {((selectedTrade.strategy ?? 'CORE') === 'ACTIVE' ? ACTIVE_CONDITIONS : CORE_CONDITIONS).map((c) => {
                  const ok = passed(selectedTrade.entryConditions).includes(c);
                  return (
                    <span key={c} className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${ok ? 'bg-green-500/15 text-green-600' : 'bg-red-500/10 text-red-500'}`}>
                      {ok ? '✓' : '✕'} {c}
                    </span>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* selected blocked-signal details */}
      {selectedSignal && (
        <div className="rounded-xl border border-border/70 bg-muted/30 p-3 text-xs">
          <div className="mb-1 flex items-center justify-between">
            <span className="font-bold">{selectedSignal.direction ?? ''} SIGNAL — NOT EXECUTED</span>
            <button type="button" className="text-muted-foreground" onClick={() => setSelectedSignalTs(null)}>✕</button>
          </div>
          <p>{new Date(selectedSignal.ts * 1000).toUTCString()}</p>
          <p className="mt-1 text-muted-foreground">Blocked: {selectedSignal.blockedReason ?? 'reason not recorded'}</p>
        </div>
      )}
    </div>
  );
}
