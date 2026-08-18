import { useState, useMemo } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Legend,
} from 'recharts';
import { TrendingUp, TrendingDown } from 'lucide-react';
import { useGetPnlSeries } from '@workspace/api-client-react';

// ─── Asset colours ────────────────────────────────────────────────────────────
const ASSET_COLORS: Record<string, string> = {
  CRYPTO:  'hsl(var(--primary))',
  BTC:     '#f59e0b',
  ETH:     '#a78bfa',
  SOL:     '#34d399',
  XRP:     '#60a5fa',
  GOLD:    '#fde047',
  SILVER:  '#94a3b8',
};

const ASSET_LABELS: Record<string, string> = {
  CRYPTO: 'All Crypto',
  BTC: 'Bitcoin',
  ETH: 'Ethereum',
  SOL: 'Solana',
  XRP: 'XRP',
  GOLD: 'Gold',
  SILVER: 'Silver',
};

// All selectable assets — CRYPTO is the combined crypto portfolio view
const CRYPTO_ASSETS = ['CRYPTO', 'BTC', 'ETH', 'SOL', 'XRP'] as const;
const METALS_ASSETS = ['GOLD', 'SILVER'] as const;

// ─── Formatters ───────────────────────────────────────────────────────────────
const fmtDate = (ts: string) => {
  try {
    const d = new Date(ts);
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
  } catch {
    return ts;
  }
};

const fmtDateTime = (ts: string) => {
  try {
    const d = new Date(ts);
    return d.toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  } catch {
    return ts;
  }
};

// ─── Custom tooltip ───────────────────────────────────────────────────────────
function PnlTooltip({ active, payload, label }: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color: string; payload: { balance?: number } }>;
  label?: string;
}) {
  if (!active || !payload?.length || !label) return null;
  return (
    <div className="rounded-xl border border-border/80 bg-card px-3 py-2.5 shadow-lg">
      <p className="mb-1.5 font-mono-data text-[10px] text-muted-foreground">{fmtDateTime(label)}</p>
      {payload.map((entry) => (
        <div key={entry.name} className="flex items-center gap-2 text-[11px]">
          <span className="h-1.5 w-1.5 rounded-full shrink-0" style={{ background: entry.color }} />
          <span className="text-muted-foreground">{ASSET_LABELS[entry.name] ?? entry.name}</span>
          <span
            className="ml-auto font-mono-data font-semibold"
            style={{ color: entry.value >= 0 ? 'hsl(var(--accent))' : 'hsl(var(--destructive))' }}
          >
            {entry.value >= 0 ? '+' : ''}{entry.value.toFixed(2)}%
          </span>
        </div>
      ))}
    </div>
  );
}

// ─── Filter button ────────────────────────────────────────────────────────────
function FilterBtn({
  label, active, color, onClick,
}: { label: string; active: boolean; color: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1 font-mono-data text-[10px] font-semibold uppercase tracking-wider transition-colors ${
        active
          ? 'border-transparent text-background'
          : 'border-border/60 text-muted-foreground hover:border-border hover:text-foreground'
      }`}
      style={active ? { background: color } : {}}
    >
      {label}
    </button>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────
export function PnlChart() {
  const [activeAssets, setActiveAssets] = useState<Set<string>>(new Set(['CRYPTO']));

  const { data, isLoading, isError } = useGetPnlSeries({
    query: { queryKey: ['/api/paper-trader/pnl-series'], staleTime: 60_000, refetchInterval: 120_000 },
  });

  // Toggle an asset filter on/off; ensure at least one remains selected
  const toggle = (asset: string) => {
    setActiveAssets((prev) => {
      const next = new Set(prev);
      if (next.has(asset)) {
        if (next.size === 1) return next; // keep at least one
        next.delete(asset);
      } else {
        next.add(asset);
      }
      return next;
    });
  };

  // Merge all selected series into a unified [{ts, BTC: pct, ETH: pct, …}] array
  const chartData = useMemo(() => {
    if (!data) return [];
    const series = data.series;

    // Collect all timestamps from selected assets
    const tsMap = new Map<string, Record<string, number>>();
    for (const asset of activeAssets) {
      const points = series[asset] ?? [];
      for (const pt of points) {
        const row = tsMap.get(pt.ts) ?? {};
        row[asset] = pt.cumulativePnlPct;
        tsMap.set(pt.ts, row);
      }
    }

    // Sort by time
    return Array.from(tsMap.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([ts, values]) => ({ ts, ...values }));
  }, [data, activeAssets]);

  // Summary stats for active single-asset or combined
  const summaryStats = useMemo(() => {
    if (!data) return null;
    return Array.from(activeAssets).map((asset) => {
      const points = data.series[asset] ?? [];
      if (points.length === 0) return { asset, pnl: 0, pct: 0, trades: 0 };
      const last = points[points.length - 1];
      return {
        asset,
        pnl: last.cumulativePnl,
        pct: last.cumulativePnlPct,
        trades: points.length,
        balance: last.balance,
      };
    });
  }, [data, activeAssets]);

  const allAssets = [...CRYPTO_ASSETS, ...METALS_ASSETS];
  const hasData = chartData.length > 0;

  return (
    <div className="space-y-3">
      {/* Filter row */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono-data text-[10px] font-bold uppercase tracking-wider text-muted-foreground">View:</span>
        <div className="flex flex-wrap gap-1.5">
          {allAssets.map((asset) => (
            <FilterBtn
              key={asset}
              label={asset === 'CRYPTO' ? 'All Crypto' : asset}
              active={activeAssets.has(asset)}
              color={ASSET_COLORS[asset] ?? '#888'}
              onClick={() => toggle(asset)}
            />
          ))}
        </div>
      </div>

      {/* Stats strip */}
      {summaryStats && summaryStats.length > 0 && (
        <div className="flex flex-wrap gap-3">
          {summaryStats.map(({ asset, pnl, pct, trades }) => (
            <div key={asset} className="flex items-center gap-2 rounded-lg border border-border/60 bg-background px-3 py-1.5">
              <span
                className="h-2 w-2 rounded-full shrink-0"
                style={{ background: ASSET_COLORS[asset] ?? '#888' }}
              />
              <span className="font-mono-data text-[10px] text-muted-foreground">{ASSET_LABELS[asset] ?? asset}</span>
              <span
                className="font-mono-data text-[11px] font-semibold"
                style={{ color: pct >= 0 ? 'hsl(var(--accent))' : 'hsl(var(--destructive))' }}
              >
                {pct >= 0 ? <TrendingUp className="inline mr-0.5" size={10} /> : <TrendingDown className="inline mr-0.5" size={10} />}
                {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
              </span>
              <span className="font-mono-data text-[10px] text-muted-foreground/60">({trades} trades)</span>
            </div>
          ))}
        </div>
      )}

      {/* Chart area */}
      <div className="h-64 w-full">
        {isLoading && (
          <div className="flex h-full items-center justify-center">
            <span className="font-mono-data text-[11px] text-muted-foreground animate-pulse">Loading equity curve…</span>
          </div>
        )}
        {isError && (
          <div className="flex h-full items-center justify-center">
            <span className="font-mono-data text-[11px] text-destructive">Unable to load P&amp;L data</span>
          </div>
        )}
        {!isLoading && !isError && !hasData && (
          <div className="flex h-full items-center justify-center">
            <span className="font-mono-data text-[11px] text-muted-foreground">No closed trades yet — chart will appear after the first exit</span>
          </div>
        )}
        {!isLoading && !isError && hasData && (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 4, right: 4, left: 0, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" strokeOpacity={0.4} />
              <XAxis
                dataKey="ts"
                tickFormatter={fmtDate}
                tick={{ fontSize: 9, fill: 'hsl(var(--muted-foreground))' }}
                tickLine={false}
                axisLine={false}
                minTickGap={40}
              />
              <YAxis
                tickFormatter={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`}
                tick={{ fontSize: 9, fill: 'hsl(var(--muted-foreground))' }}
                tickLine={false}
                axisLine={false}
                width={52}
              />
              <ReferenceLine y={0} stroke="hsl(var(--muted-foreground))" strokeOpacity={0.5} strokeDasharray="4 4" />
              <Tooltip content={<PnlTooltip />} />
              <Legend
                iconType="circle"
                iconSize={8}
                formatter={(value) => (
                  <span style={{ fontSize: 9, color: 'hsl(var(--muted-foreground))' }}>
                    {ASSET_LABELS[value] ?? value}
                  </span>
                )}
              />
              {Array.from(activeAssets).map((asset) => (
                <Line
                  key={asset}
                  type="monotone"
                  dataKey={asset}
                  name={asset}
                  stroke={ASSET_COLORS[asset] ?? '#888'}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 3, strokeWidth: 0 }}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
