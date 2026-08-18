import { useMemo, useState } from 'react';
import { FlaskConical, Lock, Trophy } from 'lucide-react';
import {
  useGetLabOverview,
  useGetLabStrategy,
  type LabStats,
  type LabLeaderboardEntry,
} from '@workspace/api-client-react';
import { TradingShell } from '@/components/trading-shell';

const fmt = (v: number | null | undefined, suffix = '', digits = 2) =>
  v === null || v === undefined ? '—' : `${v.toFixed(digits)}${suffix}`;

const confTone = (c: string) =>
  c === 'LARGE SAMPLE' || c === 'GOOD SAMPLE'
    ? 'text-accent'
    : c === 'MODERATE CONFIDENCE'
      ? 'text-primary'
      : 'text-muted-foreground';

function StatGrid({ s, compact }: { s: LabStats; compact?: boolean }) {
  const rows: Array<[string, string]> = [
    ['Trades', String(s.trades)],
    ['Win rate', fmt(s.winRate, '%', 1)],
    ['Profit factor', fmt(s.profitFactor)],
    ['Expectancy', fmt(s.expectancy, 'R', 3)],
    ['ROI @1% risk', fmt(s.roiPct, '%')],
    ['Max drawdown', fmt(s.maxDrawdownPct, '%')],
  ];
  const shown = compact ? rows.slice(0, 4) : rows;
  return (
    <div className="grid grid-cols-2 gap-x-3 gap-y-1">
      {shown.map(([k, v]) => (
        <div key={k} className="flex items-baseline justify-between gap-2">
          <span className="text-[9px] uppercase tracking-[0.1em] text-muted-foreground">{k}</span>
          <span className="font-mono-data text-[11px] font-semibold">{v}</span>
        </div>
      ))}
      {s.insufficientData && (
        <p className="col-span-2 text-[9px] font-bold uppercase text-amber-500">Insufficient data — {s.confidence}</p>
      )}
    </div>
  );
}

function BucketTable({ rows }: { rows: Array<{ name: string; stats: LabStats }> }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] text-left">
        <thead>
          <tr className="border-b border-border/70 text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
            <th className="py-1.5 pr-2">Bucket</th>
            <th className="px-2 py-1.5 text-right">Trades</th>
            <th className="px-2 py-1.5 text-right">Win %</th>
            <th className="px-2 py-1.5 text-right">PF</th>
            <th className="px-2 py-1.5 text-right">Expect.</th>
            <th className="px-2 py-1.5 text-right">Max DD</th>
            <th className="px-2 py-1.5 text-right">Sample</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ name, stats: s }) => (
            <tr key={name} className="border-b border-border/40 font-mono-data text-[11px]">
              <td className="py-1.5 pr-2 font-sans text-xs font-semibold">{name}</td>
              <td className="px-2 py-1.5 text-right">{s.trades}</td>
              <td className="px-2 py-1.5 text-right">{fmt(s.winRate, '', 1)}</td>
              <td className="px-2 py-1.5 text-right">{fmt(s.profitFactor)}</td>
              <td className="px-2 py-1.5 text-right">{fmt(s.expectancy, '', 3)}</td>
              <td className="px-2 py-1.5 text-right">{fmt(s.maxDrawdownPct, '%')}</td>
              <td className={`px-2 py-1.5 text-right text-[9px] font-bold uppercase ${confTone(s.confidence)}`}>
                {s.trades === 0 ? '—' : s.insufficientData ? 'LOW' : s.confidence.split(' ')[0]}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type SortKey = 'expectancy' | 'profitFactor' | 'roiPct' | 'winRate' | 'maxDrawdownPct' | 'sharpe' | 'trades';

function EquityChart({ points }: { points: Array<{ balance: number; drawdownPct: number }> }) {
  if (points.length < 2) {
    return <p className="py-6 text-center text-xs text-muted-foreground">Not enough closed shadow trades yet — the curve appears once this strategy has results.</p>;
  }
  const w = 340;
  const h = 110;
  const dh = 40;
  const bs = points.map((p) => p.balance);
  const min = Math.min(...bs);
  const max = Math.max(...bs);
  const span = max - min || 1;
  const x = (i: number) => (i / (points.length - 1)) * w;
  const y = (b: number) => h - ((b - min) / span) * (h - 8) - 4;
  const maxDD = Math.max(...points.map((p) => p.drawdownPct), 0.01);
  const yd = (d: number) => (d / maxDD) * (dh - 4);
  return (
    <div>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full" preserveAspectRatio="none">
        <polyline
          fill="none"
          stroke="hsl(var(--primary))"
          strokeWidth="1.6"
          points={points.map((p, i) => `${x(i)},${y(p.balance)}`).join(' ')}
        />
      </svg>
      <p className="mt-1 text-[9px] uppercase tracking-[0.1em] text-muted-foreground">Drawdown</p>
      <svg viewBox={`0 0 ${w} ${dh}`} className="w-full" preserveAspectRatio="none">
        <polyline
          fill="none"
          stroke="hsl(var(--destructive))"
          strokeWidth="1.2"
          points={points.map((p, i) => `${x(i)},${yd(p.drawdownPct)}`).join(' ')}
        />
      </svg>
    </div>
  );
}

export default function StrategyLabPage() {
  const overviewQ = useGetLabOverview({
    query: { queryKey: ['/api/market/lab-overview'], refetchInterval: 60000 },
  });
  const data = overviewQ.data;

  const [sortKey, setSortKey] = useState<SortKey>('expectancy');
  const [selected, setSelected] = useState<string>('4/6 + DYNAMIC');
  const [compareA, setCompareA] = useState<string>('4/6 + DYNAMIC');
  const [compareB, setCompareB] = useState<string>('5/6 + BALANCED');
  const [startBal, setStartBal] = useState<100 | 1000>(1000);
  const [risk, setRisk] = useState<number>(1.0);

  const detailQ = useGetLabStrategy(
    { strategy: selected, start: startBal, risk },
    { query: { queryKey: ['/api/market/lab-strategy', selected, startBal, risk] } },
  );

  const leaderboard = useMemo(() => {
    const rows = [...(data?.leaderboard ?? [])];
    rows.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      return sortKey === 'maxDrawdownPct' ? (av as number) - (bv as number) : (bv as number) - (av as number);
    });
    return rows;
  }, [data, sortKey]);

  const strategies = useMemo(() => (data?.leaderboard ?? []).map((l) => l.strategy), [data]);
  const byId = useMemo(() => {
    const m = new Map<string, LabLeaderboardEntry>();
    (data?.leaderboard ?? []).forEach((l) => m.set(l.strategy, l));
    return m;
  }, [data]);

  const s = data?.summary;
  const cmpRows: Array<[string, (e: LabLeaderboardEntry) => string]> = [
    ['Trades', (e) => String(e.trades)],
    ['Win rate', (e) => fmt(e.winRate, '%', 1)],
    ['Profit factor', (e) => fmt(e.profitFactor)],
    ['Expectancy', (e) => fmt(e.expectancy, 'R', 3)],
    ['Net R', (e) => fmt(e.netR, 'R')],
    ['ROI @1% risk', (e) => fmt(e.roiPct, '%')],
    ['Max drawdown', (e) => fmt(e.maxDrawdownPct, '%')],
    ['Sharpe', (e) => fmt(e.sharpe)],
    ['Avg win / loss', (e) => `${fmt(e.avgWin, 'R')} / ${fmt(e.avgLoss, 'R')}`],
    ['Longest loss streak', (e) => String(e.longestLossStreak ?? '—')],
    ['Sample', (e) => e.confidence],
  ];

  return (
    <TradingShell
      eyebrow="strategy lab"
      title="Strategy Lab"
      subtitle="Shadow strategies simulate what would have happened — nothing here touches real paper positions"
    >
      <div className="space-y-5" data-testid="page-strategy-lab">
        {/* Guardrail banner */}
        <div className="flex items-start gap-2 rounded-xl border border-primary/25 bg-primary/[0.06] px-4 py-3">
          <Lock size={14} className="mt-0.5 shrink-0 text-primary" />
          <p className="text-[11px] leading-relaxed text-foreground/80">
            <span className="font-bold uppercase">Simulation only.</span> Main ACTIVE strategy stays at 4/6 — the lab
            observes and can never change it automatically. All results are <span className="font-bold">net of estimated
            fees ({s ? s.feePctPerSide : '0.26'}%/side) and spread</span>. Sample-size labels describe data volume, not
            future profitability.
          </p>
        </div>

        {/* Summary */}
        <section className="grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="lab-summary">
          {[
            ['Experiments', s ? String(s.experimentsRunning) : '—'],
            ['Shadow trades', s ? `${s.totalShadowTrades} closed · ${s.openShadowTrades} open` : '—'],
            ['Signals recorded', s ? String(s.totalSignals) : '—'],
            ['Data confidence', s?.dataConfidence ?? '—'],
            ['Best expectancy', s?.bestExpectancy ? `${s.bestExpectancy.strategy} (${fmt(s.bestExpectancy.value, 'R', 3)})` : 'Insufficient data'],
            ['Best profit factor', s?.bestProfitFactor ? `${s.bestProfitFactor.strategy} (${fmt(s.bestProfitFactor.value)})` : 'Insufficient data'],
            ['Best risk-adjusted', s?.bestRiskAdjusted ? `${s.bestRiskAdjusted.strategy} (Sharpe ${fmt(s.bestRiskAdjusted.value)})` : 'Insufficient data'],
            ['Lowest drawdown', s?.lowestDrawdown ? `${s.lowestDrawdown.strategy} (${fmt(s.lowestDrawdown.value, '%')})` : 'Insufficient data'],
          ].map(([k, v]) => (
            <div key={k} className="rounded-xl border border-border/80 bg-card px-3 py-2.5">
              <p className="text-[9px] font-bold uppercase tracking-[0.12em] text-muted-foreground">{k}</p>
              <p className="mt-0.5 font-mono-data text-[11px] font-semibold leading-snug">{v}</p>
            </div>
          ))}
        </section>

        {/* Leaderboard */}
        <section className="rounded-2xl border border-border/80 bg-card p-4" data-testid="lab-leaderboard">
          <div className="flex flex-wrap items-center gap-2">
            <Trophy size={14} className="text-primary" />
            <h2 className="text-xs font-extrabold uppercase tracking-[0.13em]">Strategy leaderboard</h2>
            <select
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as SortKey)}
              className="ml-auto rounded-lg border border-border bg-background px-2 py-1 text-[11px]"
              data-testid="select-lab-sort"
            >
              <option value="expectancy">Sort: Expectancy</option>
              <option value="profitFactor">Sort: Profit factor</option>
              <option value="roiPct">Sort: ROI</option>
              <option value="sharpe">Sort: Sharpe</option>
              <option value="winRate">Sort: Win rate</option>
              <option value="maxDrawdownPct">Sort: Lowest drawdown</option>
              <option value="trades">Sort: Trades</option>
            </select>
          </div>
          <p className="mt-1 text-[10px] text-muted-foreground">Rankings need a real sample — win rate alone never decides.</p>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[760px] text-left">
              <thead>
                <tr className="border-b border-border/70 text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
                  <th className="py-1.5 pr-2">#</th>
                  <th className="py-1.5 pr-2">Strategy</th>
                  <th className="px-2 py-1.5 text-right">Trades</th>
                  <th className="px-2 py-1.5 text-right">Win %</th>
                  <th className="px-2 py-1.5 text-right">Net R</th>
                  <th className="px-2 py-1.5 text-right">ROI %</th>
                  <th className="px-2 py-1.5 text-right">PF</th>
                  <th className="px-2 py-1.5 text-right">Expect.</th>
                  <th className="px-2 py-1.5 text-right">Max DD</th>
                  <th className="px-2 py-1.5 text-right">Sharpe</th>
                  <th className="px-2 py-1.5 text-right">Avg W/L</th>
                  <th className="px-2 py-1.5 text-right">Sample</th>
                </tr>
              </thead>
              <tbody>
                {leaderboard.map((l, i) => (
                  <tr key={l.strategy} className="border-b border-border/40 font-mono-data text-[11px]">
                    <td className="py-1.5 pr-2">{i + 1}</td>
                    <td className="py-1.5 pr-2 font-sans text-xs font-semibold">
                      {l.strategy}
                      {l.promotionCandidate && (
                        <span className="ml-1.5 rounded bg-accent/15 px-1 py-0.5 text-[8px] font-bold uppercase text-accent">Candidate for promotion</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-right">{l.trades}</td>
                    <td className="px-2 py-1.5 text-right">{fmt(l.winRate, '', 1)}</td>
                    <td className="px-2 py-1.5 text-right">{fmt(l.netR)}</td>
                    <td className="px-2 py-1.5 text-right">{fmt(l.roiPct)}</td>
                    <td className="px-2 py-1.5 text-right">{fmt(l.profitFactor)}</td>
                    <td className="px-2 py-1.5 text-right">{fmt(l.expectancy, '', 3)}</td>
                    <td className="px-2 py-1.5 text-right">{fmt(l.maxDrawdownPct, '%')}</td>
                    <td className="px-2 py-1.5 text-right">{fmt(l.sharpe)}</td>
                    <td className="px-2 py-1.5 text-right">{fmt(l.avgWin, '', 1)}/{fmt(l.avgLoss, '', 1)}</td>
                    <td className={`px-2 py-1.5 text-right text-[9px] font-bold uppercase ${confTone(l.confidence)}`}>
                      {l.trades === 0 ? 'NO DATA' : l.insufficientData ? 'INSUFFICIENT DATA' : l.confidence}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* Equity curve */}
        <section className="rounded-2xl border border-border/80 bg-card p-4" data-testid="lab-equity">
          <h2 className="text-xs font-extrabold uppercase tracking-[0.13em]">Equity curve (hypothetical compounding)</h2>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <select value={selected} onChange={(e) => setSelected(e.target.value)} className="rounded-lg border border-border bg-background px-2 py-1 text-[11px]" data-testid="select-lab-strategy">
              {strategies.map((st) => <option key={st} value={st}>{st}</option>)}
            </select>
            <div className="flex overflow-hidden rounded-lg border border-border text-[10px] font-bold">
              {[100, 1000].map((b) => (
                <button key={b} onClick={() => setStartBal(b as 100 | 1000)} className={`px-2.5 py-1 ${startBal === b ? 'bg-primary text-primary-foreground' : 'bg-background text-muted-foreground'}`} data-testid={`button-start-${b}`}>
                  £{b.toLocaleString()}
                </button>
              ))}
            </div>
            <select value={risk} onChange={(e) => setRisk(Number(e.target.value))} className="rounded-lg border border-border bg-background px-2 py-1 text-[11px]" data-testid="select-lab-risk">
              {[0.5, 1.0, 1.5, 2.0, 2.5].map((r) => <option key={r} value={r}>{r}% risk</option>)}
            </select>
          </div>
          <div className="mt-3">
            {detailQ.data?.ok && detailQ.data.equity ? (
              <>
                <EquityChart points={detailQ.data.equity} />
                {detailQ.data.stats && (
                  <div className="mt-3 rounded-lg bg-background p-3"><StatGrid s={detailQ.data.stats} /></div>
                )}
                {detailQ.data.riskTable && detailQ.data.riskTable.length > 0 && (
                  <div className="mt-3">
                    <p className="mb-1 text-[9px] font-bold uppercase tracking-[0.12em] text-muted-foreground">Same trades at every risk level (one trade, five risk models)</p>
                    <BucketTable rows={detailQ.data.riskTable.map((r) => ({ name: `${r.riskPct}% risk`, stats: r }))} />
                  </div>
                )}
                {detailQ.data.drawdownProtection && detailQ.data.stats && (
                  <p className="mt-2 text-[10px] text-muted-foreground">
                    Drawdown-protection model on the same trades: ROI {fmt(detailQ.data.drawdownProtection.roiPct, '%')} with max DD {fmt(detailQ.data.drawdownProtection.maxDrawdownPct, '%')} vs constant-risk ROI {fmt(detailQ.data.stats.roiPct, '%')} / DD {fmt(detailQ.data.stats.maxDrawdownPct, '%')}.
                  </p>
                )}
              </>
            ) : (
              <p className="py-4 text-center text-xs text-muted-foreground">Loading strategy…</p>
            )}
          </div>
        </section>

        {/* Comparison */}
        <section className="rounded-2xl border border-border/80 bg-card p-4" data-testid="lab-compare">
          <h2 className="text-xs font-extrabold uppercase tracking-[0.13em]">Strategy comparison</h2>
          <div className="mt-2 grid grid-cols-2 gap-2">
            <select value={compareA} onChange={(e) => setCompareA(e.target.value)} className="rounded-lg border border-border bg-background px-2 py-1 text-[11px]" data-testid="select-compare-a">
              {strategies.map((st) => <option key={st} value={st}>{st}</option>)}
            </select>
            <select value={compareB} onChange={(e) => setCompareB(e.target.value)} className="rounded-lg border border-border bg-background px-2 py-1 text-[11px]" data-testid="select-compare-b">
              {strategies.map((st) => <option key={st} value={st}>{st}</option>)}
            </select>
          </div>
          {byId.get(compareA) && byId.get(compareB) && (
            <table className="mt-3 w-full text-left">
              <thead>
                <tr className="border-b border-border/70 text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
                  <th className="py-1.5 pr-2">Metric</th>
                  <th className="px-2 py-1.5 text-right">A</th>
                  <th className="px-2 py-1.5 text-right">B</th>
                </tr>
              </thead>
              <tbody>
                {cmpRows.map(([label, get]) => (
                  <tr key={label} className="border-b border-border/40 font-mono-data text-[11px]">
                    <td className="py-1.5 pr-2 font-sans text-[11px] text-muted-foreground">{label}</td>
                    <td className="px-2 py-1.5 text-right">{get(byId.get(compareA)!)}</td>
                    <td className="px-2 py-1.5 text-right">{get(byId.get(compareB)!)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="mt-2 text-[10px] text-muted-foreground">Differences are descriptive — no statistical certainty is claimed on small samples.</p>
        </section>

        {/* LONG vs SHORT */}
        {data && (
          <section className="rounded-2xl border border-border/80 bg-card p-4" data-testid="lab-long-short">
            <h2 className="text-xs font-extrabold uppercase tracking-[0.13em]">Long vs Short (4/6+ signals, all exits)</h2>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="rounded-lg bg-background p-3">
                <p className="mb-2 text-[10px] font-bold uppercase text-accent">LONG</p>
                <StatGrid s={data.longShort.LONG} />
              </div>
              <div className="rounded-lg bg-background p-3">
                <p className="mb-2 text-[10px] font-bold uppercase text-destructive">SHORT</p>
                <StatGrid s={data.longShort.SHORT} />
              </div>
            </div>
            {data.perAsset.length > 0 && (
              <div className="mt-4">
                <p className="mb-1 text-[9px] font-bold uppercase tracking-[0.12em] text-muted-foreground">By cryptocurrency</p>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[560px] text-left">
                    <thead>
                      <tr className="border-b border-border/70 text-[9px] uppercase tracking-[0.12em] text-muted-foreground">
                        <th className="py-1.5 pr-2">Asset</th>
                        <th className="px-2 py-1.5 text-right">L trades</th>
                        <th className="px-2 py-1.5 text-right">L win %</th>
                        <th className="px-2 py-1.5 text-right">L PF</th>
                        <th className="px-2 py-1.5 text-right">S trades</th>
                        <th className="px-2 py-1.5 text-right">S win %</th>
                        <th className="px-2 py-1.5 text-right">S PF</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.perAsset.map((a) => (
                        <tr key={a.ticker} className="border-b border-border/40 font-mono-data text-[11px]">
                          <td className="py-1.5 pr-2 font-sans text-xs font-semibold">{a.ticker}</td>
                          <td className="px-2 py-1.5 text-right">{a.long.trades}</td>
                          <td className="px-2 py-1.5 text-right">{fmt(a.long.winRate, '', 1)}</td>
                          <td className="px-2 py-1.5 text-right">{fmt(a.long.profitFactor)}</td>
                          <td className="px-2 py-1.5 text-right">{a.short.trades}</td>
                          <td className="px-2 py-1.5 text-right">{fmt(a.short.winRate, '', 1)}</td>
                          <td className="px-2 py-1.5 text-right">{fmt(a.short.profitFactor)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </section>
        )}

        {/* Elliott buckets */}
        {data && (
          <section className="rounded-2xl border border-border/80 bg-card p-4" data-testid="lab-elliott">
            <h2 className="text-xs font-extrabold uppercase tracking-[0.13em]">Elliott Wave experiment (recorded, never a filter)</h2>
            <div className="mt-3">
              <BucketTable
                rows={[
                  ['all', 'All 4/6+ trades'], ['aligned', 'Elliott aligned'], ['notAligned', 'Elliott not aligned'],
                  ['wave3', 'Potential Wave 3'], ['wave5Exhaustion', 'Wave 5 exhaustion'], ['abc', 'ABC correction'],
                  ['uncertain', 'Elliott uncertain'],
                ].map(([key, name]) => ({ name, stats: data.elliott[key] ?? { trades: 0, confidence: 'VERY LOW CONFIDENCE' } as LabStats }))}
              />
            </div>
          </section>
        )}

        {/* Regimes + combinations */}
        {data && (
          <section className="rounded-2xl border border-border/80 bg-card p-4" data-testid="lab-regimes">
            <h2 className="text-xs font-extrabold uppercase tracking-[0.13em]">Market regimes & winning combinations</h2>
            <div className="mt-3">
              <p className="mb-1 text-[9px] font-bold uppercase tracking-[0.12em] text-muted-foreground">By market regime (recorded on every shadow trade)</p>
              <BucketTable rows={Object.entries(data.regimes).map(([name, stats]) => ({ name, stats }))} />
            </div>
            <div className="mt-4">
              <p className="mb-1 text-[9px] font-bold uppercase tracking-[0.12em] text-muted-foreground">Condition combinations</p>
              <BucketTable rows={data.combinations.map((c) => ({ name: c.name, stats: c }))} />
            </div>
          </section>
        )}

        {/* Protections & missed opportunities */}
        {data && (
          <section className="rounded-2xl border border-border/80 bg-card p-4" data-testid="lab-protections">
            <div className="flex items-center gap-2">
              <FlaskConical size={14} className="text-primary" />
              <h2 className="text-xs font-extrabold uppercase tracking-[0.13em]">Protection experiments & missed opportunities</h2>
            </div>
            <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="rounded-lg bg-background p-3">
                <p className="mb-2 text-[10px] font-bold uppercase">Correlation protection (max 2 same-direction)</p>
                <p className="mb-1 text-[9px] uppercase text-muted-foreground">OFF (all trades)</p>
                <StatGrid s={data.correlationProtection.off} compact />
                <p className="mb-1 mt-2 text-[9px] uppercase text-muted-foreground">ON (capped)</p>
                <StatGrid s={data.correlationProtection.on} compact />
              </div>
              <div className="rounded-lg bg-background p-3">
                <p className="mb-2 text-[10px] font-bold uppercase">Missed / shadow opportunities</p>
                <p className="font-mono-data text-lg font-bold">{data.missedOpportunities.signalsBlocked}</p>
                <p className="text-[10px] text-muted-foreground">signals the portfolio couldn't take (position/risk limits)</p>
                <p className="mb-1 mt-2 text-[9px] uppercase text-muted-foreground">How they'd have performed (DYNAMIC exit)</p>
                <StatGrid s={data.missedOpportunities.performanceIfTaken} compact />
              </div>
            </div>
            {data.riskModels.length > 0 && (
              <div className="mt-4">
                <p className="mb-1 text-[9px] font-bold uppercase tracking-[0.12em] text-muted-foreground">Risk models on the current leader ({s?.bestStrategy})</p>
                <BucketTable rows={data.riskModels.map((r) => ({ name: `${r.riskPct}% risk`, stats: r }))} />
              </div>
            )}
          </section>
        )}
      </div>
    </TradingShell>
  );
}
