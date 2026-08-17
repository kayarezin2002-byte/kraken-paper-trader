import { useEffect, useState, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle, ArrowDownRight, ArrowUpRight, BarChart3, Clock3,
  RefreshCw, RotateCcw, ShieldAlert, Target, TrendingDown, TrendingUp,
  Wallet, Zap, ChevronDown, ChevronUp,
} from 'lucide-react';
import {
  getGetMultiCoinStateQueryKey,
  getListActivityLogQueryKey,
  useGetMultiCoinState,
  useRefreshMultiCoin,
  useResetAllCoins,
  useListActivityLog,
  type PaperTraderState,
} from '@workspace/api-client-react';
import { TradingShell } from '@/components/trading-shell';
import { StrategyConditionsPanel } from '@/components/strategy-conditions';

// ─── Formatters ──────────────────────────────────────────────────────────────
const money = (v: number | null | undefined, d = 2) =>
  v == null ? '—' : `£${v.toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const num = (v: number | null | undefined, d = 2) =>
  v == null ? '—' : v.toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d });
const time = (v?: string | null) =>
  v ? new Date(v).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '—';
const dateTime = (v?: string | null) =>
  v ? new Date(v).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—';
const pct = (v: number) => `${v >= 0 ? '+' : ''}${num(v)}%`;

// ─── Coin visual identity ─────────────────────────────────────────────────────
const COIN_META: Record<string, { label: string; accent: string; border: string; bg: string }> = {
  BTC: { label: 'Bitcoin',  accent: 'text-amber-400',  border: 'border-amber-500/20',   bg: 'bg-amber-500/5'  },
  ETH: { label: 'Ethereum', accent: 'text-violet-400', border: 'border-violet-500/20',  bg: 'bg-violet-500/5' },
  SOL: { label: 'Solana',   accent: 'text-green-400',  border: 'border-green-500/20',   bg: 'bg-green-500/5'  },
  XRP: { label: 'XRP',      accent: 'text-blue-400',   border: 'border-blue-500/20',    bg: 'bg-blue-500/5'   },
};

// ─── Countdown hook ───────────────────────────────────────────────────────────
function useCountdown(intervalSecs: number, triggerRef: React.MutableRefObject<number>) {
  const [secs, setSecs] = useState(intervalSecs);
  useEffect(() => {
    setSecs(intervalSecs);
  }, [triggerRef.current]);
  useEffect(() => {
    const id = window.setInterval(() => {
      setSecs((s) => {
        if (s <= 1) return intervalSecs;
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(id);
  }, [intervalSecs]);
  return secs;
}

// ─── Skeleton ─────────────────────────────────────────────────────────────────
function Skeleton() {
  return (
    <TradingShell eyebrow="Live desk" title="Portfolio dashboard" subtitle="BTC · ETH · SOL · XRP — simulated accounts only.">
      <div className="space-y-6">
        <div className="grid gap-3 sm:grid-cols-4">
          {[1,2,3,4].map((i) => <div key={i} className="h-24 rounded-2xl skeleton-shimmer" />)}
        </div>
        <div className="grid gap-5 lg:grid-cols-2">
          {[1,2,3,4].map((i) => <div key={i} className="h-[420px] rounded-2xl skeleton-shimmer" />)}
        </div>
      </div>
    </TradingShell>
  );
}

// ─── Error panel ──────────────────────────────────────────────────────────────
function ErrorPanel({ onRetry }: { onRetry: () => void }) {
  return (
    <TradingShell eyebrow="Live desk" title="Portfolio dashboard" subtitle="Could not read market data.">
      <div className="grid min-h-[55vh] place-items-center">
        <div className="max-w-md rounded-2xl border border-destructive/25 bg-card p-8 text-center shadow-sm">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-destructive/10 text-destructive"><AlertTriangle size={23} /></div>
          <h2 className="text-lg font-extrabold">The desk is temporarily quiet</h2>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">Could not load multi-coin state. Your virtual accounts are safe.</p>
          <button onClick={onRetry} className="mt-6 inline-flex items-center gap-2 rounded-lg bg-sidebar px-4 py-2.5 text-sm font-bold text-white transition-transform hover:-translate-y-0.5">
            <RefreshCw size={15} /> Try again
          </button>
        </div>
      </div>
    </TradingShell>
  );
}

// ─── Open position panel ──────────────────────────────────────────────────────
function PositionPanel({ position, coin }: { position: NonNullable<PaperTraderState['position']>; coin: string }) {
  const meta = COIN_META[coin] ?? COIN_META.BTC;
  const pnl = position.unrealisedPnl ?? null;
  const pnlPct = position.unrealisedPct ?? null;
  const isLong = position.direction === 'LONG';
  return (
    <div className={`rounded-xl border p-3 ${meta.border} ${meta.bg}`}>
      <div className="mb-2 flex items-center justify-between">
        <span className={`font-mono-data text-[10px] font-bold uppercase tracking-wider ${meta.accent}`}>
          {position.direction} position open
        </span>
        {pnl != null && (
          <span className={`font-mono-data text-[11px] font-semibold ${pnl >= 0 ? 'text-accent' : 'text-destructive'}`}>
            {pnl >= 0 ? '+' : ''}{money(pnl)} ({pnlPct != null ? pct(pnlPct) : '—'})
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[10px]">
        <div><span className="text-muted-foreground">Entry</span><span className="ml-2 font-mono-data">{money(position.entry, 4)}</span></div>
        <div><span className="text-muted-foreground">Current</span><span className="ml-2 font-mono-data">{position.currentPrice ? money(position.currentPrice, 4) : '—'}</span></div>
        <div><span className="text-muted-foreground">Stop</span><span className="ml-2 font-mono-data text-destructive/80">{money(position.stopLoss, 4)}</span></div>
        <div><span className="text-muted-foreground">Target</span><span className="ml-2 font-mono-data text-accent">{money(position.takeProfit, 4)}</span></div>
        <div><span className="text-muted-foreground">Risk</span><span className="ml-2 font-mono-data">{money(position.riskAmount)}</span></div>
        <div><span className="text-muted-foreground">Opened</span><span className="ml-2 font-mono-data">{time(position.openedAt)}</span></div>
      </div>
    </div>
  );
}

// ─── Coin card ────────────────────────────────────────────────────────────────
function CoinCard({ coin, state }: { coin: string; state: PaperTraderState }) {
  const meta = COIN_META[coin] ?? COIN_META.BTC;
  const [expanded, setExpanded] = useState(false);
  const positivePnl = state.metrics.totalProfitLoss >= 0;
  const hasPosition = state.position != null;

  return (
    <div className="rise-in flex flex-col overflow-hidden rounded-2xl border border-border/80 bg-card shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)]">
      {/* Header */}
      <div className={`border-b border-border/70 px-5 py-4`}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <span className={`font-mono-data text-[11px] font-bold uppercase tracking-[0.2em] ${meta.accent}`}>{coin}</span>
              <span className="text-[10px] text-muted-foreground">{state.market.pair}</span>
            </div>
            <p className={`mt-1 font-mono-data text-2xl font-medium tracking-[-0.06em] ${meta.accent}`}>
              {money(state.market.currentPrice, state.market.currentPrice != null && state.market.currentPrice < 10 ? 4 : 2)}
            </p>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono-data text-[10px] font-medium uppercase tracking-wider
              ${state.signal === 'LONG' ? 'bg-accent/10 text-accent' : state.signal === 'SHORT' ? 'bg-destructive/10 text-destructive' : 'bg-muted text-muted-foreground'}`}>
              {state.signal === 'LONG' ? <ArrowUpRight size={11} /> : state.signal === 'SHORT' ? <ArrowDownRight size={11} /> : null}
              {state.signal.replace('_', ' ')}
            </span>
            <span className="font-mono-data text-[10px] text-muted-foreground/60">updated {time(state.market.updatedAt)}</span>
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 space-y-4 px-5 py-4">
        {/* Strategy conditions */}
        <StrategyConditionsPanel
          conditions={state.strategyConditions}
          proposedTrade={state.proposedTrade}
          hasPosition={hasPosition}
          botStatus={state.botStatus}
          signal={state.signal}
          compact
        />

        {/* Open position summary */}
        {hasPosition && state.position && (
          <PositionPanel position={state.position} coin={coin} />
        )}
      </div>

      {/* Footer metrics */}
      <div className="border-t border-border/70 px-5 py-3">
        <div className="grid grid-cols-3 gap-3 text-[10px]">
          <div>
            <p className="uppercase tracking-[0.12em] text-muted-foreground">Balance</p>
            <p className="mt-0.5 font-mono-data font-semibold">{money(state.metrics.virtualBalance)}</p>
          </div>
          <div>
            <p className="uppercase tracking-[0.12em] text-muted-foreground">P&L</p>
            <p className={`mt-0.5 font-mono-data font-semibold ${positivePnl ? 'text-accent' : 'text-destructive'}`}>
              {state.metrics.totalProfitLoss >= 0 ? '+' : ''}{money(state.metrics.totalProfitLoss)}
            </p>
          </div>
          <div>
            <p className="uppercase tracking-[0.12em] text-muted-foreground">Trades</p>
            <p className="mt-0.5 font-mono-data font-semibold">
              {num(state.metrics.numberOfTrades, 0)} <span className="text-accent">{num(state.metrics.wins, 0)}W</span>/<span className="text-destructive">{num(state.metrics.losses, 0)}L</span>
            </p>
          </div>
        </div>

        {/* Expand toggle for indicators */}
        <button
          onClick={() => setExpanded((e) => !e)}
          className="mt-2 flex w-full items-center justify-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60 transition-colors hover:text-muted-foreground"
        >
          {expanded ? <><ChevronUp size={11} /> Less</> : <><ChevronDown size={11} /> Indicators</>}
        </button>

        {expanded && (
          <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-border/50 pt-3 text-[10px]">
            <div><span className="text-muted-foreground">RSI</span><span className="ml-2 font-mono-data">{num(state.indicators.rsi)}</span></div>
            <div><span className="text-muted-foreground">MACD</span><span className="ml-2 font-mono-data">{num(state.indicators.macd, 4)}</span></div>
            <div><span className="text-muted-foreground">EMA 20</span><span className="ml-2 font-mono-data">{money(state.indicators.ema20, 4)}</span></div>
            <div><span className="text-muted-foreground">EMA 50</span><span className="ml-2 font-mono-data">{money(state.indicators.ema50, 4)}</span></div>
            <div><span className="text-muted-foreground">ATR</span><span className="ml-2 font-mono-data">{num(state.indicators.atr, 4)}</span></div>
            <div><span className="text-muted-foreground">1h trend</span><span className={`ml-2 font-mono-data ${state.oneHourTrend === 'BULLISH' ? 'text-accent' : state.oneHourTrend === 'BEARISH' ? 'text-destructive' : 'text-muted-foreground'}`}>{state.oneHourTrend}</span></div>
            <div><span className="text-muted-foreground">4h trend</span><span className={`ml-2 font-mono-data ${state.fourHourTrend === 'BULLISH' ? 'text-accent' : state.fourHourTrend === 'BEARISH' ? 'text-destructive' : 'text-muted-foreground'}`}>{state.fourHourTrend}</span></div>
            <div><span className="text-muted-foreground">Win rate</span><span className="ml-2 font-mono-data">{num(state.metrics.winRate)}%</span></div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Open positions summary section ──────────────────────────────────────────
function OpenPositionsSummary({ coins }: { coins: Record<string, PaperTraderState> }) {
  const openPositions = Object.entries(coins).filter(([, s]) => s.position != null);
  if (openPositions.length === 0) return null;

  return (
    <section className="rise-in rise-in-delay-1 overflow-hidden rounded-2xl border border-border/80 bg-card shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)]">
      <div className="flex items-center gap-2 border-b border-border/70 px-5 py-4 sm:px-6">
        <Target size={16} className="text-primary" />
        <h2 className="text-sm font-extrabold">Open positions</h2>
        <span className="ml-auto font-mono-data text-[10px] uppercase tracking-wider text-muted-foreground">{openPositions.length} active</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[700px] text-left">
          <thead>
            <tr className="border-b border-border/70 text-[10px] uppercase tracking-[0.13em] text-muted-foreground">
              <th className="px-5 py-3 font-bold sm:px-6">Coin</th>
              <th className="px-3 py-3 font-bold">Side</th>
              <th className="px-3 py-3 font-bold">Entry</th>
              <th className="px-3 py-3 font-bold">Current</th>
              <th className="px-3 py-3 font-bold">Stop / Target</th>
              <th className="px-3 py-3 font-bold">Opened</th>
              <th className="px-5 py-3 text-right font-bold sm:px-6">Unrealised P&L</th>
            </tr>
          </thead>
          <tbody>
            {openPositions.map(([coin, state]) => {
              const pos = state.position!;
              const meta = COIN_META[coin] ?? COIN_META.BTC;
              const pnl = pos.unrealisedPnl ?? null;
              return (
                <tr key={coin} className="border-b border-border/50 last:border-0">
                  <td className="px-5 py-3.5 sm:px-6">
                    <span className={`font-mono-data text-xs font-bold ${meta.accent}`}>{coin}</span>
                    <p className="text-[10px] text-muted-foreground">{state.market.pair}</p>
                  </td>
                  <td className="px-3 py-3.5">
                    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono-data text-[10px] font-medium ${pos.direction === 'LONG' ? 'bg-accent/10 text-accent' : 'bg-destructive/10 text-destructive'}`}>
                      {pos.direction === 'LONG' ? <TrendingUp size={10} /> : <TrendingDown size={10} />}
                      {pos.direction}
                    </span>
                  </td>
                  <td className="px-3 py-3.5 font-mono-data text-xs">{money(pos.entry, 4)}</td>
                  <td className="px-3 py-3.5 font-mono-data text-xs">{pos.currentPrice ? money(pos.currentPrice, 4) : '—'}</td>
                  <td className="px-3 py-3.5 text-[11px]">
                    <p className="font-mono-data text-destructive/80">{money(pos.stopLoss, 4)}</p>
                    <p className="font-mono-data text-accent">{money(pos.takeProfit, 4)}</p>
                  </td>
                  <td className="px-3 py-3.5 font-mono-data text-[11px] text-muted-foreground">{dateTime(pos.openedAt)}</td>
                  <td className={`px-5 py-3.5 text-right font-mono-data text-xs font-semibold sm:px-6 ${pnl == null ? 'text-muted-foreground' : pnl >= 0 ? 'text-accent' : 'text-destructive'}`}>
                    {pnl == null ? '—' : `${pnl >= 0 ? '+' : ''}${money(pnl)}`}
                    {pos.unrealisedPct != null && (
                      <p className="text-[10px] font-normal text-muted-foreground">{pct(pos.unrealisedPct)}</p>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ─── Activity preview ─────────────────────────────────────────────────────────
function ActivityPreview() {
  const activityQuery = useListActivityLog(
    { limit: 15 },
    { query: { queryKey: getListActivityLogQueryKey({ limit: 15 }), refetchInterval: 30000 } },
  );
  const events = activityQuery.data ?? [];
  const significant = events.filter((e) =>
    ['TRADE_OPENED', 'TRADE_CLOSED', 'RISK_LIMIT_REACHED', 'ACCOUNT_RESET', 'API_ERROR'].includes(e.event)
  );

  if (significant.length === 0) return null;

  const COIN_COLORS: Record<string, string> = {
    BTC: 'bg-amber-500/15 text-amber-400',
    ETH: 'bg-violet-500/15 text-violet-400',
    SOL: 'bg-green-500/15 text-green-400',
    XRP: 'bg-blue-500/15 text-blue-400',
  };
  const EVENT_STYLES: Record<string, string> = {
    TRADE_OPENED:       'text-accent bg-accent/10',
    TRADE_CLOSED:       'text-primary bg-primary/10',
    API_ERROR:          'text-destructive bg-destructive/10',
    RISK_LIMIT_REACHED: 'text-amber-400 bg-amber-400/10',
    ACCOUNT_RESET:      'text-primary bg-primary/10',
  };

  return (
    <section className="overflow-hidden rounded-2xl border border-border/80 bg-card shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)]">
      <div className="flex items-center gap-2 border-b border-border/70 px-5 py-4 sm:px-6">
        <Zap size={16} className="text-primary" />
        <h2 className="text-sm font-extrabold">Recent events</h2>
        <span className="ml-auto font-mono-data text-[10px] uppercase tracking-wider text-muted-foreground">significant only</span>
      </div>
      <div className="divide-y divide-border/50">
        {significant.slice(0, 8).map((e) => (
          <div key={e.id} className="flex items-center gap-3 px-5 py-2.5 sm:px-6">
            <span className={`shrink-0 rounded px-1.5 py-0.5 font-mono-data text-[9px] font-bold uppercase ${COIN_COLORS[e.coin] ?? 'bg-muted text-muted-foreground'}`}>
              {e.coin}
            </span>
            <span className={`shrink-0 rounded px-1.5 py-0.5 font-mono-data text-[9px] font-semibold uppercase ${EVENT_STYLES[e.event] ?? 'text-muted-foreground bg-muted'}`}>
              {e.event.replace(/_/g, ' ')}
            </span>
            <p className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">{e.message}</p>
            <span className="shrink-0 font-mono-data text-[10px] text-muted-foreground/50">{time(e.ts)}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

// ─── Main dashboard ───────────────────────────────────────────────────────────
const COINS = ['BTC', 'ETH', 'SOL', 'XRP'] as const;
const REFRESH_INTERVAL = 60;

export default function Dashboard() {
  const queryClient = useQueryClient();
  const [resetting, setResetting] = useState(false);
  const refreshTick = useRef(0);
  const countdown = useCountdown(REFRESH_INTERVAL, refreshTick);

  const multiStateQuery = useGetMultiCoinState({
    query: { queryKey: getGetMultiCoinStateQueryKey(), refetchInterval: 30000 },
  });
  const refresh = useRefreshMultiCoin();
  const resetAll = useResetAllCoins();

  const multiState = multiStateQuery.data;

  // Auto-refresh every 60s
  useEffect(() => {
    const syncMarket = () => {
      refreshTick.current += 1;
      refresh.mutate(undefined, {
        onSuccess: (next) => {
          queryClient.setQueryData(getGetMultiCoinStateQueryKey(), next);
          queryClient.invalidateQueries({ queryKey: getListActivityLogQueryKey() });
        },
      });
    };
    syncMarket();
    const id = window.setInterval(syncMarket, REFRESH_INTERVAL * 1000);
    return () => clearInterval(id);
  }, []);

  if (multiStateQuery.isLoading && !multiState) return <Skeleton />;
  if (multiStateQuery.isError || !multiState) return <ErrorPanel onRetry={() => multiStateQuery.refetch()} />;

  const totalBalance  = COINS.reduce((s, c) => s + (multiState[c]?.metrics.virtualBalance ?? 0), 0);
  const totalStarting = COINS.reduce((s, c) => s + (multiState[c]?.metrics.startingBalance ?? 0), 0);
  const totalPnl      = totalBalance - totalStarting;
  const totalRoi      = totalStarting > 0 ? totalPnl / totalStarting * 100 : 0;
  const totalTrades   = COINS.reduce((s, c) => s + (multiState[c]?.metrics.numberOfTrades ?? 0), 0);
  const openPositions = COINS.filter((c) => multiState[c]?.position != null).length;

  const handleRefresh = () => {
    refreshTick.current += 1;
    refresh.mutate(undefined, {
      onSuccess: (next) => {
        queryClient.setQueryData(getGetMultiCoinStateQueryKey(), next);
        queryClient.invalidateQueries({ queryKey: getListActivityLogQueryKey() });
      },
    });
  };

  const handleResetAll = () => {
    if (!window.confirm('Reset ALL four virtual accounts to £100 and clear their entire trade history? This cannot be undone.')) return;
    setResetting(true);
    resetAll.mutate({ data: {} }, {
      onSuccess: (next) => {
        queryClient.setQueryData(getGetMultiCoinStateQueryKey(), next);
        queryClient.invalidateQueries({ queryKey: getListActivityLogQueryKey() });
      },
      onSettled: () => setResetting(false),
    });
  };

  return (
    <TradingShell eyebrow="Live desk" title="Portfolio dashboard" subtitle="BTC · ETH · SOL · XRP — four simulated accounts, each starting at £100.">
      <div className="space-y-6">
        {/* ─── Portfolio header ─────────────────────────────────────────── */}
        <section className="rise-in rounded-2xl border border-border/80 bg-card p-5 shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)] sm:p-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className="flex items-center gap-2">
                <Wallet size={16} className="text-primary" />
                <span className="font-mono-data text-[10px] font-bold uppercase tracking-[0.18em] text-muted-foreground">Combined portfolio</span>
                <span className="flex items-center gap-1.5 font-mono-data text-[10px] text-muted-foreground/70">
                  <span className="h-1.5 w-1.5 rounded-full bg-accent pulse-dot" />
                  next refresh in {countdown}s
                </span>
              </div>
              <div className="mt-2 flex flex-wrap items-end gap-x-5 gap-y-1">
                <span className={`font-mono-data text-3xl font-medium tracking-[-0.06em] ${totalPnl >= 0 ? 'text-accent' : 'text-destructive'}`}>
                  {money(totalBalance)}
                </span>
                <span className={`mb-0.5 font-mono-data text-sm font-semibold ${totalPnl >= 0 ? 'text-accent' : 'text-destructive'}`}>
                  {totalPnl >= 0 ? '+' : ''}{money(totalPnl)} ({pct(totalRoi)})
                </span>
              </div>
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleRefresh}
                disabled={refresh.isPending}
                data-testid="button-refresh-all"
                className="inline-flex items-center gap-2 rounded-lg border border-border bg-background px-3.5 py-2.5 text-xs font-bold transition-all hover:border-primary/50 hover:bg-primary/5 disabled:cursor-wait disabled:opacity-60"
              >
                <RefreshCw size={14} className={refresh.isPending ? 'animate-spin' : ''} />
                {refresh.isPending ? 'Refreshing…' : 'Refresh all'}
              </button>
              <button
                onClick={handleResetAll}
                disabled={resetting}
                data-testid="button-reset-all"
                className="inline-flex items-center gap-2 rounded-lg border border-destructive/25 bg-destructive/[0.04] px-3.5 py-2.5 text-xs font-bold text-destructive transition-all hover:bg-destructive/10 disabled:opacity-60"
              >
                <RotateCcw size={14} /> {resetting ? 'Resetting…' : 'Reset all'}
              </button>
            </div>
          </div>

          {/* Portfolio metric tiles */}
          <div className="mt-5 grid gap-3 border-t border-border/60 pt-5 sm:grid-cols-2 xl:grid-cols-4">
            {[
              { label: 'Starting capital', value: money(totalStarting), sub: 'across all coins', tone: '' },
              { label: 'Total P&L', value: `${totalPnl >= 0 ? '+' : ''}${money(totalPnl)}`, sub: `ROI ${pct(totalRoi)}`, tone: totalPnl >= 0 ? 'text-accent' : 'text-destructive' },
              { label: 'Total trades', value: num(totalTrades, 0), sub: 'across all coins', tone: '' },
              { label: 'Open positions', value: num(openPositions, 0), sub: `of 4 coins active`, tone: openPositions > 0 ? 'text-primary' : '' },
            ].map(({ label, value, sub, tone }) => (
              <div key={label} className="rounded-xl border border-border/60 bg-background px-4 py-3.5">
                <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground">{label}</p>
                <p className={`mt-1.5 font-mono-data text-lg font-medium ${tone}`}>{value}</p>
                <p className="mt-0.5 text-[10px] text-muted-foreground">{sub}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ─── Coin strategy cards ──────────────────────────────────────── */}
        <div className="grid gap-5 lg:grid-cols-2">
          {COINS.map((coin) => (
            <CoinCard key={coin} coin={coin} state={multiState[coin]} />
          ))}
        </div>

        {/* ─── Open positions table ─────────────────────────────────────── */}
        <OpenPositionsSummary coins={multiState as unknown as Record<string, PaperTraderState>} />

        {/* ─── Risk summary strip ───────────────────────────────────────── */}
        <section className="rise-in rise-in-delay-2 overflow-hidden rounded-2xl border border-border/80 bg-card shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)]">
          <div className="flex items-center gap-2 border-b border-border/70 px-5 py-4 sm:px-6">
            <ShieldAlert size={16} className="text-primary" />
            <h2 className="text-sm font-extrabold">Risk guardrails</h2>
            <span className="ml-auto font-mono-data text-[10px] uppercase tracking-wider text-accent">all active</span>
          </div>
          <div className="grid gap-px sm:grid-cols-2 xl:grid-cols-4">
            {COINS.map((coin) => {
              const s = multiState[coin];
              const riskUsage = s.risk.dailyLossLimit > 0
                ? Math.min(100, s.metrics.dailyLoss / s.risk.dailyLossLimit * 100)
                : 0;
              const meta = COIN_META[coin] ?? COIN_META.BTC;
              return (
                <div key={coin} className="px-5 py-4">
                  <p className={`mb-2 font-mono-data text-[10px] font-bold uppercase tracking-wider ${meta.accent}`}>{coin}</p>
                  <div className="mb-1.5 flex justify-between text-[10px]">
                    <span className="text-muted-foreground">Daily loss</span>
                    <span className="font-mono-data">{money(s.metrics.dailyLoss)} / {money(s.risk.dailyLossLimit)}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                    <div className={`h-full rounded-full ${riskUsage > 75 ? 'bg-destructive' : 'bg-primary'}`} style={{ width: `${riskUsage}%` }} />
                  </div>
                  <div className="mt-2 flex justify-between text-[10px] text-muted-foreground">
                    <span>Streak: {s.metrics.consecutiveLosses}/{s.risk.maximumConsecutiveLosses}</span>
                    <span className={`font-mono-data font-semibold ${s.botStatus === 'RISK_PAUSED' ? 'text-amber-400' : s.botStatus === 'READY' ? 'text-accent' : 'text-muted-foreground'}`}>
                      {s.botStatus.replace('_', ' ')}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* ─── Activity preview ─────────────────────────────────────────── */}
        <ActivityPreview />
      </div>
    </TradingShell>
  );
}
