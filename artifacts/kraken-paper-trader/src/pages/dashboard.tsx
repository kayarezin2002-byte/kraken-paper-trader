import { useEffect, useState, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle, ArrowDownRight, ArrowUpRight, BarChart3, CheckCircle2,
  ChevronDown, ChevronUp, Clock3, RefreshCw, RotateCcw, ScanSearch,
  ShieldAlert, Target, TrendingDown, TrendingUp, Wallet, XCircle, Zap,
} from 'lucide-react';
import {
  getGetMultiCoinStateQueryKey,
  getListActivityLogQueryKey,
  useGetEngineStatus,
  useGetMultiCoinState,
  useRefreshMultiCoin,
  useResetAllCoins,
  useListActivityLog,
  type PaperTraderState,
} from '@workspace/api-client-react';
import { TradingShell } from '@/components/trading-shell';
import { StrategyConditionsPanel } from '@/components/strategy-conditions';

// ─── Formatters ──────────────────────────────────────────────────────────────
const money = (v: number | null | undefined, d = 2, sym = '£') =>
  v == null ? '—' : `${sym}${v.toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const curSym = (currency?: string) => (currency === 'USD' ? '$' : '£');
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
  GOLD:   { label: 'Gold',   accent: 'text-yellow-400', border: 'border-yellow-500/20',  bg: 'bg-yellow-500/5' },
  SILVER: { label: 'Silver', accent: 'text-slate-300',  border: 'border-slate-400/20',   bg: 'bg-slate-400/5'  },
};

// Price formatter aware of the instrument's quote currency (crypto £, metals $)
const priceFmt = (v: number | null | undefined, currency: string | undefined, d = 2) => {
  if (v == null) return '—';
  const sym = currency === 'USD' ? '$' : '£';
  return `${sym}${v.toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d })}`;
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
    <TradingShell eyebrow="Live desk" title="Portfolio dashboard" subtitle="BTC · ETH · SOL · XRP · GOLD · SILVER — simulated accounts only.">
      <div className="space-y-6">
        <div className="grid gap-3 sm:grid-cols-4">
          {[1,2,3,4].map((i) => <div key={i} className="h-24 rounded-2xl skeleton-shimmer" />)}
        </div>
        <div className="grid gap-5 lg:grid-cols-2">
          {[1,2,3,4,5,6].map((i) => <div key={i} className="h-[420px] rounded-2xl skeleton-shimmer" />)}
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

// ─── Opportunity panel ───────────────────────────────────────────────────────
function OpportunityPanel({ opportunity, coin }: { opportunity: NonNullable<PaperTraderState['opportunity']>; coin: string }) {
  const modeColor =
    opportunity.mode === 'TREND'  ? 'bg-sky-400/10 text-sky-400'
  : opportunity.mode === 'RANGE'  ? 'bg-violet-400/10 text-violet-400'
  :                                 'bg-destructive/10 text-destructive';
  const statusColor =
    opportunity.entryStatus === 'READY'   ? 'bg-accent/10 text-accent'
  : opportunity.entryStatus === 'BLOCKED' ? 'bg-amber-400/10 text-amber-400'
  :                                         'bg-muted text-muted-foreground';
  return (
    <div className="rounded-lg border border-border/70 bg-muted/30 px-3 py-2.5" data-testid={`opportunity-${coin}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono-data text-[10px] font-bold uppercase tracking-wider text-foreground">
          {opportunity.score}/{opportunity.maxScore}
        </span>
        <span className={`rounded-full px-2 py-0.5 font-mono-data text-[9px] font-semibold uppercase tracking-wider ${modeColor}`}>
          {opportunity.mode}
        </span>
        <span className={`rounded-full px-2 py-0.5 font-mono-data text-[9px] font-semibold uppercase tracking-wider ${statusColor}`}>
          {opportunity.entryStatus}
        </span>
      </div>
      {opportunity.reason && (
        <p className="mt-1.5 text-[10px] leading-relaxed text-muted-foreground">{opportunity.reason}</p>
      )}
      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[9px] text-muted-foreground/70">
        <span>Last trade: <span className="font-mono-data">{opportunity.lastTradeAt ? time(opportunity.lastTradeAt) : 'none yet'}</span></span>
        {opportunity.nextEligible && <span>Next entry: <span className="font-mono-data">{opportunity.nextEligible}</span></span>}
      </div>
    </div>
  );
}

// ─── Directional evaluation panel (all assets: independent LONG vs SHORT) ───
function DirectionalPanel({ directional, coin }: { directional: NonNullable<PaperTraderState['directional']>; coin: string }) {
  const d = directional;
  const max = d.maxScore ?? 6;
  const shortGate = d.shortThreshold ?? d.threshold;
  const decisionColor =
    d.decision === 'LONG'  ? 'bg-accent/10 text-accent'
  : d.decision === 'SHORT' ? 'bg-destructive/10 text-destructive'
  :                          'bg-muted text-muted-foreground';
  const side = (label: string, score: number, conds: typeof d.longConditions, active: boolean, tone: 'long' | 'short') => (
    <div className={`rounded-lg border px-2.5 py-2 ${active ? (tone === 'long' ? 'border-accent/40 bg-accent/5' : 'border-destructive/40 bg-destructive/5') : 'border-border/60 bg-muted/20'}`}>
      <div className="flex items-center justify-between">
        <span className={`font-mono-data text-[10px] font-bold uppercase tracking-wider ${tone === 'long' ? 'text-accent' : 'text-destructive'}`}>{label}</span>
        <span className="font-mono-data text-[11px] font-semibold text-foreground">{score}/{max}</span>
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1">
        {conds.map((c) => (
          <span
            key={c.name}
            title={`${c.currentValue} (need ${c.requiredValue})`}
            className={`inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 font-mono-data text-[9px] font-medium ${
              c.pass ? 'bg-accent/8 text-accent' : 'bg-destructive/10 text-destructive/80'
            }`}
          >
            {c.pass ? <CheckCircle2 size={8} className="shrink-0" /> : <XCircle size={8} className="shrink-0" />}
            {c.name}
          </span>
        ))}
      </div>
    </div>
  );
  return (
    <div className="rounded-lg border border-border/70 bg-muted/30 px-3 py-2.5" data-testid={`directional-${coin}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono-data text-[10px] font-bold uppercase tracking-wider text-foreground">
          Long vs short setups (gate ≥ {d.threshold === shortGate ? `${d.threshold}/${max}` : `L ${d.threshold} · S ${shortGate} of ${max}`})
        </span>
        <span className={`ml-auto rounded-full px-2 py-0.5 font-mono-data text-[9px] font-semibold uppercase tracking-wider ${decisionColor}`}>
          {d.decision === 'NO_TRADE' ? 'NO TRADE / WAIT' : `Decision: ${d.decision}`}
        </span>
      </div>
      <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {side('Long', d.longScore, d.longConditions, d.decision === 'LONG', 'long')}
        {side('Short', d.shortScore, d.shortConditions, d.decision === 'SHORT', 'short')}
      </div>
      {d.reason && (
        <p className="mt-1.5 text-[10px] leading-relaxed text-muted-foreground">{d.reason}</p>
      )}
    </div>
  );
}

// ─── Execution diagnostics panel (why is an entry NOT happening right now?) ──
function ExecutionDiagnosticsPanel({ diag, coin }: { diag: NonNullable<PaperTraderState['executionDiagnostics']>; coin: string }) {
  return (
    <div className="rounded-lg border border-border/70 bg-muted/20 px-3 py-2.5" data-testid={`exec-diag-${coin}`}>
      <div className="flex items-center gap-1.5">
        <span className="font-mono-data text-[10px] font-bold uppercase tracking-wider text-foreground">Execution diagnostics</span>
        <span className={`ml-auto rounded-full px-2 py-0.5 font-mono-data text-[9px] font-semibold uppercase tracking-wider ${
          diag.eligible ? 'bg-accent/10 text-accent' : 'bg-muted text-muted-foreground'
        }`}>
          Entry eligible: {diag.eligible ? 'YES' : 'NO'}
        </span>
      </div>
      {!diag.eligible && diag.blockers.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {diag.blockers.map((b) => (
            <li key={b} className="flex items-start gap-1.5 text-[10px] leading-relaxed text-muted-foreground">
              <XCircle size={10} className="mt-0.5 shrink-0 text-destructive/70" />
              <span>{b}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ─── Engine status strip (server-side scan scheduler) ───────────────────────
function EngineStatusStrip({ lastCandleAt }: { lastCandleAt: string | null | undefined }) {
  const { data: engine } = useGetEngineStatus({ query: { queryKey: ['engine-status'], refetchInterval: 30000 } });
  if (!engine) return null;
  const tone =
    engine.status === 'RUNNING' ? 'text-accent'
    : engine.status === 'ERROR' ? 'text-destructive'
    : 'text-muted-foreground';
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 rounded-xl border border-border/60 bg-card px-4 py-2.5" data-testid="engine-status">
      <span className="flex items-center gap-1.5 font-mono-data text-[10px] font-bold uppercase tracking-wider">
        <span className={`h-1.5 w-1.5 rounded-full ${engine.status === 'RUNNING' ? 'bg-accent pulse-dot' : engine.status === 'ERROR' ? 'bg-destructive' : 'bg-muted-foreground'}`} />
        Engine <span className={tone}>{engine.status}</span>
      </span>
      <span className="font-mono-data text-[10px] text-muted-foreground">Last strategy scan: <span className="text-foreground">{dateTime(engine.lastScanAt)}</span></span>
      <span className="font-mono-data text-[10px] text-muted-foreground">Next strategy scan: <span className="text-foreground">{dateTime(engine.nextScanAt)}</span></span>
      <span className="font-mono-data text-[10px] text-muted-foreground">Last completed 1h candle: <span className="text-foreground">{dateTime(lastCandleAt)}</span></span>
      <span className="font-mono-data text-[10px] text-muted-foreground">scans every {engine.intervalSeconds}s server-side (browser can be closed)</span>
      {engine.lastError && <span className="font-mono-data text-[10px] text-destructive">last error: {engine.lastError.slice(0, 120)}</span>}
    </div>
  );
}

// ─── Latest scan strip ───────────────────────────────────────────────────────
function noTradeReason(state: PaperTraderState): string | null {
  const conds = state.strategyConditions;
  if (!conds || !conds.conditions || conds.conditions.length === 0) return null;
  if (conds.bias === 'NEUTRAL') return 'No directional edge — LONG and SHORT evaluations tied';
  const failed = conds.conditions.filter((c) => !c.pass).map((c) => c.name);
  if (failed.length === 0) return null;
  return 'Failed: ' + failed.join(', ');
}

function LatestScanStrip({ coins, multiState }: { coins: readonly string[]; multiState: Record<string, PaperTraderState> }) {
  return (
    <section className="rise-in overflow-hidden rounded-2xl border border-border/80 bg-card shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)]">
      <div className="flex items-center gap-2 border-b border-border/70 px-5 py-4 sm:px-6">
        <ScanSearch size={16} className="text-sky-400" />
        <h2 className="text-sm font-extrabold">Latest strategy scan</h2>
        <span className="ml-auto font-mono-data text-[10px] uppercase tracking-wider text-muted-foreground">crypto ≥ 6/8 score · gold ≥ 5/6 either direction · silver 6/6 + hard safety rules</span>
      </div>
      <div className="divide-y divide-border/50">
        {coins.map((coin) => {
          const state = multiState[coin];
          if (!state) return null;
          const meta   = COIN_META[coin] ?? COIN_META.BTC;
          const conds  = state.strategyConditions;
          const pass   = conds?.passCount ?? 0;
          const total  = conds?.totalCount ?? 6;
          const sig    = state.signal;
          const reason = state.botStatus === 'WAITING_FOR_DATA' ? 'Waiting for enough candles'
                       : state.botStatus === 'API_ERROR'        ? 'API error — no market data'
                       : state.botStatus === 'RISK_PAUSED'      ? 'Risk limit reached — entries paused'
                       : noTradeReason(state);
          const sigColor = sig === 'LONG' ? 'text-accent bg-accent/10'
                         : sig === 'SHORT' ? 'text-destructive bg-destructive/10'
                         : 'text-muted-foreground bg-muted';
          const condItems = conds?.conditions ?? [];

          return (
            <div key={coin} className="px-5 py-4 sm:px-6">
              {/* Top row */}
              <div className="flex flex-wrap items-center gap-3">
                <span className={`shrink-0 font-mono-data text-[11px] font-bold uppercase tracking-[0.18em] ${meta.accent}`}>{coin}</span>
                <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono-data text-[10px] font-semibold uppercase tracking-wider ${sigColor}`}>
                  {sig === 'LONG' ? <ArrowUpRight size={10} /> : sig === 'SHORT' ? <ArrowDownRight size={10} /> : null}
                  {sig.replace('_', ' ')}
                </span>
                {/* Mini progress */}
                <div className="flex items-center gap-1.5">
                  <div className="flex gap-0.5">
                    {Array.from({ length: total }).map((_, i) => (
                      <div key={i} className={`h-2.5 w-2.5 rounded-sm ${i < pass ? 'bg-primary' : 'bg-muted'}`} />
                    ))}
                  </div>
                  <span className="font-mono-data text-[10px] text-muted-foreground">{pass}/{total}</span>
                </div>
                {conds?.long && conds?.short && (
                  <span className="flex items-center gap-1">
                    <span className="rounded bg-accent/10 px-1.5 py-0.5 font-mono-data text-[10px] font-semibold text-accent">
                      L {conds.long.passCount}/6
                    </span>
                    <span className="rounded bg-destructive/10 px-1.5 py-0.5 font-mono-data text-[10px] font-semibold text-destructive">
                      S {conds.short.passCount}/6
                    </span>
                  </span>
                )}
                {state.opportunity && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 font-mono-data text-[10px] font-semibold text-primary">
                    score {state.opportunity.score}/{state.opportunity.maxScore}
                  </span>
                )}
                <span className="ml-auto shrink-0 flex items-center gap-1 font-mono-data text-[10px] text-muted-foreground/60">
                  <Clock3 size={9} />
                  {time(state.market.updatedAt)}
                </span>
              </div>

              {/* Condition pills + reason */}
              {condItems.length > 0 && (
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  {condItems.map((cond) => (
                    <span
                      key={cond.name}
                      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 font-mono-data text-[10px] font-medium ${
                        cond.pass ? 'bg-accent/8 text-accent' : 'bg-destructive/10 text-destructive/80'
                      }`}
                    >
                      {cond.pass
                        ? <CheckCircle2 size={9} className="shrink-0" />
                        : <XCircle size={9} className="shrink-0" />}
                      {cond.name}
                    </span>
                  ))}
                </div>
              )}

              {reason && (
                <p className="mt-2 text-[11px] text-muted-foreground/80">
                  <span className="font-semibold text-muted-foreground">Why no trade: </span>{reason}
                </p>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ─── Open position panel ──────────────────────────────────────────────────────
function PositionPanel({ position, coin, sym }: { position: NonNullable<PaperTraderState['position']>; coin: string; sym: string }) {
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
            {pnl >= 0 ? '+' : ''}{money(pnl, 2, sym)} ({pnlPct != null ? pct(pnlPct) : '—'})
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[10px]">
        <div><span className="text-muted-foreground">Entry</span><span className="ml-2 font-mono-data">{money(position.entry, 4, sym)}</span></div>
        <div><span className="text-muted-foreground">Current</span><span className="ml-2 font-mono-data">{position.currentPrice ? money(position.currentPrice, 4, sym) : '—'}</span></div>
        <div><span className="text-muted-foreground">Stop</span><span className="ml-2 font-mono-data text-destructive/80">{money(position.stopLoss, 4, sym)}</span></div>
        <div><span className="text-muted-foreground">Target</span><span className="ml-2 font-mono-data text-accent">{money(position.takeProfit, 4, sym)}</span></div>
        <div><span className="text-muted-foreground">Risk</span><span className="ml-2 font-mono-data">{money(position.riskAmount, 2, sym)}</span></div>
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
  const sym = curSym(state.instrument.currency);

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
              {priceFmt(state.market.currentPrice, state.instrument.currency, state.market.currentPrice != null && state.market.currentPrice < 10 ? 4 : 2)}
            </p>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <span className={`inline-flex items-center rounded-full px-2 py-0.5 font-mono-data text-[9px] font-bold uppercase tracking-wider ${
              state.instrument.tradingMode === 'MONITORING' || state.instrument.tradingMode === 'PAPER_UNVALIDATED'
                ? 'bg-amber-400/10 text-amber-400 border border-amber-400/25'
                : 'bg-accent/10 text-accent border border-accent/25'
            }`} data-testid={`badge-status-${coin}`}>
              {state.instrument.statusLabel}
            </span>
            <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono-data text-[10px] font-medium uppercase tracking-wider
              ${state.signal === 'LONG' ? 'bg-accent/10 text-accent' : state.signal === 'SHORT' ? 'bg-destructive/10 text-destructive' : 'bg-muted text-muted-foreground'}`}>
              {state.signal === 'LONG' ? <ArrowUpRight size={11} /> : state.signal === 'SHORT' ? <ArrowDownRight size={11} /> : null}
              {state.signal.replace('_', ' ')}
            </span>
            <span className="font-mono-data text-[10px] text-muted-foreground/60">updated {time(state.market.updatedAt)}</span>
          </div>
        </div>
        <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground/70">{state.instrument.dataSource}</p>
      </div>

      {/* Body */}
      <div className="flex-1 space-y-4 px-5 py-4">
        {/* Metal-specific feed-status notices */}
        {state.instrument.kind === 'METAL' && state.botStatus === 'API_ERROR' && (
          <div
            className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/8 px-3 py-2.5"
            data-testid={`metal-feed-error-${coin}`}
          >
            <AlertTriangle size={13} className="mt-0.5 shrink-0 text-destructive" />
            <div>
              <p className="font-mono-data text-[10px] font-bold uppercase tracking-wider text-destructive">
                Spot price feed unavailable
              </p>
              <p className="mt-0.5 text-[10px] leading-relaxed text-muted-foreground">
                {state.message || 'gold-api.com is not responding — no current price data.'}
              </p>
            </div>
          </div>
        )}
        {state.instrument.kind === 'METAL' && state.scanNote && (
          <div
            className="flex items-start gap-2 rounded-lg border border-amber-500/25 bg-amber-500/8 px-3 py-2.5"
            data-testid={`metal-scan-unavailable-${coin}`}
          >
            <AlertTriangle size={13} className="mt-0.5 shrink-0 text-amber-400" />
            <div>
              <p className="font-mono-data text-[10px] font-bold uppercase tracking-wider text-amber-400">
                Scan data unavailable
              </p>
              <p className="mt-0.5 text-[10px] leading-relaxed text-muted-foreground">
                {state.scanNote} — spot price is still live.
              </p>
            </div>
          </div>
        )}

        {/* Dual-direction evaluation */}
        {state.strategyConditions?.long && state.strategyConditions?.short && (
          <div
            className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-border/70 bg-muted/30 px-3 py-2"
            data-testid={`dual-direction-${coin}`}
          >
            <span className="font-mono-data text-[10px] font-semibold text-accent">
              LONG {state.strategyConditions.long.passCount}/6 · {state.strategyConditions.long.score}/8
            </span>
            <span className="font-mono-data text-[10px] font-semibold text-destructive">
              SHORT {state.strategyConditions.short.passCount}/6 · {state.strategyConditions.short.score}/8
            </span>
            <span className="ml-auto font-mono-data text-[9px] font-bold uppercase tracking-wider text-muted-foreground">
              decision: <span className={
                (state.strategyConditions.decision ?? state.signal) === 'LONG' ? 'text-accent'
                : (state.strategyConditions.decision ?? state.signal) === 'SHORT' ? 'text-destructive'
                : 'text-muted-foreground'
              }>{(state.strategyConditions.decision ?? state.signal).replace('_', ' ')}</span>
            </span>
          </div>
        )}

        {/* Opportunity panel */}
        {state.opportunity && <OpportunityPanel opportunity={state.opportunity} coin={coin} />}

        {/* Independent LONG/SHORT directional scores (all assets) */}
        {state.directional && <DirectionalPanel directional={state.directional} coin={coin} />}

        {/* Why is an entry not happening right now? */}
        {state.executionDiagnostics && <ExecutionDiagnosticsPanel diag={state.executionDiagnostics} coin={coin} />}

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
          <PositionPanel position={state.position} coin={coin} sym={sym} />
        )}
      </div>

      {/* Footer metrics */}
      <div className="border-t border-border/70 px-5 py-3">
        <div className="grid grid-cols-3 gap-3 text-[10px]">
          <div>
            <p className="uppercase tracking-[0.12em] text-muted-foreground">Balance</p>
            <p className="mt-0.5 font-mono-data font-semibold">{money(state.metrics.virtualBalance, 2, sym)}</p>
          </div>
          <div>
            <p className="uppercase tracking-[0.12em] text-muted-foreground">P&L</p>
            <p className={`mt-0.5 font-mono-data font-semibold ${positivePnl ? 'text-accent' : 'text-destructive'}`}>
              {state.metrics.totalProfitLoss >= 0 ? '+' : ''}{money(state.metrics.totalProfitLoss, 2, sym)}
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
            <div><span className="text-muted-foreground">EMA 20</span><span className="ml-2 font-mono-data">{money(state.indicators.ema20, 4, sym)}</span></div>
            <div><span className="text-muted-foreground">EMA 50</span><span className="ml-2 font-mono-data">{money(state.indicators.ema50, 4, sym)}</span></div>
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
              const sym = curSym(state.instrument.currency);
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
                  <td className="px-3 py-3.5 font-mono-data text-xs">{money(pos.entry, 4, sym)}</td>
                  <td className="px-3 py-3.5 font-mono-data text-xs">{pos.currentPrice ? money(pos.currentPrice, 4, sym) : '—'}</td>
                  <td className="px-3 py-3.5 text-[11px]">
                    <p className="font-mono-data text-destructive/80">{money(pos.stopLoss, 4, sym)}</p>
                    <p className="font-mono-data text-accent">{money(pos.takeProfit, 4, sym)}</p>
                  </td>
                  <td className="px-3 py-3.5 font-mono-data text-[11px] text-muted-foreground">{dateTime(pos.openedAt)}</td>
                  <td className={`px-5 py-3.5 text-right font-mono-data text-xs font-semibold sm:px-6 ${pnl == null ? 'text-muted-foreground' : pnl >= 0 ? 'text-accent' : 'text-destructive'}`}>
                    {pnl == null ? '—' : `${pnl >= 0 ? '+' : ''}${money(pnl, 2, sym)}`}
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
    GOLD: 'bg-yellow-500/15 text-yellow-400',
    SILVER: 'bg-slate-400/15 text-slate-300',
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
const COINS = ['BTC', 'ETH', 'SOL', 'XRP', 'GOLD', 'SILVER'] as const;
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

  // £ totals cover the four GBP crypto accounts only — metals are USD-denominated
  // and shown per-account to avoid mixing currencies in one sum.
  const CRYPTO_COINS  = ['BTC', 'ETH', 'SOL', 'XRP'] as const;
  const totalBalance  = CRYPTO_COINS.reduce((s, c) => s + (multiState[c]?.metrics.virtualBalance ?? 0), 0);
  const totalStarting = CRYPTO_COINS.reduce((s, c) => s + (multiState[c]?.metrics.startingBalance ?? 0), 0);
  const totalPnl      = totalBalance - totalStarting;
  const totalRoi      = totalStarting > 0 ? totalPnl / totalStarting * 100 : 0;
  const totalTrades   = COINS.reduce((s, c) => s + (multiState[c]?.metrics.numberOfTrades ?? 0), 0);
  const openPositions = COINS.filter((c) => multiState[c]?.position != null).length;
  const allStarting   = COINS.reduce((s, c) => s + (multiState[c]?.metrics.startingBalance ?? 0), 0);
  const capitalAtRisk = COINS.reduce((s, c) => s + (multiState[c]?.position?.riskAmount ?? 0), 0);
  const riskPct       = allStarting > 0 ? (capitalAtRisk / allStarting) * 100 : 0;

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
    if (!window.confirm('Reset ALL six virtual accounts (crypto £100, metals $100) and clear their entire trade history? This cannot be undone.')) return;
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
    <TradingShell eyebrow="Live desk" title="Portfolio dashboard" subtitle="BTC · ETH · SOL · XRP (£100 each) · GOLD · SILVER ($100 each) — six simulated paper accounts. Metals: unvalidated strategy, paper trading only.">
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
              { label: 'Starting capital', value: money(totalStarting), sub: 'across 4 crypto accounts (£)', tone: '' },
              { label: 'Total P&L', value: `${totalPnl >= 0 ? '+' : ''}${money(totalPnl)}`, sub: `crypto ROI ${pct(totalRoi)}`, tone: totalPnl >= 0 ? 'text-accent' : 'text-destructive' },
              { label: 'Total trades', value: num(totalTrades, 0), sub: 'across all 6 accounts', tone: '' },
              { label: 'Open positions', value: `${num(openPositions, 0)}/6`, sub: `capital at risk ${num(capitalAtRisk)} (${riskPct.toFixed(2)}% · ceiling 2%)`, tone: openPositions > 0 ? 'text-primary' : '' },
            ].map(({ label, value, sub, tone }) => (
              <div key={label} className="rounded-xl border border-border/60 bg-background px-4 py-3.5">
                <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground">{label}</p>
                <p className={`mt-1.5 font-mono-data text-lg font-medium ${tone}`}>{value}</p>
                <p className="mt-0.5 text-[10px] text-muted-foreground">{sub}</p>
              </div>
            ))}
          </div>
        </section>

        {/* ─── Background engine status (scans continue with browser closed) ── */}
        <EngineStatusStrip lastCandleAt={multiState.BTC?.market.lastCompletedCandleAt} />

        {/* ─── Latest strategy scan strip ───────────────────────────────── */}
        <LatestScanStrip coins={COINS} multiState={multiState as unknown as Record<string, PaperTraderState>} />

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
          <div className="grid gap-px sm:grid-cols-2 xl:grid-cols-3">
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
                    <span className={`font-mono-data font-semibold ${s.botStatus === 'RISK_PAUSED' ? 'text-amber-400' : s.botStatus === 'MONITORING' ? 'text-yellow-400' : s.botStatus === 'READY' ? 'text-accent' : 'text-muted-foreground'}`}>
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
