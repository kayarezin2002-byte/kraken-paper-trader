import { useMemo, useState } from 'react';
import { ArrowDownRight, ArrowUpRight, Briefcase, LineChart, X } from 'lucide-react';
import {
  useGetMultiCoinState,
  getGetMultiCoinStateQueryKey,
  useGetScannerPositions,
  getGetScannerPositionsQueryKey,
  type PaperTraderState,
  type OpenPosition,
  type ScannerPosition,
} from '@workspace/api-client-react';
import { TradingShell } from '@/components/trading-shell';
import { AssetChart } from '@/components/asset-chart';

const COINS = ['BTC', 'ETH', 'SOL', 'XRP', 'GOLD', 'SILVER'] as const;

const COIN_COLORS: Record<string, string> = {
  BTC: 'bg-amber-500/15 text-amber-400',
  ETH: 'bg-violet-500/15 text-violet-400',
  SOL: 'bg-green-500/15 text-green-400',
  XRP: 'bg-blue-500/15 text-blue-400',
  GOLD: 'bg-yellow-500/15 text-yellow-400',
  SILVER: 'bg-slate-400/15 text-slate-300',
};

type StrategyLabel = 'ACTIVE' | 'HIGH-CONFIDENCE' | 'SCANNER';

interface OpenTrade {
  coin: string;
  strategy: StrategyLabel;
  currency: string;
  pos: OpenPosition;
  state?: PaperTraderState;
}

/** Scanner positions come from the $ SCANNER account — reshape to OpenPosition. */
function scannerToOpenPosition(p: ScannerPosition): OpenPosition {
  const pct = p.currentPrice != null && p.entry
    ? ((p.direction === 'LONG' ? p.currentPrice - p.entry : p.entry - p.currentPrice) / p.entry) * 100
    : null;
  return {
    direction: p.direction as OpenPosition['direction'],
    entry: p.entry,
    stopLoss: p.stopLoss,
    initialStop: p.initialStop ?? null,
    takeProfit: p.takeProfit,
    quantity: p.quantity,
    riskAmount: p.riskAmount ?? null,
    openedAt: p.openedAt,
    currentPrice: p.currentPrice ?? null,
    unrealisedPnl: p.unrealisedPnl ?? null,
    unrealisedPct: pct != null ? Math.round(pct * 100) / 100 : null,
    bestPrice: p.bestPrice ?? null,
    worstPrice: p.worstPrice ?? null,
    longScore: p.longScore ?? null,
    shortScore: p.shortScore ?? null,
  } as OpenPosition;
}

const sym = (c: string) => (c === 'USD' ? '$' : '£');
const fmt = (v: number | null | undefined, d = 2) =>
  v == null ? '—' : v.toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d });
const price = (v: number | null | undefined, cur: string) =>
  v == null ? '—' : `${sym(cur)}${fmt(v, v < 10 ? 4 : 2)}`;
const dateTime = (v?: string | null) =>
  v ? new Date(v).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—';

function holdTime(openedAt: string): string {
  const s = Math.max(0, (Date.now() - Date.parse(openedAt)) / 1000);
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

/** 0 = at stop, 0.5 = at entry, 1 = at take-profit (direction aware). */
function progress(pos: OpenPosition): number | null {
  const p = pos.currentPrice;
  if (p == null) return null;
  const stopSide = Math.abs(pos.entry - pos.stopLoss);
  const tpSide = Math.abs(pos.takeProfit - pos.entry);
  if (stopSide <= 0 || tpSide <= 0) return null;
  const isLong = pos.direction === 'LONG';
  const fromEntry = isLong ? p - pos.entry : pos.entry - p; // + = towards TP
  const frac = fromEntry >= 0
    ? 0.5 + 0.5 * Math.min(1, fromEntry / tpSide)
    : 0.5 - 0.5 * Math.min(1, -fromEntry / stopSide);
  return Math.max(0, Math.min(1, frac));
}

function TradeCard({ t, onView }: { t: OpenTrade; onView: (t: OpenTrade) => void }) {
  const { pos, coin, strategy, currency } = t;
  const isLong = pos.direction === 'LONG';
  const pnl = pos.unrealisedPnl;
  const inProfit = (pnl ?? 0) >= 0;
  const cur = pos.currentPrice;
  const qty = pos.quantity;
  const score = isLong ? pos.longScore : pos.shortScore;
  const maxScore = strategy === 'ACTIVE' || strategy === 'SCANNER' ? 6 : coin === 'GOLD' || coin === 'SILVER' ? 6 : 8;
  const distStop = cur != null ? Math.abs(cur - pos.stopLoss) / cur * 100 : null;
  const distTp = cur != null ? Math.abs(pos.takeProfit - cur) / cur * 100 : null;
  const initialStop = pos.initialStop ?? pos.stopLoss;
  const beArmed = isLong ? pos.stopLoss >= pos.entry : pos.stopLoss <= pos.entry;
  const trailing = pos.stopLoss !== initialStop;
  const best = pos.bestPrice;
  const worst = pos.worstPrice;
  const bestPnl = best != null ? (isLong ? best - pos.entry : pos.entry - best) * qty : null;
  const worstPnl = worst != null ? (isLong ? worst - pos.entry : pos.entry - worst) * qty : null;
  const prog = progress(pos);

  return (
    <div className="rounded-2xl border border-border/80 bg-card p-4 shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)] sm:p-5" data-testid={`open-trade-${coin}-${strategy}`}>
      {/* header */}
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded px-1.5 py-0.5 font-mono-data text-[11px] font-bold uppercase ${COIN_COLORS[coin] ?? 'bg-muted'}`}>{coin}</span>
        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono-data text-[10px] font-bold ${isLong ? 'bg-accent/15 text-accent' : 'bg-destructive/15 text-destructive'}`}>
          {isLong ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}{pos.direction}
        </span>
        <span className={`rounded px-1.5 py-0.5 font-mono-data text-[9px] font-bold uppercase ${strategy === 'ACTIVE' ? 'bg-cyan-500/15 text-cyan-500' : strategy === 'SCANNER' ? 'bg-purple-500/15 text-purple-400' : 'bg-blue-500/10 text-blue-500'}`}>{strategy}</span>
        <span className={`ml-auto rounded px-1.5 py-0.5 font-mono-data text-[10px] font-bold uppercase ${inProfit ? 'bg-accent/15 text-accent' : 'bg-destructive/15 text-destructive'}`}>
          {inProfit ? 'PROFIT' : 'LOSS'}
        </span>
      </div>

      {/* P&L headline */}
      <div className="mt-3 flex items-end gap-3">
        <span className={`font-mono-data text-2xl font-medium tracking-[-0.05em] ${inProfit ? 'text-accent' : 'text-destructive'}`} data-testid={`pnl-${coin}-${strategy}`}>
          {pnl == null ? '—' : `${pnl >= 0 ? '+' : ''}${sym(currency)}${fmt(pnl)}`}
        </span>
        <span className={`mb-0.5 font-mono-data text-xs font-semibold ${inProfit ? 'text-accent' : 'text-destructive'}`}>
          {pos.unrealisedPct == null ? '' : `${pos.unrealisedPct >= 0 ? '+' : ''}${fmt(pos.unrealisedPct)}%`}
        </span>
      </div>

      {/* progress bar SL ← ENTRY → TP */}
      {prog != null && (
        <div className="mt-3">
          <div className="flex justify-between font-mono-data text-[9px] uppercase tracking-wider text-muted-foreground">
            <span className="text-destructive">SL {price(pos.stopLoss, currency)}</span>
            <span>entry {price(pos.entry, currency)}</span>
            <span className="text-accent">TP {price(pos.takeProfit, currency)}</span>
          </div>
          <div className="relative mt-1 h-2 rounded-full bg-muted">
            <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
            <div
              className={`absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card shadow ${inProfit ? 'bg-accent' : 'bg-destructive'}`}
              style={{ left: `${prog * 100}%` }}
            />
          </div>
        </div>
      )}

      {/* detail grid */}
      <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px] sm:grid-cols-3">
        {([
          ['Entry', price(pos.entry, currency)],
          ['Current', price(cur, currency)],
          ['Size', `${qty < 1 ? qty.toFixed(6) : fmt(qty, 4)} ${coin}`],
          ['Opened', dateTime(pos.openedAt)],
          ['Held', holdTime(pos.openedAt)],
          ['Entry score', score != null ? `${score}/${maxScore}` : '—'],
          ['Stop loss', price(pos.stopLoss, currency)],
          ['Take profit', price(pos.takeProfit, currency)],
          ['Risk', `${sym(currency)}${fmt(pos.riskAmount)}`],
          ['To stop', distStop != null ? `${fmt(distStop)}%` : '—'],
          ['To target', distTp != null ? `${fmt(distTp)}%` : '—'],
          ['Best / worst', bestPnl != null ? `${bestPnl >= 0 ? '+' : ''}${sym(currency)}${fmt(bestPnl)} / ${worstPnl != null ? `${worstPnl >= 0 ? '+' : ''}${sym(currency)}${fmt(worstPnl)}` : '—'}` : '—'],
        ] as const).map(([k, v]) => (
          <div key={k}>
            <p className="text-[9px] font-bold uppercase tracking-[0.12em] text-muted-foreground">{k}</p>
            <p className="font-mono-data text-[11px]">{v}</p>
          </div>
        ))}
      </div>

      {/* stop management status */}
      <div className="mt-3 flex flex-wrap gap-1.5">
        <span className={`rounded px-1.5 py-0.5 font-mono-data text-[9px] font-bold uppercase ${beArmed ? 'bg-accent/15 text-accent' : 'bg-muted text-muted-foreground'}`}>
          break-even {beArmed ? 'armed — stop at/beyond entry' : 'not armed'}
        </span>
        <span className={`rounded px-1.5 py-0.5 font-mono-data text-[9px] font-bold uppercase ${trailing ? 'bg-cyan-500/15 text-cyan-500' : 'bg-muted text-muted-foreground'}`}>
          trailing stop {trailing ? `active (from ${price(initialStop, currency)})` : 'inactive'}
        </span>
      </div>

      <button
        type="button"
        onClick={() => onView(t)}
        className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg border border-primary/25 bg-primary/10 px-3 py-2 text-xs font-bold text-primary transition-colors hover:bg-primary/15"
        data-testid={`button-view-trade-${coin}-${strategy}`}
      >
        <LineChart size={14} /> View trade
      </button>
    </div>
  );
}

function TradeChartModal({ trade, onClose }: { trade: OpenTrade; onClose: () => void }) {
  const opened = Date.parse(trade.pos.openedAt);
  const ageDays = (Date.now() - opened) / 86400000;
  const range = ageDays <= 0.7 ? '24H' : ageDays <= 6 ? '7D' : ageDays <= 28 ? '30D' : '90D';
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-6" onClick={onClose}>
      <div className="max-h-[92vh] w-full overflow-y-auto rounded-t-2xl border border-border/80 bg-card p-4 sm:max-w-3xl sm:rounded-2xl sm:p-5" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-bold">
            Open trade — {trade.coin} {trade.pos.direction}
            <span className={`ml-2 rounded px-1.5 py-0.5 font-mono-data text-[9px] font-bold uppercase ${trade.strategy === 'ACTIVE' ? 'bg-cyan-500/15 text-cyan-500' : trade.strategy === 'SCANNER' ? 'bg-purple-500/15 text-purple-400' : 'bg-blue-500/10 text-blue-500'}`}>{trade.strategy}</span>
          </h3>
          <button type="button" onClick={onClose} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted" data-testid="button-close-trade-chart">
            <X size={16} />
          </button>
        </div>
        <AssetChart
          asset={trade.coin}
          trades={[]}
          position={trade.strategy === 'HIGH-CONFIDENCE' ? trade.pos : null}
          activePosition={trade.strategy !== 'HIGH-CONFIDENCE' ? trade.pos : null}
          currentPrice={trade.pos.currentPrice ?? undefined}
          defaultRange={range as '24H' | '7D' | '30D' | '90D'}
        />
      </div>
    </div>
  );
}

const STRAT_FILTERS = ['ALL', 'LONG', 'SHORT', 'ACTIVE STRATEGY', 'HIGH-CONFIDENCE', 'SCANNER'] as const;
type StratFilter = typeof STRAT_FILTERS[number];

export default function OpenTrades() {
  const [filter, setFilter] = useState<StratFilter>('ALL');
  const [coinFilter, setCoinFilter] = useState<string>('ALL');
  const [viewTrade, setViewTrade] = useState<OpenTrade | null>(null);

  const multiStateQuery = useGetMultiCoinState({
    query: { queryKey: getGetMultiCoinStateQueryKey(), refetchInterval: 15000 },
  });
  const scannerQuery = useGetScannerPositions({
    query: { queryKey: getGetScannerPositionsQueryKey(), refetchInterval: 15000 },
  });
  const ms = multiStateQuery.data;
  const scannerPositions = scannerQuery.data;

  const trades = useMemo<OpenTrade[]>(() => {
    const out: OpenTrade[] = [];
    for (const p of scannerPositions ?? []) {
      out.push({ coin: p.ticker, strategy: 'SCANNER', currency: 'USD', pos: scannerToOpenPosition(p) });
    }
    if (!ms) return out;
    for (const coin of COINS) {
      const state = (ms as unknown as Record<string, PaperTraderState>)[coin];
      if (!state) continue;
      const currency = state.instrument?.currency ?? (coin === 'GOLD' || coin === 'SILVER' ? 'USD' : 'GBP');
      if (state.position) out.push({ coin, strategy: 'HIGH-CONFIDENCE', currency, pos: state.position, state });
      if (state.activePosition) out.push({ coin, strategy: 'ACTIVE', currency, pos: state.activePosition, state });
    }
    return out;
  }, [ms, scannerPositions]);

  const filtered = trades.filter((t) => {
    if (coinFilter !== 'ALL' && t.coin !== coinFilter) return false;
    if (filter === 'LONG') return t.pos.direction === 'LONG';
    if (filter === 'SHORT') return t.pos.direction === 'SHORT';
    if (filter === 'ACTIVE STRATEGY') return t.strategy === 'ACTIVE';
    if (filter === 'HIGH-CONFIDENCE') return t.strategy === 'HIGH-CONFIDENCE';
    if (filter === 'SCANNER') return t.strategy === 'SCANNER';
    return true;
  });

  // Currency-aware totals: crypto in £, metals in $ — never mixed.
  const totals = (cur: string) => {
    const subset = trades.filter((t) => t.currency === cur);
    return {
      pnl: subset.reduce((s, t) => s + (t.pos.unrealisedPnl ?? 0), 0),
      risk: subset.reduce((s, t) => s + (t.pos.riskAmount ?? 0), 0),
      count: subset.length,
    };
  };
  const gbp = totals('GBP');
  const usd = totals('USD');
  const longs = trades.filter((t) => t.pos.direction === 'LONG').length;
  const shorts = trades.length - longs;

  return (
    <TradingShell eyebrow="Live desk" title="Open trades" subtitle="Every currently active paper position across all six accounts — live prices, unrealised P&L, and stop management status. Updates automatically.">
      <div className="space-y-5">
        {/* ─── Summary strip ────────────────────────────────────────────── */}
        <section className="rise-in rounded-2xl border border-border/80 bg-card p-5 shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)] sm:p-6" data-testid="open-trades-summary">
          <div className="flex items-center gap-2">
            <Briefcase size={16} className="text-primary" />
            <span className="font-mono-data text-[10px] font-bold uppercase tracking-[0.18em] text-muted-foreground">Open positions</span>
            <span className="flex items-center gap-1.5 font-mono-data text-[10px] text-muted-foreground/70">
              <span className="h-1.5 w-1.5 rounded-full bg-accent pulse-dot" /> live
            </span>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="rounded-xl bg-background p-3.5">
              <p className="text-[9px] font-bold uppercase tracking-[0.14em] text-muted-foreground">Open positions</p>
              <p className="mt-1.5 font-mono-data text-xl" data-testid="stat-open-count">{trades.length}</p>
            </div>
            <div className="rounded-xl bg-background p-3.5">
              <p className="text-[9px] font-bold uppercase tracking-[0.14em] text-muted-foreground">Unrealised P&L</p>
              <p className="mt-1.5 space-x-2 font-mono-data text-sm" data-testid="stat-unrealised">
                <span className={gbp.pnl >= 0 ? 'text-accent' : 'text-destructive'}>{gbp.pnl >= 0 ? '+' : ''}£{fmt(gbp.pnl)}</span>
                {usd.count > 0 && <span className={usd.pnl >= 0 ? 'text-accent' : 'text-destructive'}>{usd.pnl >= 0 ? '+' : ''}${fmt(usd.pnl)}</span>}
              </p>
            </div>
            <div className="rounded-xl bg-background p-3.5">
              <p className="text-[9px] font-bold uppercase tracking-[0.14em] text-muted-foreground">Capital at risk</p>
              <p className="mt-1.5 space-x-2 font-mono-data text-sm">
                <span>£{fmt(gbp.risk)}</span>
                {usd.count > 0 && <span>${fmt(usd.risk)}</span>}
              </p>
            </div>
            <div className="rounded-xl bg-background p-3.5">
              <p className="text-[9px] font-bold uppercase tracking-[0.14em] text-muted-foreground">Long / short</p>
              <p className="mt-1.5 font-mono-data text-xl"><span className="text-accent">{longs}</span> / <span className="text-destructive">{shorts}</span></p>
            </div>
          </div>
        </section>

        {/* ─── Filters ──────────────────────────────────────────────────── */}
        <div className="space-y-2">
          <div className="flex gap-2 overflow-x-auto pb-1">
            {STRAT_FILTERS.map((f) => (
              <button key={f} onClick={() => setFilter(f)} data-testid={`filter-${f.toLowerCase().replace(/ /g, '-')}`}
                className={`shrink-0 rounded-lg px-3 py-1.5 font-mono-data text-[10px] font-bold uppercase tracking-wider transition-all ${filter === f ? 'bg-primary text-white' : 'border border-border bg-card text-muted-foreground hover:text-foreground'}`}>
                {f}
              </button>
            ))}
          </div>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {['ALL', ...COINS, ...Array.from(new Set(trades.filter((t) => t.strategy === 'SCANNER').map((t) => t.coin)))].map((c) => (
              <button key={c} onClick={() => setCoinFilter(c)}
                className={`shrink-0 rounded-lg px-3 py-1.5 font-mono-data text-[10px] font-bold uppercase tracking-wider transition-all ${coinFilter === c ? 'bg-primary text-white' : 'border border-border bg-card text-muted-foreground hover:text-foreground'}`}>
                {c}
              </button>
            ))}
          </div>
        </div>

        {/* ─── Cards ────────────────────────────────────────────────────── */}
        {multiStateQuery.isLoading && !ms ? (
          <div className="space-y-3">{[1, 2].map((i) => <div key={i} className="h-48 rounded-2xl skeleton-shimmer" />)}</div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center rounded-2xl border border-border/80 bg-card px-5 py-16 text-center" data-testid="empty-open-trades">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-muted text-muted-foreground"><Briefcase size={22} /></div>
            <p className="mt-3 text-sm font-bold">{trades.length === 0 ? 'No open positions' : 'No positions match this filter'}</p>
            <p className="mt-1 max-w-sm text-xs leading-relaxed text-muted-foreground">
              {trades.length === 0 ? 'When a strategy signal qualifies, the position will appear here with live P&L and stop management.' : 'Try a different filter.'}
            </p>
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {filtered.map((t) => <TradeCard key={`${t.coin}-${t.strategy}`} t={t} onView={setViewTrade} />)}
          </div>
        )}
      </div>

      {viewTrade && <TradeChartModal trade={viewTrade} onClose={() => setViewTrade(null)} />}
    </TradingShell>
  );
}
