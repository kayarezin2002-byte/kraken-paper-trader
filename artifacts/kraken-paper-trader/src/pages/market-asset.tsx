import { useMemo } from 'react';
import { Link, useParams } from 'wouter';
import { ArrowLeft, Check, ShieldAlert, X } from 'lucide-react';
import {
  useGetMarketAsset,
  getGetMarketAssetQueryKey,
  useGetMultiCoinState,
  getGetMultiCoinStateQueryKey,
  useListAllTrades,
  getListAllTradesQueryKey,
  type PaperTraderState,
  type ScannerCondition,
} from '@workspace/api-client-react';
import { TradingShell } from '@/components/trading-shell';
import { AssetChart } from '@/components/asset-chart';

const CORE_COINS = ['BTC', 'ETH', 'SOL', 'XRP'];

const fmt = (v: number | null | undefined, d = 2) =>
  v == null ? '—' : v.toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d });
const usd = (v: number | null | undefined) => {
  if (v == null) return '—';
  const d = v >= 100 ? 2 : v >= 1 ? 4 : 8;
  return `$${v.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: d })}`;
};
const dateTime = (v?: string | null) =>
  v ? new Date(v).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—';

function ConditionList({ conds }: { conds: ScannerCondition[] | null | undefined }) {
  if (!conds?.length) return <p className="text-xs text-muted-foreground">Waiting for the first scan…</p>;
  return (
    <div className="space-y-1">
      {conds.map((c) => (
        <div key={c.name} className="flex items-center gap-2 text-xs">
          {c.pass ? <Check size={13} className="shrink-0 text-accent" /> : <X size={13} className="shrink-0 text-destructive" />}
          <span className={c.pass ? '' : 'text-muted-foreground'}>{c.name}</span>
          <span className="ml-auto truncate font-mono-data text-[10px] text-muted-foreground">{c.currentValue ?? ''}</span>
        </div>
      ))}
    </div>
  );
}

function EnginePanel({ title, long, short, threshold, maxScore, longConds, shortConds }: {
  title: string; long: number | null | undefined; short: number | null | undefined;
  threshold: number | null | undefined; maxScore: number;
  longConds: ScannerCondition[] | null | undefined; shortConds: ScannerCondition[] | null | undefined;
}) {
  return (
    <section className="rounded-2xl border border-border/80 bg-card p-4">
      <div className="flex items-center gap-2">
        <h2 className="text-xs font-extrabold uppercase tracking-[0.13em]">{title}</h2>
        <span className="ml-auto font-mono-data text-[10px] text-muted-foreground">gate {threshold ?? '—'}/{maxScore}</span>
      </div>
      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        <div>
          <p className="mb-1.5 font-mono-data text-[11px] font-bold text-accent">LONG {long ?? '—'}/{maxScore}</p>
          <ConditionList conds={longConds} />
        </div>
        <div>
          <p className="mb-1.5 font-mono-data text-[11px] font-bold text-destructive">SHORT {short ?? '—'}/{maxScore}</p>
          <ConditionList conds={shortConds} />
        </div>
      </div>
    </section>
  );
}

export default function MarketAssetPage() {
  const params = useParams<{ ticker: string }>();
  const ticker = (params.ticker ?? '').toUpperCase();
  const assetQuery = useGetMarketAsset(ticker, {
    query: { queryKey: getGetMarketAssetQueryKey(ticker), refetchInterval: 30000, enabled: !!ticker },
  });
  const isCore = CORE_COINS.includes(ticker);
  const multiQuery = useGetMultiCoinState({
    query: { queryKey: getGetMultiCoinStateQueryKey(), refetchInterval: 30000, enabled: isCore },
  });
  const tradesQuery = useListAllTrades(
    { limit: 500 },
    { query: { queryKey: getListAllTradesQueryKey({ limit: 500 }), refetchInterval: 60000 } },
  );

  const d = assetQuery.data;
  const a = d?.asset;
  const coreState = isCore
    ? (multiQuery.data as unknown as Record<string, PaperTraderState> | undefined)?.[ticker]
    : undefined;
  const assetTrades = useMemo(
    () => (tradesQuery.data ?? []).filter((t) => t.coin === ticker),
    [tradesQuery.data, ticker],
  );

  if (assetQuery.isLoading) {
    return (
      <TradingShell eyebrow="markets" title={ticker} subtitle="Loading asset detail…">
        <p className="py-10 text-center text-sm text-muted-foreground">Loading…</p>
      </TradingShell>
    );
  }
  if (!d?.ok || !a) {
    return (
      <TradingShell eyebrow="markets" title={ticker || 'Unknown'} subtitle="Asset not found">
        <Link href="/markets" className="text-sm text-primary">← Back to markets</Link>
      </TradingShell>
    );
  }

  const pos = d.position;
  const stats = d.stats;

  return (
    <TradingShell eyebrow="markets / crypto" title={`${a.name} (${a.ticker})`} subtitle="Scanner detail — same engine, same rules, paper only">
      <div className="space-y-5">
        <Link href="/markets" className="inline-flex items-center gap-1.5 text-xs font-bold text-muted-foreground hover:text-foreground" data-testid="link-back-markets">
          <ArrowLeft size={14} /> All markets
        </Link>

        {/* header */}
        <section className="rounded-2xl border border-border/80 bg-card p-4 sm:p-5" data-testid="asset-header">
          <div className="flex flex-wrap items-end gap-3">
            <span className="font-mono-data text-2xl font-semibold tracking-tight">{usd(a.price)}</span>
            <span className={`mb-0.5 font-mono-data text-sm font-semibold ${(a.change24h ?? 0) >= 0 ? 'text-accent' : 'text-destructive'}`}>
              {a.change24h == null ? '' : `${a.change24h >= 0 ? '+' : ''}${a.change24h}% 24h`}
            </span>
            {a.signal && <span className="mb-1 ml-auto rounded bg-muted px-2 py-0.5 font-mono-data text-[10px] font-bold">{a.signal}</span>}
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              ['24h high', usd(a.high24)], ['24h low', usd(a.low24)],
              ['15m / 1h / 4h', `${a.trend15m?.slice(0, 4) ?? '—'} / ${a.trend1h?.slice(0, 4) ?? '—'} / ${a.trend4h?.slice(0, 4) ?? '—'}`],
              ['Watchlisted', d.watchlisted ? '★ yes' : 'no'],
            ].map(([l, v]) => (
              <div key={l} className="rounded-lg bg-background px-3 py-2">
                <p className="text-[9px] font-bold uppercase tracking-[0.12em] text-muted-foreground">{l}</p>
                <p className="font-mono-data text-xs font-semibold">{v}</p>
              </div>
            ))}
          </div>
          {a.tradingEnabled === false && a.disabledReason && (
            <p className="mt-3 flex items-center gap-1.5 rounded-lg bg-amber-500/10 px-3 py-2 text-[10px] font-bold uppercase text-amber-500">
              <ShieldAlert size={13} /> {a.disabledReason}
            </p>
          )}
        </section>

        {/* Elliott Wave read (experimental — observation only) */}
        {a.elliott && (
          <section className="rounded-2xl border border-border/80 bg-card p-4" data-testid="asset-elliott">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-xs font-extrabold uppercase tracking-[0.13em]">Elliott Wave read</h2>
              <span className="rounded bg-amber-500/10 px-1.5 py-0.5 text-[8px] font-bold uppercase text-amber-500">Experimental — never gates trades</span>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className={`rounded px-2 py-1 font-mono-data text-[11px] font-bold ${a.elliott.direction === 'BULLISH' ? 'bg-accent/15 text-accent' : a.elliott.direction === 'BEARISH' ? 'bg-destructive/15 text-destructive' : 'bg-muted text-muted-foreground'}`}>
                {a.elliott.structure === 'UNCERTAIN' ? 'UNCERTAIN' : `${a.elliott.structure} · Wave ${a.elliott.wave ?? '?'} · ${a.elliott.direction}`}
              </span>
              <span className="font-mono-data text-[11px] text-muted-foreground">{a.elliott.confidence}% confidence ({a.elliott.confidenceLabel})</span>
              {a.elliott.alignment && <span className="rounded bg-muted px-1.5 py-0.5 text-[9px] font-bold uppercase">{a.elliott.alignment}</span>}
            </div>
            <div className="mt-3 grid grid-cols-3 gap-2">
              {(['15m', '1h', '4h'] as const).map((tf) => {
                const t = a.elliott?.timeframes?.[tf];
                return (
                  <div key={tf} className="rounded-lg bg-background px-3 py-2">
                    <p className="text-[9px] font-bold uppercase tracking-[0.12em] text-muted-foreground">{tf}</p>
                    <p className="font-mono-data text-xs font-semibold">
                      {!t || t.structure === 'UNCERTAIN' ? 'Uncertain' : `${t.structure === 'IMPULSE' ? 'W' : 'ABC-'}${t.wave ?? '?'} ${t.direction.slice(0, 4)}`}
                    </p>
                    <p className="font-mono-data text-[10px] text-muted-foreground">{t ? `${t.confidence}%` : '—'}</p>
                  </div>
                );
              })}
            </div>
            {(a.elliott.wave3Candidate || a.elliott.wave5Exhaustion || a.elliott.abcCandidate || a.elliott.fibLocation) && (
              <div className="mt-2 space-y-1 text-[10px]">
                {a.elliott.wave3Candidate && <p className="font-bold uppercase text-primary">⚡ Potential Wave 3 in progress</p>}
                {a.elliott.wave5Exhaustion && <p className="font-bold uppercase text-amber-500">⚠ Wave 5 exhaustion warning</p>}
                {a.elliott.abcCandidate && <p className="font-bold uppercase text-muted-foreground">ABC correction candidate</p>}
                {a.elliott.fibLocation && <p className="text-muted-foreground">Price is {a.elliott.fibLocation} of the last swing.</p>}
              </div>
            )}
          </section>
        )}

        {/* engines */}
        <EnginePanel
          title="Active engine (15m)" maxScore={6}
          long={a.longScore} short={a.shortScore} threshold={a.threshold}
          longConds={a.longConditions} shortConds={a.shortConditions}
        />
        {isCore && coreState?.directional && (
          <EnginePanel
            title="High-confidence engine (1h)" maxScore={coreState.directional.maxScore ?? 8}
            long={coreState.directional.longScore} short={coreState.directional.shortScore}
            threshold={coreState.directional.threshold}
            longConds={coreState.directional.longConditions as unknown as ScannerCondition[]}
            shortConds={coreState.directional.shortConditions as unknown as ScannerCondition[]}
          />
        )}

        {/* open position */}
        {pos && (
          <section className="rounded-2xl border border-primary/30 bg-primary/5 p-4" data-testid="asset-position">
            <h2 className="text-xs font-extrabold uppercase tracking-[0.13em] text-primary">Open scanner position</h2>
            <div className="mt-2 grid grid-cols-2 gap-2 font-mono-data text-xs sm:grid-cols-4">
              <span>{pos.direction} · entry {usd(pos.entry)}</span>
              <span>SL {usd(pos.stopLoss)}</span>
              <span>TP {usd(pos.takeProfit)}</span>
              <span>risk ${fmt(pos.riskAmount)}</span>
            </div>
          </section>
        )}

        {/* chart with trades */}
        <section className="rounded-2xl border border-border/80 bg-card p-4">
          <AssetChart
            asset={ticker}
            trades={assetTrades}
            position={isCore ? coreState?.position : undefined}
            activePosition={isCore ? coreState?.activePosition : (pos as never)}
            currentPrice={a.price}
          />
        </section>

        {/* diagnostics */}
        <section className="rounded-2xl border border-border/80 bg-card p-4" data-testid="asset-diagnostics">
          <h2 className="text-xs font-extrabold uppercase tracking-[0.13em]">Scanner diagnostics</h2>
          <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-3">
            {[
              ['Last scan', dateTime(a.lastScanAt)],
              ['Next scan', dateTime(a.nextScanAt)],
              ['LONG / SHORT', `${a.longScore ?? '—'} / ${a.shortScore ?? '—'}`],
              ['Winning direction', a.decision ?? '—'],
              ['Entry threshold', `${a.threshold ?? '—'}/6`],
              ['Position', pos ? `${pos.direction} open` : isCore && (coreState?.position || coreState?.activePosition) ? 'open (£ account)' : 'none'],
              ['Last trade', dateTime(d.lastTradeAt)],
              ['Last signal change', dateTime(a.lastSignalChange?.at)],
              ['Trading enabled', a.tradingEnabled ? 'YES' : `NO${isCore ? ' (uses £ account)' : ''}`],
            ].map(([l, v]) => (
              <div key={l} className="flex justify-between gap-2 border-b border-border/40 py-1 last:border-0">
                <span className="text-muted-foreground">{l}</span>
                <span className="text-right font-mono-data">{v}</span>
              </div>
            ))}
          </div>
          {!a.tradingEnabled && a.disabledReason && (
            <p className="mt-2 text-[10px] text-muted-foreground">Reason: {a.disabledReason}</p>
          )}
        </section>

        {/* per-asset stats */}
        {stats && stats.trades > 0 && (
          <section className="rounded-2xl border border-border/80 bg-card p-4" data-testid="asset-stats">
            <h2 className="text-xs font-extrabold uppercase tracking-[0.13em]">Strategy performance — {ticker}</h2>
            <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {[
                ['Trades', String(stats.trades)],
                ['Win rate', stats.winRate == null ? '—' : `${stats.winRate}%`],
                ['P&L', `${stats.pnl >= 0 ? '+' : ''}${fmt(stats.pnl)}`],
                ['Profit factor', stats.profitFactor == null ? '—' : String(stats.profitFactor)],
              ].map(([l, v]) => (
                <div key={l} className="rounded-lg bg-background px-3 py-2">
                  <p className="text-[9px] font-bold uppercase tracking-[0.12em] text-muted-foreground">{l}</p>
                  <p className="font-mono-data text-sm font-semibold">{v}</p>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </TradingShell>
  );
}
