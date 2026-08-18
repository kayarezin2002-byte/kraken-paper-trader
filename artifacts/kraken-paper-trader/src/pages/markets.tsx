import { useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link } from 'wouter';
import {
  ArrowDownRight, ArrowUpRight, Flame, Grid3X3, List, Search, Star, TrendingDown, TrendingUp, Zap,
} from 'lucide-react';
import {
  useGetMarketDirectory,
  getGetMarketDirectoryQueryKey,
  useToggleWatchlist,
  useGetMultiCoinState,
  getGetMultiCoinStateQueryKey,
  useGetElliottLab,
  getGetElliottLabQueryKey,
  type ScannerAsset,
  type PaperTraderState,
  type ElliottLabCandidate,
} from '@workspace/api-client-react';
import { TradingShell } from '@/components/trading-shell';
import { AssetChart } from '@/components/asset-chart';

const SIGNAL_FILTERS = ['ALL', 'STRONG LONG', 'LONG', 'NEUTRAL', 'SHORT', 'STRONG SHORT', 'OPEN POSITION', 'WATCHLIST'] as const;
const SORTS = [
  { key: 'long', label: 'LONG score' },
  { key: 'short', label: 'SHORT score' },
  { key: 'gain', label: '24h gain' },
  { key: 'loss', label: '24h loss' },
  { key: 'volume', label: 'Volume' },
  { key: 'newest', label: 'Newest signal' },
  { key: 'elliott', label: 'Elliott confidence' },
  { key: 'wave3', label: 'Wave 3 setups' },
  { key: 'alpha', label: 'A–Z' },
] as const;

function elliottTone(direction: string | null | undefined): string {
  if (direction === 'BULLISH') return 'text-accent';
  if (direction === 'BEARISH') return 'text-destructive';
  return 'text-muted-foreground';
}

function ElliottCell({ a }: { a: ScannerAsset }) {
  const e = a.elliott;
  if (!e || e.structure === 'UNCERTAIN') return <span className="text-[10px] text-muted-foreground">—</span>;
  const label = e.structure === 'IMPULSE' ? `W${e.wave ?? '?'}` : `ABC-${e.wave ?? '?'}`;
  return (
    <span className="inline-flex flex-col items-center">
      <span className={`font-mono-data text-[11px] font-bold ${elliottTone(e.direction)}`}>{label}</span>
      <span className="font-mono-data text-[9px] text-muted-foreground">{e.confidence}%</span>
      {e.wave3Candidate && <span className="text-[8px] font-bold uppercase text-primary">W3 setup</span>}
      {e.wave5Exhaustion && <span className="text-[8px] font-bold uppercase text-amber-500">W5 exhaust</span>}
    </span>
  );
}

const fmt = (v: number | null | undefined, d = 2) =>
  v == null ? '—' : v.toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d });
const usd = (v: number | null | undefined) => {
  if (v == null) return '—';
  const d = v >= 100 ? 2 : v >= 1 ? 4 : 8;
  return `$${v.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: d })}`;
};
const compactUsd = (v: number | null | undefined) => {
  if (v == null) return '—';
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
};
const hhmm = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '—';

function signalTone(sig: string | null | undefined): string {
  switch (sig) {
    case 'STRONG LONG': return 'bg-accent text-accent-foreground';
    case 'LONG': return 'bg-accent/20 text-accent';
    case 'STRONG SHORT': return 'bg-destructive text-destructive-foreground';
    case 'SHORT': return 'bg-destructive/20 text-destructive';
    default: return 'bg-muted text-muted-foreground';
  }
}

function heatTile(sig: string | null | undefined): string {
  switch (sig) {
    case 'STRONG LONG': return 'bg-accent/80 text-accent-foreground border-accent';
    case 'LONG': return 'bg-accent/30 text-foreground border-accent/40';
    case 'STRONG SHORT': return 'bg-destructive/80 text-destructive-foreground border-destructive';
    case 'SHORT': return 'bg-destructive/30 text-foreground border-destructive/40';
    default: return 'bg-muted/60 text-muted-foreground border-border';
  }
}

function DirBadge({ trend }: { trend: string | null | undefined }) {
  if (trend === 'BULLISH') return <TrendingUp size={13} className="text-accent" />;
  if (trend === 'BEARISH') return <TrendingDown size={13} className="text-destructive" />;
  return <span className="text-muted-foreground">—</span>;
}

function OppCard({ a, direction }: { a: ScannerAsset; direction: 'LONG' | 'SHORT' }) {
  const score = direction === 'LONG' ? a.longScore : a.shortScore;
  const conds = (direction === 'LONG' ? a.longConditions : a.shortConditions) ?? [];
  const passed = conds.filter((c) => c.pass).slice(0, 3);
  return (
    <Link href={`/markets/${a.ticker}`} className="block min-w-[150px] flex-1 rounded-xl border border-border/70 bg-background p-3 transition-colors hover:border-primary/40" data-testid={`opp-${direction}-${a.ticker}`}>
      <div className="flex items-center gap-2">
        <span className="font-mono-data text-xs font-bold">{a.ticker}</span>
        <span className={`ml-auto rounded px-1.5 py-0.5 font-mono-data text-[10px] font-bold ${direction === 'LONG' ? 'bg-accent/15 text-accent' : 'bg-destructive/15 text-destructive'}`}>
          {direction} {score}/6
        </span>
      </div>
      <div className="mt-1.5 space-y-0.5">
        {passed.map((c) => (
          <p key={c.name} className="truncate text-[10px] text-muted-foreground">✓ {c.name}</p>
        ))}
      </div>
    </Link>
  );
}

function CryptoTab() {
  const qc = useQueryClient();
  const dirQuery = useGetMarketDirectory({
    query: { queryKey: getGetMarketDirectoryQueryKey(), refetchInterval: 30000 },
  });
  const toggleWatch = useToggleWatchlist({
    mutation: {
      onSuccess: () => qc.invalidateQueries({ queryKey: getGetMarketDirectoryQueryKey() }),
    },
  });
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState<(typeof SIGNAL_FILTERS)[number]>('ALL');
  const [sort, setSort] = useState<(typeof SORTS)[number]['key']>('volume');
  const [view, setView] = useState<'list' | 'heatmap'>('list');

  const data = dirQuery.data;
  const assets = useMemo(() => {
    let list = (data?.assets ?? []).slice();
    const q = search.trim().toLowerCase();
    if (q) list = list.filter((a) => a.ticker.toLowerCase().includes(q) || a.name.toLowerCase().includes(q));
    if (filter === 'OPEN POSITION') list = list.filter((a) => a.hasPosition);
    else if (filter === 'WATCHLIST') list = list.filter((a) => a.watchlisted);
    else if (filter !== 'ALL') list = list.filter((a) => a.signal === filter);
    const vol = (a: ScannerAsset) => a.volumeUsd ?? 0;
    switch (sort) {
      case 'long': list.sort((a, b) => (b.longScore ?? -1) - (a.longScore ?? -1) || vol(b) - vol(a)); break;
      case 'short': list.sort((a, b) => (b.shortScore ?? -1) - (a.shortScore ?? -1) || vol(b) - vol(a)); break;
      case 'gain': list.sort((a, b) => (b.change24h ?? -Infinity) - (a.change24h ?? -Infinity)); break;
      case 'loss': list.sort((a, b) => (a.change24h ?? Infinity) - (b.change24h ?? Infinity)); break;
      case 'newest': list.sort((a, b) => (b.lastSignalChange?.at ?? '').localeCompare(a.lastSignalChange?.at ?? '')); break;
      case 'elliott': list.sort((a, b) => ((b.elliott?.structure !== 'UNCERTAIN' ? b.elliott?.confidence ?? 0 : 0) - (a.elliott?.structure !== 'UNCERTAIN' ? a.elliott?.confidence ?? 0 : 0))); break;
      case 'wave3': list.sort((a, b) => (Number(b.elliott?.wave3Candidate ?? false) - Number(a.elliott?.wave3Candidate ?? false)) || ((b.elliott?.confidence ?? 0) - (a.elliott?.confidence ?? 0))); break;
      case 'alpha': list.sort((a, b) => a.ticker.localeCompare(b.ticker)); break;
      default: list.sort((a, b) => vol(b) - vol(a));
    }
    return list;
  }, [data, search, filter, sort]);

  const bestLong = useMemo(() =>
    (data?.assets ?? []).filter((a) => (a.longScore ?? 0) >= 4 && (a.longScore ?? 0) > (a.shortScore ?? 0))
      .sort((a, b) => (b.longScore ?? 0) - (a.longScore ?? 0)).slice(0, 5), [data]);
  const bestShort = useMemo(() =>
    (data?.assets ?? []).filter((a) => (a.shortScore ?? 0) >= 4 && (a.shortScore ?? 0) > (a.longScore ?? 0))
      .sort((a, b) => (b.shortScore ?? 0) - (a.shortScore ?? 0)).slice(0, 5), [data]);
  const movers = useMemo(() =>
    (data?.assets ?? []).filter((a) => a.lastSignalChange?.at)
      .sort((a, b) => (b.lastSignalChange?.at ?? '').localeCompare(a.lastSignalChange?.at ?? '')).slice(0, 6), [data]);

  if (dirQuery.isLoading) return <p className="py-10 text-center text-sm text-muted-foreground">Scanning the market…</p>;
  if (!data) return <p className="py-10 text-center text-sm text-destructive">Market scanner unavailable — check the API server.</p>;

  const stats = data.marketStats;
  const acct = data.scannerAccount;
  const acctPnl = acct.balance - acct.startingBalance;

  return (
    <div className="space-y-5">
      {/* ── Market overview ─────────────────────────────────────────── */}
      <section className="rounded-2xl border border-border/80 bg-card p-4 sm:p-5" data-testid="market-overview">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
          {[
            ['Scanned', `${stats.scanned}/${stats.universe}`, ''],
            ['Strong Long', stats.counts['STRONG LONG'] ?? 0, 'text-accent'],
            ['Long', stats.counts['LONG'] ?? 0, 'text-accent'],
            ['Neutral', stats.counts['NEUTRAL'] ?? 0, ''],
            ['Short', stats.counts['SHORT'] ?? 0, 'text-destructive'],
            ['Strong Short', stats.counts['STRONG SHORT'] ?? 0, 'text-destructive'],
            ['Open trades', stats.openCryptoTrades, 'text-primary'],
            ['Next scan', hhmm(stats.nextScanAt), ''],
          ].map(([label, value, tone]) => (
            <div key={String(label)} className="rounded-lg bg-background px-3 py-2">
              <p className="text-[9px] font-bold uppercase tracking-[0.13em] text-muted-foreground">{label}</p>
              <p className={`font-mono-data text-sm font-semibold ${tone}`}>{value}</p>
            </div>
          ))}
        </div>
        <p className="mt-3 text-[10px] text-muted-foreground">
          SCANNER paper account: <span className="font-mono-data font-semibold">${fmt(acct.balance)}</span>
          <span className={`ml-1 font-mono-data ${acctPnl >= 0 ? 'text-accent' : 'text-destructive'}`}>({acctPnl >= 0 ? '+' : ''}${fmt(acctPnl)})</span>
          {' '}· {acct.openPositions}/{acct.maxPositions} positions · {acct.riskPerTradePct}% risk per trade ·
          signals are algorithmic classifications for paper trading, not financial advice.
        </p>
      </section>

      {/* ── Best opportunities ──────────────────────────────────────── */}
      {(bestLong.length > 0 || bestShort.length > 0) && (
        <section className="space-y-3" data-testid="best-opportunities">
          {bestLong.length > 0 && (
            <div>
              <h2 className="mb-2 flex items-center gap-1.5 text-[11px] font-extrabold uppercase tracking-[0.13em] text-accent"><ArrowUpRight size={13} /> Best long opportunities</h2>
              <div className="flex gap-2 overflow-x-auto pb-1">{bestLong.map((a) => <OppCard key={a.ticker} a={a} direction="LONG" />)}</div>
            </div>
          )}
          {bestShort.length > 0 && (
            <div>
              <h2 className="mb-2 flex items-center gap-1.5 text-[11px] font-extrabold uppercase tracking-[0.13em] text-destructive"><ArrowDownRight size={13} /> Best short opportunities</h2>
              <div className="flex gap-2 overflow-x-auto pb-1">{bestShort.map((a) => <OppCard key={a.ticker} a={a} direction="SHORT" />)}</div>
            </div>
          )}
        </section>
      )}

      {/* ── Fastest changing signals ────────────────────────────────── */}
      {movers.length > 0 && (
        <section className="rounded-2xl border border-border/80 bg-card p-4" data-testid="fastest-changing">
          <h2 className="mb-2 flex items-center gap-1.5 text-[11px] font-extrabold uppercase tracking-[0.13em]"><Zap size={13} className="text-primary" /> Fastest changing signals</h2>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {movers.map((a) => {
              const ch = a.lastSignalChange!;
              const rows: string[] = [];
              if (ch.longFrom != null && ch.longTo != null && ch.longFrom !== ch.longTo) rows.push(`LONG ${ch.longFrom} → ${ch.longTo}`);
              if (ch.shortFrom != null && ch.shortTo != null && ch.shortFrom !== ch.shortTo) rows.push(`SHORT ${ch.shortFrom} → ${ch.shortTo}`);
              return (
                <Link key={a.ticker} href={`/markets/${a.ticker}`} className="min-w-[110px] rounded-lg border border-border/70 bg-background p-2.5 transition-colors hover:border-primary/40">
                  <p className="font-mono-data text-xs font-bold">{a.ticker}</p>
                  {rows.map((r) => <p key={r} className="mt-0.5 font-mono-data text-[10px] text-muted-foreground">{r}</p>)}
                  <p className="mt-0.5 text-[9px] text-muted-foreground/70">{hhmm(ch.at)}</p>
                </Link>
              );
            })}
          </div>
        </section>
      )}

      {/* ── Search / filters / sort / view toggle ───────────────────── */}
      <section className="space-y-2.5">
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search cryptocurrency…"
              data-testid="input-search-crypto"
              className="w-full rounded-xl border border-border/80 bg-card py-2.5 pl-9 pr-3 text-sm outline-none placeholder:text-muted-foreground focus:border-primary/50"
            />
          </div>
          <button
            onClick={() => setView(view === 'list' ? 'heatmap' : 'list')}
            data-testid="button-toggle-heatmap"
            className={`flex items-center gap-1.5 rounded-xl border px-3 py-2.5 text-[11px] font-bold uppercase ${view === 'heatmap' ? 'border-primary/40 bg-primary/10 text-primary' : 'border-border/80 bg-card text-muted-foreground'}`}
          >
            {view === 'list' ? <Grid3X3 size={14} /> : <List size={14} />}
            {view === 'list' ? 'Heatmap' : 'List'}
          </button>
        </div>
        <div className="flex gap-1.5 overflow-x-auto pb-1">
          {SIGNAL_FILTERS.map((f) => (
            <button key={f} onClick={() => setFilter(f)} data-testid={`filter-${f.replace(' ', '-')}`}
              className={`whitespace-nowrap rounded-full px-3 py-1.5 font-mono-data text-[10px] font-bold uppercase tracking-wide transition-colors ${filter === f ? 'bg-primary text-primary-foreground' : 'border border-border/80 bg-card text-muted-foreground'}`}>
              {f === 'WATCHLIST' ? '★ Watchlist' : f}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5 overflow-x-auto pb-1">
          <span className="whitespace-nowrap text-[9px] font-bold uppercase tracking-[0.13em] text-muted-foreground">Sort:</span>
          {SORTS.map((s) => (
            <button key={s.key} onClick={() => setSort(s.key)} data-testid={`sort-${s.key}`}
              className={`whitespace-nowrap rounded-full px-2.5 py-1 font-mono-data text-[10px] font-semibold ${sort === s.key ? 'bg-muted text-foreground' : 'text-muted-foreground'}`}>
              {s.label}
            </button>
          ))}
        </div>
      </section>

      {/* ── Directory ───────────────────────────────────────────────── */}
      {view === 'heatmap' ? (
        <section className="grid grid-cols-3 gap-1.5 sm:grid-cols-5 lg:grid-cols-6" data-testid="crypto-heatmap">
          {assets.map((a) => (
            <Link key={a.ticker} href={`/markets/${a.ticker}`}
              className={`rounded-lg border p-2.5 text-center transition-transform active:scale-95 ${heatTile(a.signal)}`}
              data-testid={`heat-${a.ticker}`}>
              <p className="font-mono-data text-xs font-extrabold">{a.ticker}</p>
              <p className="font-mono-data text-[11px] font-semibold">{a.change24h == null ? '—' : `${a.change24h >= 0 ? '+' : ''}${a.change24h}%`}</p>
              <p className="mt-0.5 text-[8px] font-bold uppercase tracking-wide opacity-80">{a.signal ?? '—'}</p>
            </Link>
          ))}
        </section>
      ) : (
        <section className="overflow-hidden rounded-2xl border border-border/80 bg-card" data-testid="crypto-directory">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left">
              <thead>
                <tr className="border-b border-border/70 text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
                  <th className="px-3 py-2.5 font-bold">#</th>
                  <th className="px-2 py-2.5 font-bold">Asset</th>
                  <th className="px-2 py-2.5 text-right font-bold">Price</th>
                  <th className="px-2 py-2.5 text-right font-bold">24h</th>
                  <th className="px-2 py-2.5 text-right font-bold">24h Hi / Lo</th>
                  <th className="px-2 py-2.5 text-right font-bold">Volume</th>
                  <th className="px-2 py-2.5 text-center font-bold">15m</th>
                  <th className="px-2 py-2.5 text-center font-bold">1h</th>
                  <th className="px-2 py-2.5 text-center font-bold">4h</th>
                  <th className="px-2 py-2.5 text-center font-bold">L / S</th>
                  <th className="px-2 py-2.5 text-center font-bold">Elliott</th>
                  <th className="px-2 py-2.5 text-center font-bold">Signal</th>
                  <th className="px-3 py-2.5 text-center font-bold">★</th>
                </tr>
              </thead>
              <tbody>
                {assets.map((a) => (
                  <tr key={a.ticker} className="border-b border-border/40 last:border-0 hover:bg-muted/40" data-testid={`row-${a.ticker}`}>
                    <td className="px-3 py-2.5 font-mono-data text-[10px] text-muted-foreground">{a.rank ?? '—'}</td>
                    <td className="px-2 py-2.5">
                      <Link href={`/markets/${a.ticker}`} className="flex items-center gap-2">
                        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-muted font-mono-data text-[8px] font-extrabold">{a.ticker.slice(0, 3)}</span>
                        <span>
                          <span className="block font-mono-data text-xs font-bold">{a.ticker}</span>
                          <span className="block text-[9px] text-muted-foreground">{a.name}</span>
                        </span>
                        {a.hasPosition && <span className="rounded bg-primary/15 px-1 py-0.5 text-[8px] font-bold uppercase text-primary">open</span>}
                      </Link>
                      {a.tradingEnabled === false && a.disabledReason?.startsWith('TRADE DISABLED') && (
                        <p className="mt-0.5 text-[8px] font-bold uppercase text-amber-500">Trade disabled — low liquidity</p>
                      )}
                    </td>
                    <td className="px-2 py-2.5 text-right font-mono-data text-xs">{usd(a.price)}</td>
                    <td className={`px-2 py-2.5 text-right font-mono-data text-xs font-semibold ${(a.change24h ?? 0) >= 0 ? 'text-accent' : 'text-destructive'}`}>
                      {a.change24h == null ? '—' : `${a.change24h >= 0 ? '+' : ''}${a.change24h}%`}
                    </td>
                    <td className="px-2 py-2.5 text-right font-mono-data text-[10px] text-muted-foreground">
                      {usd(a.high24)}<br />{usd(a.low24)}
                    </td>
                    <td className="px-2 py-2.5 text-right font-mono-data text-xs">{compactUsd(a.volumeUsd)}</td>
                    <td className="px-2 py-2.5 text-center"><DirBadge trend={a.trend15m} /></td>
                    <td className="px-2 py-2.5 text-center"><DirBadge trend={a.trend1h} /></td>
                    <td className="px-2 py-2.5 text-center"><DirBadge trend={a.trend4h} /></td>
                    <td className="px-2 py-2.5 text-center font-mono-data text-[11px]">
                      <span className="text-accent">{a.longScore ?? '—'}</span>
                      <span className="text-muted-foreground"> / </span>
                      <span className="text-destructive">{a.shortScore ?? '—'}</span>
                    </td>
                    <td className="px-2 py-2.5 text-center"><ElliottCell a={a} /></td>
                    <td className="px-2 py-2.5 text-center">
                      <span className={`inline-block whitespace-nowrap rounded px-1.5 py-0.5 font-mono-data text-[9px] font-bold ${signalTone(a.signal)}`}>{a.signal ?? '—'}</span>
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <button
                        onClick={() => toggleWatch.mutate({ data: { ticker: a.ticker } })}
                        data-testid={`watch-${a.ticker}`}
                        aria-label={a.watchlisted ? `Remove ${a.ticker} from watchlist` : `Add ${a.ticker} to watchlist`}
                      >
                        <Star size={14} className={a.watchlisted ? 'fill-amber-400 text-amber-400' : 'text-muted-foreground'} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {assets.length === 0 && <p className="px-4 py-8 text-center text-sm text-muted-foreground">No assets match this filter.</p>}
        </section>
      )}
    </div>
  );
}

function LabCandidateCard({ c }: { c: ElliottLabCandidate }) {
  return (
    <Link href={`/markets/${c.ticker}`} className="min-w-[140px] flex-1 rounded-xl border border-border/70 bg-background p-3 transition-colors hover:border-primary/40" data-testid={`lab-${c.ticker}`}>
      <div className="flex items-center gap-2">
        <span className="font-mono-data text-xs font-bold">{c.ticker}</span>
        <span className={`ml-auto font-mono-data text-[10px] font-bold ${elliottTone(c.direction)}`}>
          {c.structure === 'IMPULSE' ? `W${c.wave ?? '?'}` : c.structure === 'ABC CORRECTION' ? `ABC-${c.wave ?? '?'}` : '—'}
        </span>
      </div>
      <p className="mt-1 font-mono-data text-[10px] text-muted-foreground">
        {c.confidence ?? '—'}% {c.confidenceLabel ? `(${c.confidenceLabel})` : ''}
      </p>
      <p className="mt-0.5 text-[9px] text-muted-foreground">ACTIVE: {c.signal ?? '—'} · L{c.longScore ?? '—'}/S{c.shortScore ?? '—'}</p>
    </Link>
  );
}

function StatRow({ label, s }: { label: string; s: { trades: number; winRate?: number | null; netPnl: number; profitFactor?: number | null; expectancy?: number | null; maxDrawdown?: number | null } | undefined }) {
  if (!s) return null;
  return (
    <tr className="border-b border-border/40 last:border-0">
      <td className="px-3 py-2 text-[10px] font-bold uppercase tracking-wide">{label}</td>
      <td className="px-2 py-2 text-right font-mono-data text-[11px]">{s.trades}</td>
      <td className="px-2 py-2 text-right font-mono-data text-[11px]">{s.winRate == null ? '—' : `${s.winRate}%`}</td>
      <td className={`px-2 py-2 text-right font-mono-data text-[11px] font-semibold ${s.netPnl >= 0 ? 'text-accent' : 'text-destructive'}`}>{s.netPnl >= 0 ? '+' : ''}${fmt(s.netPnl)}</td>
      <td className="px-2 py-2 text-right font-mono-data text-[11px]">{s.profitFactor ?? '—'}</td>
      <td className="px-2 py-2 text-right font-mono-data text-[11px]">{s.expectancy == null ? '—' : `$${fmt(s.expectancy)}`}</td>
      <td className="px-3 py-2 text-right font-mono-data text-[11px]">{s.maxDrawdown == null ? '—' : `$${fmt(s.maxDrawdown)}`}</td>
    </tr>
  );
}

function ElliottLabTab() {
  const labQuery = useGetElliottLab({
    query: { queryKey: getGetElliottLabQueryKey(), refetchInterval: 60000 },
  });
  const lab = labQuery.data;
  if (labQuery.isLoading) return <p className="py-10 text-center text-sm text-muted-foreground">Reading wave structures…</p>;
  if (!lab) return <p className="py-10 text-center text-sm text-destructive">Elliott lab unavailable — check the API server.</p>;
  const stats = lab.tradeStats as Record<string, { trades: number; winRate?: number | null; netPnl: number; profitFactor?: number | null; expectancy?: number | null; maxDrawdown?: number | null }>;
  const totalTracked = Object.values(stats).length ? (stats.aligned?.trades ?? 0) + (stats.notAligned?.trades ?? 0) + (stats.mixed?.trades ?? 0) : 0;
  return (
    <div className="space-y-5" data-testid="elliott-lab">
      <section className="rounded-2xl border border-border/80 bg-card p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-[11px] font-extrabold uppercase tracking-[0.13em]">Elliott Wave Lab</h2>
          <span className="rounded bg-amber-500/10 px-2 py-0.5 text-[9px] font-bold uppercase text-amber-500">Experimental — observation only</span>
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          <span className="rounded bg-muted px-2 py-1 font-mono-data text-[10px]">Score influence: <b>{lab.flags.elliottScoreInfluence}</b></span>
          <span className="rounded bg-muted px-2 py-1 font-mono-data text-[10px]">Wave 5 veto: <b>{lab.flags.wave5Veto}</b></span>
          <span className="rounded bg-muted px-2 py-1 font-mono-data text-[10px]">ACTIVE gate: <b>{lab.flags.activeGate}</b></span>
        </div>
        <p className="mt-2 text-[10px] text-muted-foreground">
          Elliott analysis never blocks or triggers trades. It is recorded on every ACTIVE entry so results can be compared later.
        </p>
      </section>

      {lab.wave3Candidates.length > 0 && (
        <section>
          <h2 className="mb-2 flex items-center gap-1.5 text-[11px] font-extrabold uppercase tracking-[0.13em] text-primary"><Zap size={13} /> Potential Wave 3 setups</h2>
          <div className="flex gap-2 overflow-x-auto pb-1">{lab.wave3Candidates.slice(0, 6).map((c) => <LabCandidateCard key={c.ticker} c={c} />)}</div>
        </section>
      )}
      {lab.wave5ExhaustionCandidates.length > 0 && (
        <section>
          <h2 className="mb-2 text-[11px] font-extrabold uppercase tracking-[0.13em] text-amber-500">⚠ Wave 5 exhaustion warnings</h2>
          <div className="flex gap-2 overflow-x-auto pb-1">{lab.wave5ExhaustionCandidates.slice(0, 6).map((c) => <LabCandidateCard key={c.ticker} c={c} />)}</div>
        </section>
      )}
      {lab.abcCandidates.length > 0 && (
        <section>
          <h2 className="mb-2 text-[11px] font-extrabold uppercase tracking-[0.13em]">ABC corrections in progress</h2>
          <div className="flex gap-2 overflow-x-auto pb-1">{lab.abcCandidates.slice(0, 6).map((c) => <LabCandidateCard key={c.ticker} c={c} />)}</div>
        </section>
      )}

      <section className="rounded-2xl border border-border/80 bg-card p-4">
        <h2 className="mb-2 text-[11px] font-extrabold uppercase tracking-[0.13em]">Multi-timeframe alignment</h2>
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded-lg bg-background p-3">
            <p className="text-[9px] font-bold uppercase tracking-[0.13em] text-accent">Strong bullish alignment</p>
            <p className="mt-1 font-mono-data text-xs">{lab.bullishAligned.length ? lab.bullishAligned.join(' · ') : 'None right now'}</p>
          </div>
          <div className="rounded-lg bg-background p-3">
            <p className="text-[9px] font-bold uppercase tracking-[0.13em] text-destructive">Strong bearish alignment</p>
            <p className="mt-1 font-mono-data text-xs">{lab.bearishAligned.length ? lab.bearishAligned.join(' · ') : 'None right now'}</p>
          </div>
        </div>
        {lab.uncertain.length > 0 && (
          <p className="mt-2 text-[10px] text-muted-foreground">Uncertain structure: {lab.uncertain.join(', ')}</p>
        )}
      </section>

      <section className="overflow-hidden rounded-2xl border border-border/80 bg-card">
        <div className="p-4 pb-2">
          <h2 className="text-[11px] font-extrabold uppercase tracking-[0.13em]">The experiment — Elliott-aligned vs not</h2>
          <p className="mt-1 text-[10px] text-muted-foreground">
            {totalTracked === 0
              ? 'No trades with Elliott context recorded yet — every new ACTIVE/SCANNER trade will appear here after it closes.'
              : `${totalTracked} closed trades with Elliott context so far.`}
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left">
            <thead>
              <tr className="border-b border-border/70 text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
                <th className="px-3 py-2 font-bold">Bucket</th>
                <th className="px-2 py-2 text-right font-bold">Trades</th>
                <th className="px-2 py-2 text-right font-bold">Win rate</th>
                <th className="px-2 py-2 text-right font-bold">Net P&L</th>
                <th className="px-2 py-2 text-right font-bold">PF</th>
                <th className="px-2 py-2 text-right font-bold">Expectancy</th>
                <th className="px-3 py-2 text-right font-bold">Max DD</th>
              </tr>
            </thead>
            <tbody>
              <StatRow label="Elliott aligned" s={stats.aligned} />
              <StatRow label="Not aligned" s={stats.notAligned} />
              <StatRow label="Mixed / uncertain at entry" s={stats.mixed} />
              <StatRow label="Wave 3 entries" s={stats.wave3} />
              <StatRow label="Wave 5 entries" s={stats.wave5} />
              <StatRow label="During ABC correction" s={stats.abc} />
              <StatRow label="Veto would have blocked" s={stats.vetoWouldHaveBlocked} />
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function MetalTab({ metal }: { metal: 'GOLD' | 'SILVER' }) {
  const stateQuery = useGetMultiCoinState({
    query: { queryKey: getGetMultiCoinStateQueryKey(), refetchInterval: 30000 },
  });
  const s = (stateQuery.data as unknown as Record<string, PaperTraderState> | undefined)?.[metal];
  return (
    <div className="space-y-4">
      {s && (
        <section className="rounded-2xl border border-border/80 bg-card p-4">
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-mono-data text-lg font-bold">{metal}</span>
            <span className="font-mono-data text-lg">${fmt(s.market.currentPrice)}</span>
            <span className="rounded bg-amber-500/10 px-2 py-1 text-[9px] font-bold uppercase text-amber-500">Unvalidated strategy — paper only</span>
          </div>
        </section>
      )}
      <section className="rounded-2xl border border-border/80 bg-card p-4">
        <AssetChart asset={metal} trades={[]} currentPrice={s?.market.currentPrice} />
      </section>
    </div>
  );
}

export default function MarketsPage() {
  const [tab, setTab] = useState<'CRYPTO' | 'ELLIOTT' | 'GOLD' | 'SILVER'>('CRYPTO');
  return (
    <TradingShell eyebrow="live desk" title="Markets" subtitle="Whole-market scanner — every asset ranked by the same ACTIVE strategy engine">
      <div className="mb-5 flex gap-1.5 overflow-x-auto pb-1">
        {(['CRYPTO', 'ELLIOTT', 'GOLD', 'SILVER'] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)} data-testid={`tab-${t}`}
            className={`flex items-center gap-1.5 whitespace-nowrap rounded-xl px-4 py-2 text-xs font-extrabold uppercase tracking-wide ${tab === t ? 'bg-primary text-primary-foreground' : 'border border-border/80 bg-card text-muted-foreground'}`}>
            {t === 'CRYPTO' && <Flame size={13} />}{t === 'ELLIOTT' ? 'Elliott Lab' : t}
          </button>
        ))}
      </div>
      {tab === 'CRYPTO' ? <CryptoTab /> : tab === 'ELLIOTT' ? <ElliottLabTab /> : <MetalTab metal={tab} />}
    </TradingShell>
  );
}
