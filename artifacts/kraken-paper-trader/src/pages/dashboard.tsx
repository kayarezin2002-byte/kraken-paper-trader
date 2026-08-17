import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ArrowDownRight, ArrowUpRight, BarChart3, Bot, Clock3, Crosshair, Gauge, LockKeyhole, RefreshCw, RotateCcw, ShieldAlert, Target, TrendingDown, TrendingUp, Wallet } from 'lucide-react';
import { getGetPaperTraderStateQueryKey, getListPaperTradesQueryKey, useGetPaperTraderState, useRefreshPaperTrader, useResetPaperTrader } from '@workspace/api-client-react';
import { TradingShell } from '@/components/trading-shell';

const money = (value: number | null | undefined, digits = 2) => value == null ? '—' : `£${value.toLocaleString('en-GB', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
const number = (value: number | null | undefined, digits = 2) => value == null ? '—' : value.toLocaleString('en-GB', { minimumFractionDigits: digits, maximumFractionDigits: digits });
const time = (value?: string | null) => value ? new Date(value).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '—';
const dateTime = (value?: string | null) => value ? new Date(value).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—';

function SkeletonDashboard() {
  return <TradingShell eyebrow="Live desk" title="Market dashboard" subtitle="A measured view of the BTC/GBP paper strategy.">
    <div className="space-y-6">
      <div className="h-28 rounded-2xl skeleton-shimmer" />
      <div className="grid gap-5 xl:grid-cols-[1.45fr_1fr]"><div className="h-[420px] rounded-2xl skeleton-shimmer" /><div className="h-[420px] rounded-2xl skeleton-shimmer" /></div>
      <div className="h-52 rounded-2xl skeleton-shimmer" />
    </div>
  </TradingShell>;
}

function ErrorPanel({ onRetry }: { onRetry: () => void }) {
  return <TradingShell eyebrow="Live desk" title="Market dashboard" subtitle="We could not read the current paper-trading state.">
    <div className="grid min-h-[55vh] place-items-center">
      <div className="max-w-md rounded-2xl border border-destructive/25 bg-card p-8 text-center shadow-sm">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-destructive/10 text-destructive"><AlertTriangle size={23} /></div>
        <h2 className="text-lg font-extrabold">The desk is temporarily quiet</h2>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">The API did not return a usable state. Your virtual account is safe while we reconnect.</p>
        <button onClick={onRetry} data-testid="button-retry-state" className="mt-6 inline-flex items-center gap-2 rounded-lg bg-sidebar px-4 py-2.5 text-sm font-bold text-white transition-transform hover:-translate-y-0.5"><RefreshCw size={15} /> Try again</button>
      </div>
    </div>
  </TradingShell>;
}

function MetricTile({ label, value, sub, tone = 'default', testId }: { label: string; value: string; sub?: string; tone?: 'default' | 'positive' | 'negative'; testId: string }) {
  return <div className="rounded-xl border border-border/80 bg-card px-4 py-4 shadow-[0_4px_18px_hsl(215_35%_13%_/_0.03)]">
    <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-muted-foreground">{label}</p>
    <p data-testid={testId} className={`mt-2 font-mono-data text-[21px] font-medium tracking-[-0.06em] ${tone === 'positive' ? 'text-accent' : tone === 'negative' ? 'text-destructive' : 'text-foreground'}`}>{value}</p>
    {sub && <p className="mt-1 text-[11px] text-muted-foreground">{sub}</p>}
  </div>;
}

function TrendPill({ label, trend }: { label: string; trend: string }) {
  const positive = trend === 'BULLISH';
  const negative = trend === 'BEARISH';
  return <div className="flex items-center justify-between border-b border-border/70 py-3 last:border-0">
    <span className="text-xs font-semibold text-muted-foreground">{label}</span>
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-mono-data text-[10px] font-medium uppercase tracking-wider ${positive ? 'bg-accent/10 text-accent' : negative ? 'bg-destructive/10 text-destructive' : 'bg-muted text-muted-foreground'}`}>
      {positive ? <TrendingUp size={12} /> : negative ? <TrendingDown size={12} /> : <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {trend.replace('_', ' ')}
    </span>
  </div>;
}

function Dashboard() {
  const queryClient = useQueryClient();
  const [resetting, setResetting] = useState(false);
  const stateQuery = useGetPaperTraderState({ query: { queryKey: getGetPaperTraderStateQueryKey(), refetchInterval: 30000 } });
  const refresh = useRefreshPaperTrader();
  const reset = useResetPaperTrader();
  const state = stateQuery.data;

  useEffect(() => {
    const syncMarket = () => refresh.mutate(undefined, {
      onSuccess: (next) => {
        queryClient.setQueryData(getGetPaperTraderStateQueryKey(), next);
        queryClient.invalidateQueries({ queryKey: getListPaperTradesQueryKey() });
      },
    });
    syncMarket();
    const interval = window.setInterval(syncMarket, 60000);
    return () => window.clearInterval(interval);
  }, []);

  if (stateQuery.isLoading && !state) return <SkeletonDashboard />;
  if (stateQuery.isError || !state) return <ErrorPanel onRetry={() => stateQuery.refetch()} />;

  const positivePnl = state.metrics.totalProfitLoss >= 0;
  const riskUsage = state.risk.dailyLossLimit > 0 ? Math.min(100, Math.abs(state.metrics.dailyLoss) / state.risk.dailyLossLimit * 100) : 0;
  const signalPositive = state.signal === 'LONG';

  const handleRefresh = () => refresh.mutate(undefined, {
    onSuccess: (next) => {
      queryClient.setQueryData(getGetPaperTraderStateQueryKey(), next);
    },
  });
  const handleReset = () => {
    if (!window.confirm('Reset the virtual account and clear simulated history?')) return;
    setResetting(true);
    reset.mutate({ data: { startingBalance: state.metrics.startingBalance } }, {
      onSuccess: (next) => {
        queryClient.setQueryData(getGetPaperTraderStateQueryKey(), next);
        queryClient.invalidateQueries({ queryKey: ['/api/paper-trader/trades'] });
      },
      onSettled: () => setResetting(false),
    });
  };

  return <TradingShell eyebrow="Live desk" title="Market dashboard" subtitle="A measured view of the BTC/GBP paper strategy.">
    <div className="space-y-6">
      <section className="rise-in flex flex-col justify-between gap-5 rounded-2xl border border-border/80 bg-card p-5 shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)] sm:flex-row sm:items-center sm:p-6">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-mono-data text-[11px] font-medium uppercase tracking-[0.2em] text-muted-foreground">{state.market.pair}</span>
            <span className="rounded-full bg-accent/10 px-2.5 py-1 font-mono-data text-[10px] font-medium uppercase tracking-wider text-accent">Kraken spot</span>
            <span className="flex items-center gap-1.5 font-mono-data text-[10px] uppercase tracking-wider text-muted-foreground"><span className="h-1.5 w-1.5 rounded-full bg-accent pulse-dot" /> refreshed {time(state.market.updatedAt)}</span>
          </div>
          <div className="mt-3 flex flex-wrap items-end gap-x-4 gap-y-1">
            <span data-testid="text-market-price" className="font-mono-data text-[34px] font-medium leading-none tracking-[-0.07em] sm:text-[42px]">{money(state.market.currentPrice)}</span>
            <span className="mb-1 text-xs text-muted-foreground">latest market price</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button onClick={handleRefresh} disabled={refresh.isPending} data-testid="button-refresh-market" className="inline-flex items-center justify-center gap-2 rounded-lg border border-border bg-background px-3.5 py-2.5 text-xs font-bold transition-all hover:border-primary/50 hover:bg-primary/5 disabled:cursor-wait disabled:opacity-60">
            <RefreshCw size={14} className={refresh.isPending ? 'animate-spin' : ''} /> {refresh.isPending ? 'Checking…' : 'Refresh market'}
          </button>
          <button onClick={handleReset} disabled={resetting} data-testid="button-reset-account" className="inline-flex items-center justify-center gap-2 rounded-lg border border-destructive/25 bg-destructive/[0.04] px-3.5 py-2.5 text-xs font-bold text-destructive transition-all hover:bg-destructive/10 disabled:opacity-60">
            <RotateCcw size={14} /> {resetting ? 'Resetting…' : 'Reset account'}
          </button>
        </div>
      </section>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile label="Virtual balance" value={money(state.metrics.virtualBalance)} sub={`start ${money(state.metrics.startingBalance)}`} testId="metric-virtual-balance" />
        <MetricTile label="Total P&L" value={`${positivePnl ? '+' : ''}${money(state.metrics.totalProfitLoss)}`} sub={`${state.metrics.roi >= 0 ? '+' : ''}${number(state.metrics.roi)}% return`} tone={positivePnl ? 'positive' : 'negative'} testId="metric-total-pnl" />
         <MetricTile label="ROI" value={`${state.metrics.roi >= 0 ? '+' : ''}${number(state.metrics.roi)}%`} sub="since account start" tone={state.metrics.roi >= 0 ? 'positive' : 'negative'} testId="metric-roi" />
         <MetricTile label="Trades" value={number(state.metrics.numberOfTrades, 0)} sub={`${state.metrics.wins} wins / ${state.metrics.losses} losses`} testId="metric-trade-count" />
        <MetricTile label="Win rate" value={`${number(state.metrics.winRate)}%`} sub={`${state.metrics.wins} wins / ${state.metrics.losses} losses`} tone="positive" testId="metric-win-rate" />
         <MetricTile label="Profit factor" value={state.metrics.profitFactor > 0 ? number(state.metrics.profitFactor) : '—'} sub="gross profit / loss" tone={state.metrics.profitFactor >= 1 ? 'positive' : 'default'} testId="metric-profit-factor" />
         <MetricTile label="Max drawdown" value={`${number(state.metrics.maximumDrawdown)}%`} sub={`${state.metrics.consecutiveLosses} consecutive losses`} tone={state.metrics.maximumDrawdown > 0 ? 'negative' : 'default'} testId="metric-drawdown" />
      </section>

      <div className="grid gap-6 xl:grid-cols-[1.42fr_0.95fr]">
        <section className="rise-in rise-in-delay-1 overflow-hidden rounded-2xl border border-border/80 bg-card shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)]">
          <div className="flex flex-col gap-4 border-b border-border/70 p-5 sm:flex-row sm:items-start sm:justify-between sm:p-6">
            <div>
              <div className="flex items-center gap-2"><Crosshair size={16} className="text-primary" /><h2 className="text-sm font-extrabold">Strategy readout</h2></div>
              <p className="mt-1 text-xs text-muted-foreground">The signal is a discipline prompt, not a trade recommendation.</p>
            </div>
            <div className={`inline-flex w-fit items-center gap-2 rounded-lg border px-3 py-2 ${signalPositive ? 'border-accent/30 bg-accent/10 text-accent' : state.signal === 'SHORT' ? 'border-destructive/30 bg-destructive/10 text-destructive' : 'border-border bg-muted text-muted-foreground'}`}>
              {signalPositive ? <ArrowUpRight size={16} /> : state.signal === 'SHORT' ? <ArrowDownRight size={16} /> : <LockKeyhole size={15} />}
              <span data-testid="status-signal" className="font-mono-data text-xs font-medium uppercase tracking-wider">{state.signal.replace('_', ' ')}</span>
            </div>
          </div>
          <div className="grid gap-5 p-5 sm:grid-cols-[1fr_1.1fr] sm:p-6">
            <div className="grid-lines relative min-h-[210px] overflow-hidden rounded-xl border border-border/70 bg-background p-4">
              <div className="absolute inset-x-0 top-1/2 border-t border-dashed border-border" />
              <div className="absolute bottom-0 left-0 right-0 h-[72%] opacity-80">
                <svg viewBox="0 0 520 180" preserveAspectRatio="none" className="h-full w-full" aria-label="Illustrative market rhythm">
                  <path d="M0 145 C25 138 35 146 58 118 S95 132 112 104 S144 98 164 110 S183 92 204 103 S225 68 248 79 S272 65 294 91 S322 72 344 77 S372 50 394 63 S415 40 437 52 S469 32 520 20" fill="none" stroke="hsl(var(--chart-1))" strokeWidth="3" vectorEffect="non-scaling-stroke" />
                  <path d="M0 145 C25 138 35 146 58 118 S95 132 112 104 S144 98 164 110 S183 92 204 103 S225 68 248 79 S272 65 294 91 S322 72 344 77 S372 50 394 63 S415 40 437 52 S469 32 520 20 V180 H0Z" fill="url(#fill)" opacity=".14" />
                  <defs><linearGradient id="fill" x1="0" x2="0" y1="0" y2="1"><stop stopColor="hsl(var(--primary))" /><stop offset="1" stopColor="hsl(var(--primary))" stopOpacity="0" /></linearGradient></defs>
                </svg>
              </div>
              <div className="relative flex items-start justify-between">
                <span className="font-mono-data text-[10px] uppercase tracking-wider text-muted-foreground">price rhythm</span>
                <span className="rounded bg-primary/10 px-2 py-1 font-mono-data text-[10px] text-primary">1h</span>
              </div>
              <div className="absolute bottom-3 left-4 right-4 flex justify-between font-mono-data text-[9px] text-muted-foreground/70"><span>−4h</span><span>now</span></div>
            </div>
            <div className="rounded-xl border border-border/70 bg-background px-4 py-2">
              <TrendPill label="One-hour direction" trend={state.oneHourTrend} />
              <TrendPill label="Four-hour direction" trend={state.fourHourTrend} />
              <TrendPill label="Last completed candle" trend={time(state.market.lastCompletedCandleAt)} />
              <div className="mt-2 flex items-center justify-between pt-2"><span className="text-xs font-semibold text-muted-foreground">Model note</span><span className="max-w-[180px] text-right text-[11px] leading-relaxed text-foreground/75">{state.message || 'No new decision at this candle.'}</span></div>
            </div>
          </div>
        </section>

        <section className="rise-in rise-in-delay-2 rounded-2xl border border-border/80 bg-sidebar p-5 text-sidebar-foreground shadow-[0_10px_32px_hsl(215_35%_13%_/_0.12)] sm:p-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2"><Bot size={17} className="text-primary" /><h2 className="text-sm font-extrabold text-white">Bot status</h2></div>
            <span data-testid="status-bot" className={`rounded-full px-2.5 py-1 font-mono-data text-[10px] font-medium uppercase tracking-wider ${state.botStatus === 'READY' ? 'bg-accent/15 text-[#6ad4bf]' : state.botStatus === 'RISK_PAUSED' ? 'bg-primary/15 text-primary' : 'bg-white/10 text-sidebar-foreground/70'}`}>{state.botStatus.replaceAll('_', ' ')}</span>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-sidebar-foreground/60">{state.message || 'Monitoring completed candles and risk limits.'}</p>
          <div className="my-5 h-px bg-sidebar-border" />
          <div className="flex items-end justify-between">
            <div><p className="text-[10px] uppercase tracking-[0.15em] text-sidebar-foreground/45">Open position</p><p data-testid="status-position" className="mt-1 text-lg font-extrabold text-white">{state.position ? `${state.position.direction} · ${number(state.position.quantity, 5)} BTC` : 'Flat'}</p></div>
            {state.position ? <div className="text-right"><p className="text-[10px] uppercase tracking-[0.15em] text-sidebar-foreground/45">risk at stake</p><p className="mt-1 font-mono-data text-sm text-primary">{money(state.position.riskAmount)}</p></div> : <Gauge size={25} className="text-sidebar-foreground/30" />}
          </div>
          {state.position && <div className="mt-5 grid grid-cols-2 gap-2 text-xs">
            <div className="rounded-lg bg-sidebar-accent/70 p-3"><p className="text-[10px] text-sidebar-foreground/45">Entry</p><p className="mt-1 font-mono-data text-sidebar-foreground">{money(state.position.entry)}</p></div>
            <div className="rounded-lg bg-sidebar-accent/70 p-3"><p className="text-[10px] text-sidebar-foreground/45">Opened</p><p className="mt-1 font-mono-data text-sidebar-foreground">{dateTime(state.position.openedAt)}</p></div>
            <div className="rounded-lg bg-sidebar-accent/70 p-3"><p className="text-[10px] text-sidebar-foreground/45">Stop loss</p><p className="mt-1 font-mono-data text-[#f0a39f]">{money(state.position.stopLoss)}</p></div>
            <div className="rounded-lg bg-sidebar-accent/70 p-3"><p className="text-[10px] text-sidebar-foreground/45">Take profit</p><p className="mt-1 font-mono-data text-[#6ad4bf]">{money(state.position.takeProfit)}</p></div>
          </div>}
        </section>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.15fr_0.85fr]">
        <section className="rise-in rise-in-delay-3 rounded-2xl border border-border/80 bg-card p-5 shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)] sm:p-6">
          <div className="mb-4 flex items-center justify-between"><div className="flex items-center gap-2"><BarChart3 size={16} className="text-primary" /><h2 className="text-sm font-extrabold">Indicator panel</h2></div><span className="font-mono-data text-[10px] uppercase tracking-wider text-muted-foreground">live snapshot</span></div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
            {[['RSI', number(state.indicators.rsi), state.indicators.rsi != null && state.indicators.rsi > 70 ? 'elevated' : 'balanced'], ['MACD', number(state.indicators.macd, 4), 'momentum'], ['Signal', number(state.indicators.macdSignal, 4), 'baseline'], ['ATR', number(state.indicators.atr, 2), 'volatility'], ['EMA 20', money(state.indicators.ema20), 'short trend'], ['EMA 50', money(state.indicators.ema50), 'long trend'], ['Volume', number(state.indicators.volume, 0), 'latest candle'], ['Polling', `${state.risk.pollingSeconds}s`, 'refresh cadence']].map(([label, value, sub]) => <div key={label}><p className="text-[10px] font-bold uppercase tracking-[0.13em] text-muted-foreground">{label}</p><p data-testid={`indicator-${label.toLowerCase().replace(' ', '-')}`} className="mt-1 font-mono-data text-sm font-medium">{value}</p><p className="mt-0.5 text-[10px] text-muted-foreground">{sub}</p></div>)}
          </div>
        </section>
        <section className="rounded-2xl border border-border/80 bg-card p-5 shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)] sm:p-6">
          <div className="mb-4 flex items-center justify-between"><div className="flex items-center gap-2"><ShieldAlert size={16} className="text-primary" /><h2 className="text-sm font-extrabold">Risk guardrails</h2></div><span className="font-mono-data text-[10px] uppercase tracking-wider text-accent">active</span></div>
          <div className="space-y-4">
            <div><div className="mb-1.5 flex justify-between text-xs"><span className="font-semibold text-muted-foreground">Daily loss used</span><span className="font-mono-data">{money(Math.abs(state.metrics.dailyLoss))} / {money(state.risk.dailyLossLimit)}</span></div><div className="h-2 overflow-hidden rounded-full bg-muted"><div className={`h-full rounded-full transition-all ${riskUsage > 75 ? 'bg-destructive' : 'bg-primary'}`} style={{ width: `${riskUsage}%` }} /></div></div>
            <div className="grid grid-cols-3 gap-3">
              <div><p className="text-[10px] uppercase tracking-wider text-muted-foreground">Risk / trade</p><p className="mt-1 font-mono-data text-sm">{number(state.risk.riskPerTrade)}%</p></div>
              <div><p className="text-[10px] uppercase tracking-wider text-muted-foreground">Reward / risk</p><p className="mt-1 font-mono-data text-sm">1 : {number(state.risk.rewardToRisk)}</p></div>
              <div><p className="text-[10px] uppercase tracking-wider text-muted-foreground">Loss streak</p><p className="mt-1 font-mono-data text-sm">{state.metrics.consecutiveLosses} / {state.risk.maximumConsecutiveLosses}</p></div>
            </div>
          </div>
        </section>
      </div>

      <section className="rounded-2xl border border-border/80 bg-card shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)]">
        <div className="flex items-center justify-between border-b border-border/70 px-5 py-4 sm:px-6"><div className="flex items-center gap-2"><Clock3 size={16} className="text-primary" /><h2 className="text-sm font-extrabold">Recent simulated trades</h2></div><span className="font-mono-data text-[10px] uppercase tracking-wider text-muted-foreground">{state.recentTrades.length} shown</span></div>
        {state.recentTrades.length === 0 ? <div data-testid="empty-recent-trades" className="flex flex-col items-center justify-center px-5 py-12 text-center"><div className="flex h-11 w-11 items-center justify-center rounded-xl bg-muted text-muted-foreground"><Target size={20} /></div><p className="mt-3 text-sm font-bold">No trades yet</p><p className="mt-1 max-w-xs text-xs leading-relaxed text-muted-foreground">When the strategy completes its first simulated position, the review will appear here.</p></div> : <div className="overflow-x-auto"><table className="w-full min-w-[650px] text-left"><thead><tr className="border-b border-border/70 text-[10px] uppercase tracking-[0.13em] text-muted-foreground"><th className="px-5 py-3 font-bold sm:px-6">Closed</th><th className="px-3 py-3 font-bold">Direction</th><th className="px-3 py-3 font-bold">Entry → exit</th><th className="px-3 py-3 font-bold">Reason</th><th className="px-5 py-3 text-right font-bold sm:px-6">P&L</th></tr></thead><tbody>{state.recentTrades.slice(0, 5).map((trade) => <tr key={trade.id} data-testid={`row-recent-trade-${trade.id}`} className="border-b border-border/50 last:border-0"><td className="px-5 py-3.5 font-mono-data text-xs text-muted-foreground sm:px-6">{dateTime(trade.closedAt)}</td><td className="px-3 py-3.5"><span className={`font-mono-data text-xs font-medium ${trade.direction === 'LONG' ? 'text-accent' : 'text-destructive'}`}>{trade.direction}</span></td><td className="px-3 py-3.5 font-mono-data text-xs">{money(trade.entry)} <span className="text-muted-foreground">→</span> {money(trade.exit)}</td><td className="px-3 py-3.5 text-xs text-muted-foreground">{trade.exitReason.replaceAll('_', ' ')}</td><td className={`px-5 py-3.5 text-right font-mono-data text-xs font-medium sm:px-6 ${trade.profitLoss >= 0 ? 'text-accent' : 'text-destructive'}`}>{trade.profitLoss >= 0 ? '+' : ''}{money(trade.profitLoss)}</td></tr>)}</tbody></table></div>}
      </section>
    </div>
  </TradingShell>;
}

export default Dashboard;