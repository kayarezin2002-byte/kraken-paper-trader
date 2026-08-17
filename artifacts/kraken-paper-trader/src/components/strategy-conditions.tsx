import { CheckCircle2, XCircle, AlertCircle, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import type { StrategyConditions, ProposedTrade } from '@workspace/api-client-react';

const money = (v: number | null | undefined, d = 2) =>
  v == null ? '—' : `£${v.toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const num = (v: number | null | undefined, d = 2) =>
  v == null ? '—' : v.toLocaleString('en-GB', { minimumFractionDigits: d, maximumFractionDigits: d });

type Props = {
  conditions: StrategyConditions | null | undefined;
  proposedTrade: ProposedTrade | null | undefined;
  hasPosition: boolean;
  botStatus: string;
  signal: string;
  compact?: boolean;
};

function statusLabel(
  botStatus: string,
  signal: string,
  hasPosition: boolean,
): { label: string; color: string; icon: React.ReactNode } {
  if (botStatus === 'WAITING_FOR_DATA') return { label: 'WAITING FOR DATA', color: 'text-muted-foreground bg-muted', icon: <AlertCircle size={11} /> };
  if (botStatus === 'API_ERROR') return { label: 'DATA ERROR', color: 'text-destructive bg-destructive/10', icon: <XCircle size={11} /> };
  if (botStatus === 'RISK_PAUSED') return { label: 'RISK LIMIT REACHED', color: 'text-amber-400 bg-amber-400/10', icon: <AlertCircle size={11} /> };
  if (hasPosition) return { label: 'POSITION OPEN', color: 'text-primary bg-primary/10', icon: <TrendingUp size={11} /> };
  if (signal === 'LONG') return { label: 'READY TO ENTER ↑', color: 'text-accent bg-accent/10', icon: <TrendingUp size={11} /> };
  if (signal === 'SHORT') return { label: 'READY TO ENTER ↓', color: 'text-destructive bg-destructive/10', icon: <TrendingDown size={11} /> };
  return { label: 'SCANNING', color: 'text-muted-foreground bg-muted/60', icon: <Minus size={11} /> };
}

export function StrategyConditionsPanel({ conditions, proposedTrade, hasPosition, botStatus, signal, compact }: Props) {
  const status = statusLabel(botStatus, signal, hasPosition);
  const passCount = conditions?.passCount ?? 0;
  const totalCount = conditions?.totalCount ?? 6;
  const progress = totalCount > 0 ? (passCount / totalCount) * 100 : 0;
  const bias = conditions?.bias ?? 'NEUTRAL';

  return (
    <div className="space-y-3">
      {/* Status + bias row */}
      <div className="flex items-center justify-between">
        <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-mono-data text-[10px] font-semibold uppercase tracking-wider ${status.color}`}>
          {status.icon}
          {status.label}
        </span>
        <span className={`font-mono-data text-[10px] font-medium uppercase tracking-wider ${bias === 'LONG' ? 'text-accent' : bias === 'SHORT' ? 'text-destructive' : 'text-muted-foreground'}`}>
          Bias: {bias}
        </span>
      </div>

      {/* Progress bar */}
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-muted-foreground">Entry conditions</span>
          <span className="font-mono-data text-[10px] font-semibold text-foreground">{passCount} / {totalCount} met</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-muted">
          <div
            className={`h-full rounded-full transition-all duration-500 ${passCount === totalCount ? 'bg-accent' : passCount >= totalCount * 0.5 ? 'bg-primary' : 'bg-primary/40'}`}
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      {/* Condition rows */}
      {conditions?.conditions && conditions.conditions.length > 0 && (
        <div className={`space-y-1.5 ${compact ? '' : ''}`}>
          {conditions.conditions.map((cond) => (
            <div key={cond.name} className="flex items-start justify-between gap-2">
              <div className="flex min-w-0 items-center gap-1.5">
                {cond.pass
                  ? <CheckCircle2 size={12} className="mt-0.5 shrink-0 text-accent" />
                  : <XCircle size={12} className="mt-0.5 shrink-0 text-destructive/60" />}
                <span className={`truncate text-[11px] font-medium ${cond.pass ? 'text-foreground' : 'text-muted-foreground'}`}>{cond.name}</span>
              </div>
              <span className={`shrink-0 font-mono-data text-[10px] ${cond.pass ? 'text-accent' : 'text-muted-foreground/70'}`}>
                {cond.currentValue}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Proposed trade */}
      {proposedTrade && !hasPosition && (
        <div className={`rounded-lg border px-3 py-2.5 ${proposedTrade.direction === 'LONG' ? 'border-accent/20 bg-accent/5' : 'border-destructive/20 bg-destructive/5'}`}>
          <p className={`mb-2 font-mono-data text-[10px] font-bold uppercase tracking-wider ${proposedTrade.direction === 'LONG' ? 'text-accent' : 'text-destructive'}`}>
            Proposed {proposedTrade.direction} trade
          </p>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[10px]">
            <div><span className="text-muted-foreground">Entry</span><span className="ml-2 font-mono-data font-semibold">{money(proposedTrade.entry, 4)}</span></div>
            <div><span className="text-muted-foreground">R:R</span><span className="ml-2 font-mono-data font-semibold">1:{num(proposedTrade.rrRatio)}</span></div>
            <div><span className="text-muted-foreground">Stop</span><span className="ml-2 font-mono-data text-destructive/80">{money(proposedTrade.stopLoss, 4)}</span></div>
            <div><span className="text-muted-foreground">Target</span><span className="ml-2 font-mono-data text-accent">{money(proposedTrade.takeProfit, 4)}</span></div>
            <div><span className="text-muted-foreground">Risk</span><span className="ml-2 font-mono-data">{money(proposedTrade.riskAmount)}</span></div>
            <div><span className="text-muted-foreground">Reward</span><span className="ml-2 font-mono-data">{money(proposedTrade.rewardAmount)}</span></div>
          </div>
        </div>
      )}
    </div>
  );
}
