import { useMemo, useState } from 'react';
import { AlertTriangle, ArrowDownRight, ArrowUpRight, BookOpen, Filter, History as HistoryIcon, X } from 'lucide-react';
import { useListAllTrades, getListAllTradesQueryKey, useGetPortfolioSummary, getGetPortfolioSummaryQueryKey, useGetMultiCoinState, getGetMultiCoinStateQueryKey, type PaperTrade } from '@workspace/api-client-react';
import { TradingShell } from '@/components/trading-shell';
import { AssetChart, type ChartRange } from '@/components/asset-chart';

/** Pick a chart range wide enough to show 24 candles before entry and after exit. */
function reviewRange(trade: PaperTrade): ChartRange {
  const ageDays = (Date.now() - Date.parse(trade.openedAt)) / 86400000;
  if (ageDays <= 0.7) return '24H';
  if (ageDays <= 6) return '7D';
  if (ageDays <= 28) return '30D';
  return '90D';
}

function TradeReviewModal({ trade, onClose }: { trade: PaperTrade; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-6" onClick={onClose}>
      <div
        className="max-h-[92vh] w-full overflow-y-auto rounded-t-2xl border border-border/80 bg-card p-4 sm:max-w-3xl sm:rounded-2xl sm:p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-bold">
            Trade review — {trade.coin} {trade.direction}
            <span className={`ml-2 font-mono-data text-xs ${trade.profitLoss >= 0 ? 'text-accent' : 'text-destructive'}`}>
              {trade.profitLoss >= 0 ? '+' : ''}{trade.profitLoss.toFixed(2)}
            </span>
          </h3>
          <button type="button" onClick={onClose} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted" data-testid="button-close-review">
            <X size={16} />
          </button>
        </div>
        <AssetChart
          asset={trade.coin}
          trades={[trade]}
          focusTrade={trade}
          defaultRange={reviewRange(trade)}
        />
      </div>
    </div>
  );
}

const money = (v: number | null | undefined) =>
  v == null ? '—' : `£${v.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const num = (v: number | null | undefined, d = 2) =>
  v == null ? '—' : v.toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d });
const dateTime = (v?: string | null) =>
  v ? new Date(v).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';

const COINS = ['ALL', 'BTC', 'ETH', 'SOL', 'XRP', 'GOLD', 'SILVER'] as const;
type CoinFilter = typeof COINS[number];

const COIN_COLORS: Record<string, string> = {
  BTC: 'bg-amber-500/15 text-amber-400',
  ETH: 'bg-violet-500/15 text-violet-400',
  SOL: 'bg-green-500/15 text-green-400',
  XRP: 'bg-blue-500/15 text-blue-400',
  GOLD: 'bg-yellow-500/15 text-yellow-400',
  SILVER: 'bg-slate-400/15 text-slate-300',
};

export default function History() {
  const [coinFilter, setCoinFilter] = useState<CoinFilter>('ALL');
  const [reviewTrade, setReviewTrade] = useState<PaperTrade | null>(null);
  const tradesQuery = useListAllTrades(
    { limit: 200 },
    { query: { queryKey: getListAllTradesQueryKey({ limit: 200 }) } },
  );
  const allTrades = tradesQuery.data ?? [];
  const portfolioQuery = useGetPortfolioSummary({ query: { queryKey: getGetPortfolioSummaryQueryKey(), staleTime: 60_000 } });
  const strategyStats = portfolioQuery.data?.strategyStats ?? null;
  const multiStateQuery = useGetMultiCoinState({ query: { queryKey: getGetMultiCoinStateQueryKey(), staleTime: 30_000 } });
  const openPositions = useMemo(() => {
    const ms = multiStateQuery.data;
    if (!ms) return [];
    const out: { coin: string; strategy: 'HIGH-CONFIDENCE' | 'ACTIVE'; direction: string; entry: number; openedAt: string }[] = [];
    for (const coin of ['BTC', 'ETH', 'SOL', 'XRP', 'GOLD', 'SILVER'] as const) {
      const s = (ms as Record<string, any>)[coin];
      if (s?.position) out.push({ coin, strategy: 'HIGH-CONFIDENCE', direction: s.position.direction, entry: s.position.entry, openedAt: s.position.openedAt });
      if (s?.activePosition) out.push({ coin, strategy: 'ACTIVE', direction: s.activePosition.direction, entry: s.activePosition.entry, openedAt: s.activePosition.openedAt });
    }
    return out;
  }, [multiStateQuery.data]);

  const filtered = useMemo(
    () => coinFilter === 'ALL' ? allTrades : allTrades.filter((t) => t.coin === coinFilter),
    [allTrades, coinFilter],
  );
  const ordered = useMemo(
    () => [...filtered].sort((a, b) => new Date(b.closedAt).getTime() - new Date(a.closedAt).getTime()),
    [filtered],
  );

  const profitable = filtered.filter((t) => t.profitLoss > 0).length;
  const totalPnl   = filtered.reduce((s, t) => s + t.profitLoss, 0);
  const winRate    = filtered.length > 0 ? profitable / filtered.length * 100 : 0;

  // Per-coin summary row
  const coinSummary = (['BTC', 'ETH', 'SOL', 'XRP', 'GOLD', 'SILVER'] as const).map((coin) => {
    const coinTrades = allTrades.filter((t) => t.coin === coin);
    const wins = coinTrades.filter((t) => t.profitLoss > 0).length;
    const pnl  = coinTrades.reduce((s, t) => s + t.profitLoss, 0);
    return { coin, count: coinTrades.length, wins, pnl };
  });

  return (
    <TradingShell eyebrow="Review desk" title="Trade history" subtitle="All simulated positions across BTC · ETH · SOL · XRP · GOLD · SILVER.">
      <div className="space-y-6">

        {/* ─── Performance summary ─────────────────────────────────────── */}
        <section className="rise-in rounded-2xl border border-border/80 bg-card p-5 shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)] sm:p-6">
          <div className="flex flex-col gap-5 md:flex-row md:items-end md:justify-between">
            <div>
              <div className="flex items-center gap-2 text-primary"><BookOpen size={17} /><span className="font-mono-data text-[10px] font-medium uppercase tracking-[0.18em]">Performance review</span></div>
              <h2 className="mt-2 text-2xl font-extrabold tracking-[-0.04em]">Evidence over instinct.</h2>
              <p className="mt-1 max-w-xl text-sm leading-relaxed text-muted-foreground">Every closed position across all six accounts. Filter by instrument to study each strategy in isolation. Gold and Silver are monitoring only, so no trades will appear for them.</p>
            </div>
            <div className="flex items-center gap-2 rounded-lg border border-primary/25 bg-primary/10 px-3 py-2 text-xs font-bold text-primary-foreground">
              <Filter size={14} /> Last 200 trades
            </div>
          </div>

          {/* Per-coin summary strip */}
          <div className="mt-6 grid gap-3 border-t border-border/60 pt-5 sm:grid-cols-2 xl:grid-cols-3">
            {coinSummary.map(({ coin, count, wins, pnl }) => (
              <button
                key={coin}
                onClick={() => setCoinFilter((f) => f === coin ? 'ALL' : coin as CoinFilter)}
                className={`rounded-xl border p-3.5 text-left transition-all hover:-translate-y-0.5 ${coinFilter === coin ? 'border-primary/40 bg-primary/5' : 'border-border/60 bg-background'}`}
              >
                <div className="flex items-center justify-between">
                  <span className={`rounded px-1.5 py-0.5 font-mono-data text-[10px] font-bold uppercase ${COIN_COLORS[coin] ?? ''}`}>{coin}</span>
                  <span className={`font-mono-data text-[11px] font-semibold ${pnl >= 0 ? 'text-accent' : 'text-destructive'}`}>{pnl >= 0 ? '+' : ''}{money(pnl)}</span>
                </div>
                <p className="mt-2 font-mono-data text-base font-medium">{count}</p>
                <p className="mt-0.5 text-[10px] text-muted-foreground">{wins}W / {count - wins}L</p>
              </button>
            ))}
          </div>

          {/* Aggregate stats for selected filter */}
          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            <div className="rounded-xl bg-background p-4">
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground">{coinFilter === 'ALL' ? 'Combined' : coinFilter} P&L</p>
              <p className={`mt-2 font-mono-data text-xl ${totalPnl >= 0 ? 'text-accent' : 'text-destructive'}`}>{totalPnl >= 0 ? '+' : ''}{money(totalPnl)}</p>
              <p className="mt-1 text-[11px] text-muted-foreground">selected period</p>
            </div>
            <div className="rounded-xl bg-background p-4">
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground">Closed positions</p>
              <p className="mt-2 font-mono-data text-xl">{filtered.length}</p>
              <p className="mt-1 text-[11px] text-muted-foreground">{profitable} profitable outcomes</p>
            </div>
            <div className="rounded-xl bg-background p-4">
              <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground">Win rate</p>
              <p className={`mt-2 font-mono-data text-xl ${winRate >= 50 ? 'text-accent' : 'text-destructive'}`}>{num(winRate)}%</p>
              <p className="mt-1 text-[11px] text-muted-foreground">selected trades</p>
            </div>
          </div>
        </section>

        {/* ─── Open vs closed at a glance ───────────────────────────────── */}
        <section className="rise-in rounded-2xl border border-border/80 bg-card p-5 shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)] sm:p-6" data-testid="position-summary">
          <div className="grid gap-3 grid-cols-2 sm:grid-cols-4">
            {([
              ['Open positions', openPositions.length, 'text-cyan-500'],
              ['Closed trades', allTrades.length, 'text-foreground'],
              ['Wins', allTrades.filter((t) => t.profitLoss > 0).length, 'text-accent'],
              ['Losses', allTrades.filter((t) => t.profitLoss <= 0).length, 'text-destructive'],
            ] as const).map(([label, v, tone]) => (
              <div key={label} className="rounded-xl bg-background p-4">
                <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-muted-foreground">{label}</p>
                <p className={`mt-2 font-mono-data text-xl ${tone}`}>{v}</p>
              </div>
            ))}
          </div>
          {openPositions.length > 0 && (
            <div className="mt-3 space-y-1.5">
              {openPositions.map((p) => (
                <div key={`${p.coin}-${p.strategy}`} className="flex flex-wrap items-center gap-2 rounded-lg border border-border/60 bg-background px-3 py-2 text-[11px]" data-testid={`open-position-${p.coin}-${p.strategy}`}>
                  <span className={`rounded px-1.5 py-0.5 font-mono-data text-[10px] font-bold uppercase ${COIN_COLORS[p.coin] ?? ''}`}>{p.coin}</span>
                  <span className={`rounded px-1.5 py-0.5 font-mono-data text-[9px] font-bold uppercase ${p.strategy === 'ACTIVE' ? 'bg-cyan-500/15 text-cyan-500' : 'bg-blue-500/10 text-blue-500'}`}>{p.strategy}</span>
                  <span className={`font-mono-data font-semibold ${p.direction === 'LONG' ? 'text-accent' : 'text-destructive'}`}>{p.direction}</span>
                  <span className="text-muted-foreground">entry {num(p.entry, p.entry < 10 ? 4 : 2)} · opened {dateTime(p.openedAt)}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* ─── Strategy performance: HIGH-CONFIDENCE vs ACTIVE vs COMBINED ── */}
        {strategyStats && (
          <section className="rise-in rounded-2xl border border-border/80 bg-card p-5 shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)] sm:p-6" data-testid="strategy-stats">
            <h2 className="text-sm font-extrabold">Strategy performance</h2>
            <p className="mt-1 text-xs text-muted-foreground">HIGH-CONFIDENCE trades on completed 1h candles; ACTIVE trades on completed 15m candles with 1h context. Both run in parallel on every asset. Net P&L subtracts estimated fees + slippage (recorded per trade; paper fills themselves are cost-free).</p>
            <div className="mt-4 grid gap-3 sm:grid-cols-3">
              {([['HIGH-CONFIDENCE', strategyStats.core, 'text-blue-500'], ['ACTIVE', strategyStats.active, 'text-cyan-500'], ['COMBINED', strategyStats.combined, 'text-foreground']] as const).map(([label, s, tone]) => (
                <div key={label} className="rounded-xl border border-border/60 bg-background p-4">
                  <p className={`font-mono-data text-[10px] font-bold uppercase tracking-[0.14em] ${tone}`}>{label}</p>
                  <p className={`mt-2 font-mono-data text-xl ${s.pnl >= 0 ? 'text-accent' : 'text-destructive'}`}>{s.pnl >= 0 ? '+' : ''}{money(s.pnl)}</p>
                  <div className="mt-2 space-y-0.5 text-[11px] text-muted-foreground">
                    <p>{s.trades} trades · {s.wins}W / {s.losses}L · {num(s.winRate)}% win rate</p>
                    <p>ROI {num(s.roi, 3)}% · profit factor {s.profitFactor != null ? num(s.profitFactor) : '—'} · max DD {money(s.maxDrawdown)}</p>
                    {s.estCosts != null && (
                      <p>est. costs {money(s.estCosts)} · net P&L <span className={(s.pnlNet ?? 0) >= 0 ? 'text-accent' : 'text-destructive'}>{(s.pnlNet ?? 0) >= 0 ? '+' : ''}{money(s.pnlNet)}</span></p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* ─── Coin filter tabs ─────────────────────────────────────────── */}
        <div className="flex gap-2 overflow-x-auto pb-1">
          {COINS.map((c) => (
            <button
              key={c}
              onClick={() => setCoinFilter(c)}
              className={`shrink-0 rounded-lg px-4 py-2 font-mono-data text-xs font-bold uppercase tracking-wider transition-all
                ${coinFilter === c
                  ? 'bg-primary text-white'
                  : 'border border-border bg-card text-muted-foreground hover:text-foreground'}`}
            >
              {c}
            </button>
          ))}
        </div>

        {/* ─── Trade table ─────────────────────────────────────────────── */}
        <section className="rise-in rise-in-delay-1 overflow-hidden rounded-2xl border border-border/80 bg-card shadow-[0_10px_32px_hsl(215_35%_13%_/_0.05)]">
          <div className="flex items-center justify-between border-b border-border/70 px-5 py-4 sm:px-6">
            <div>
              <div className="flex items-center gap-2"><HistoryIcon size={16} className="text-primary" /><h2 className="text-sm font-extrabold">Closed positions</h2></div>
              <p className="mt-1 text-xs text-muted-foreground">Execution, context, and the reason each position ended.</p>
            </div>
            <span className="font-mono-data text-[10px] uppercase tracking-wider text-muted-foreground">{tradesQuery.isLoading ? 'loading' : `${ordered.length} records`}</span>
          </div>

          {tradesQuery.isLoading ? (
            <div className="space-y-3 p-5 sm:p-6">{[1,2,3,4].map((i) => <div key={i} className="h-12 rounded-lg skeleton-shimmer" />)}</div>
          ) : tradesQuery.isError ? (
            <div className="flex flex-col items-center px-5 py-16 text-center">
              <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-destructive/10 text-destructive"><AlertTriangle size={20} /></div>
              <p className="mt-3 text-sm font-bold">History unavailable</p>
              <p className="mt-1 text-xs text-muted-foreground">The trade record could not be loaded.</p>
            </div>
          ) : ordered.length === 0 ? (
            <div data-testid="empty-trade-history" className="flex flex-col items-center px-5 py-16 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-muted text-muted-foreground"><HistoryIcon size={22} /></div>
              <p className="mt-3 text-sm font-bold">Your record starts here</p>
              <p className="mt-1 max-w-sm text-xs leading-relaxed text-muted-foreground">
                {coinFilter === 'ALL' ? 'No simulated positions have closed yet.' : `No ${coinFilter} positions have closed yet.`}
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[960px] text-left">
                <thead>
                  <tr className="border-b border-border/70 text-[10px] uppercase tracking-[0.13em] text-muted-foreground">
                    <th className="px-5 py-3 font-bold sm:px-6">Closed / opened</th>
                    <th className="px-3 py-3 font-bold">Coin</th>
                    <th className="px-3 py-3 font-bold">Strategy</th>
                    <th className="px-3 py-3 font-bold">Side</th>
                    <th className="px-3 py-3 font-bold">Entry → exit</th>
                    <th className="px-3 py-3 font-bold">Stop / target</th>
                    <th className="px-3 py-3 font-bold">Context</th>
                    <th className="px-3 py-3 font-bold">Exit reason</th>
                    <th className="px-5 py-3 text-right font-bold sm:px-6">P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {ordered.map((trade) => (
                    <tr key={trade.id} data-testid={`row-history-trade-${trade.id}`} onClick={() => setReviewTrade(trade)} className="cursor-pointer border-b border-border/50 last:border-0 transition-colors hover:bg-muted/35">
                      <td className="px-5 py-4 sm:px-6">
                        <p className="font-mono-data text-xs">{dateTime(trade.closedAt)}</p>
                        <p className="mt-1 text-[10px] text-muted-foreground">opened {dateTime(trade.openedAt)}</p>
                      </td>
                      <td className="px-3 py-4">
                        <span className={`rounded px-1.5 py-0.5 font-mono-data text-[10px] font-bold uppercase ${COIN_COLORS[trade.coin] ?? 'bg-muted text-muted-foreground'}`}>
                          {trade.coin}
                        </span>
                      </td>
                      <td className="px-3 py-4">
                        <span className={`rounded px-1.5 py-0.5 font-mono-data text-[9px] font-bold uppercase ${(trade.strategy ?? 'CORE') === 'ACTIVE' ? 'bg-cyan-500/15 text-cyan-500' : 'bg-blue-500/10 text-blue-500'}`}>
                          {(trade.strategy ?? 'CORE') === 'ACTIVE' ? 'ACTIVE' : 'HIGH-CONF'}
                        </span>
                      </td>
                      <td className="px-3 py-4">
                        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono-data text-[10px] font-medium ${trade.direction === 'LONG' ? 'bg-accent/10 text-accent' : 'bg-destructive/10 text-destructive'}`}>
                          {trade.direction === 'LONG' ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
                          {trade.direction}
                        </span>
                      </td>
                      <td className="px-3 py-4 font-mono-data text-xs">{money(trade.entry)} <span className="text-muted-foreground">→</span> {money(trade.exit)}</td>
                      <td className="px-3 py-4">
                        <p className="font-mono-data text-[11px] text-destructive/80">{money(trade.stopLoss)}</p>
                        <p className="font-mono-data text-[11px] text-accent">{money(trade.takeProfit)}</p>
                      </td>
                      <td className="px-3 py-4">
                        <p className="font-mono-data text-[11px]">RSI {num(trade.rsi, 1)}</p>
                        <p className="mt-1 text-[10px] text-muted-foreground">{trade.trend4h} · MACD {num(trade.macd, 3)}</p>
                      </td>
                      <td className="px-3 py-4 text-xs text-muted-foreground">{trade.exitReason.replaceAll('_', ' ')}</td>
                      <td className={`px-5 py-4 text-right font-mono-data text-xs font-medium sm:px-6 ${trade.profitLoss >= 0 ? 'text-accent' : 'text-destructive'}`}>
                        {trade.profitLoss >= 0 ? '+' : ''}{money(trade.profitLoss)}
                        <p className="mt-1 text-[10px] font-normal text-muted-foreground">{money(trade.accountBalance)} bal.</p>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
      {reviewTrade && <TradeReviewModal trade={reviewTrade} onClose={() => setReviewTrade(null)} />}
    </TradingShell>
  );
}
