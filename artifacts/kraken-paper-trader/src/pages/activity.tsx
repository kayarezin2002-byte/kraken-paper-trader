import { useState } from 'react';
import { Activity, CheckCircle2, ChevronDown, ChevronUp, Clock3, RefreshCw, XCircle, Zap } from 'lucide-react';
import { useListActivityLog, getListActivityLogQueryKey } from '@workspace/api-client-react';
import { TradingShell } from '@/components/trading-shell';

const time = (v: string) =>
  new Date(v).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' });

const money = (v: number | null | undefined) =>
  v == null ? '—' : `£${v.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const EVENT_STYLES: Record<string, string> = {
  TRADE_OPENED:        'text-accent bg-accent/10',
  TRADE_CLOSED:        'text-primary bg-primary/10',
  MARKET_DATA_UPDATED: 'text-muted-foreground bg-muted/50',
  STRATEGY_EVALUATED:  'text-sky-400 bg-sky-400/10',
  API_ERROR:           'text-destructive bg-destructive/10',
  RISK_LIMIT_REACHED:  'text-amber-400 bg-amber-400/10',
  ACCOUNT_RESET:       'text-primary bg-primary/10',
};

const COIN_COLORS: Record<string, string> = {
  BTC: 'bg-amber-500/15 text-amber-400',
  ETH: 'bg-violet-500/15 text-violet-400',
  SOL: 'bg-green-500/15 text-green-400',
  XRP: 'bg-blue-500/15 text-blue-400',
  GOLD: 'bg-yellow-500/15 text-yellow-400',
  SILVER: 'bg-slate-400/15 text-slate-300',
};

// ─── Diagnostic types ─────────────────────────────────────────────────────────
interface StrategyCondition {
  name: string;
  currentValue: string;
  requiredValue: string;
  pass: boolean;
}

interface StrategyDiagnostic {
  price: number;
  signal: string;
  bias: string;
  oneHourTrend: string;
  fourHourTrend: string;
  passCount: number;
  totalCount: number;
  conditions: StrategyCondition[];
  noTradeReason: string | null;
  executionBlocked: boolean;
  blockReason: string | null;
  /** CORE (1h) or ACTIVE (15m). Absent on pre-upgrade rows → CORE. */
  strategy?: string;
  /** Independent LONG/SHORT scores — the SAME evaluation the dashboard shows. */
  directional?: {
    longScore: number;
    shortScore: number;
    threshold: number;
    shortThreshold: number;
    maxScore: number;
    decision: string;
    reason: string;
  } | null;
}

function tryParseDiagnostic(message: string): StrategyDiagnostic | null {
  try {
    const d = JSON.parse(message);
    if (d && typeof d.signal === 'string' && Array.isArray(d.conditions)) return d as StrategyDiagnostic;
    return null;
  } catch {
    return null;
  }
}

// ─── Expandable strategy diagnostic panel ────────────────────────────────────
function StrategyDiagnosticPanel({ diag }: { diag: StrategyDiagnostic }) {
  const allPass = diag.passCount === diag.totalCount;
  const signalColor =
    diag.signal === 'LONG'     ? 'text-accent'
    : diag.signal === 'SHORT'  ? 'text-destructive'
    : 'text-muted-foreground';

  return (
    <div className="mt-3 space-y-3 rounded-xl border border-border/60 bg-muted/30 p-4">
      {/* Header row */}
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-[11px]">
        <span className={`rounded px-1.5 py-0.5 font-mono-data text-[9px] font-bold uppercase tracking-wider ${(diag.strategy ?? 'CORE') === 'ACTIVE' ? 'bg-cyan-500/15 text-cyan-500' : 'bg-blue-500/10 text-blue-500'}`}>
          {(diag.strategy ?? 'CORE') === 'ACTIVE' ? 'ACTIVE' : 'HIGH-CONF'}
        </span>
        <div>
          <span className="text-muted-foreground">Price </span>
          <span className="font-mono-data font-semibold">{money(diag.price)}</span>
        </div>
        <div>
          <span className="text-muted-foreground">1h trend </span>
          <span className={`font-mono-data font-semibold ${diag.oneHourTrend === 'BULLISH' ? 'text-accent' : diag.oneHourTrend === 'BEARISH' ? 'text-destructive' : 'text-muted-foreground'}`}>
            {diag.oneHourTrend}
          </span>
        </div>
        <div>
          <span className="text-muted-foreground">4h trend </span>
          <span className={`font-mono-data font-semibold ${diag.fourHourTrend === 'BULLISH' ? 'text-accent' : diag.fourHourTrend === 'BEARISH' ? 'text-destructive' : 'text-muted-foreground'}`}>
            {diag.fourHourTrend}
          </span>
        </div>
        <div>
          <span className="text-muted-foreground">Bias </span>
          <span className={`font-mono-data font-semibold ${diag.bias === 'LONG' ? 'text-accent' : diag.bias === 'SHORT' ? 'text-destructive' : 'text-muted-foreground'}`}>
            {diag.bias}
          </span>
        </div>
      </div>

      {/* Condition rows */}
      <div className="space-y-2">
        {diag.conditions.map((cond) => (
          <div key={cond.name} className="flex flex-wrap items-start gap-2">
            <div className="flex shrink-0 items-center gap-1.5">
              {cond.pass
                ? <CheckCircle2 size={12} className="shrink-0 text-accent" />
                : <XCircle size={12} className="shrink-0 text-destructive/70" />}
              <span className={`font-mono-data text-[11px] font-semibold ${cond.pass ? 'text-foreground' : 'text-muted-foreground'}`}>
                {cond.name}
              </span>
            </div>
            <span className="font-mono-data text-[11px] text-muted-foreground">
              {cond.currentValue}
            </span>
            <span className={`ml-auto shrink-0 rounded px-2 py-0.5 font-mono-data text-[10px] font-bold uppercase tracking-wider ${cond.pass ? 'bg-accent/10 text-accent' : 'bg-destructive/10 text-destructive'}`}>
              {cond.pass ? 'PASS' : 'FAIL'}
            </span>
            {!cond.pass && (
              <span className="w-full pl-5 text-[10px] text-muted-foreground/70">
                Required: {cond.requiredValue}
              </span>
            )}
          </div>
        ))}
      </div>

      {/* Score footer — same directional evaluation the dashboard shows */}
      <div className="border-t border-border/50 pt-3 text-[11px]">
        <div className="flex flex-wrap gap-x-6 gap-y-1.5">
          {diag.directional ? (
            <div>
              <span className="text-muted-foreground">Scores </span>
              <span className="font-mono-data font-bold text-accent">
                LONG {diag.directional.longScore}/{diag.directional.maxScore}
              </span>
              <span className="mx-1 text-border">·</span>
              <span className="font-mono-data font-bold text-destructive">
                SHORT {diag.directional.shortScore}/{diag.directional.maxScore}
              </span>
              <span className="ml-1 text-muted-foreground">
                gate: {diag.directional.threshold}
                {diag.directional.shortThreshold !== diag.directional.threshold ? `/${diag.directional.shortThreshold}` : ''}
                /{diag.directional.maxScore}
              </span>
            </div>
          ) : (
            <div>
              <span className="text-muted-foreground">Conditions met </span>
              <span className={`font-mono-data font-bold ${allPass ? 'text-accent' : 'text-foreground'}`}>
                {diag.passCount}/{diag.totalCount}
              </span>
            </div>
          )}
          <div>
            <span className="text-muted-foreground">Final signal </span>
            <span className={`font-mono-data font-bold ${signalColor}`}>
              {diag.signal.replace('_', ' ')}
            </span>
          </div>
        </div>

        {/* No-trade reason */}
        {diag.noTradeReason && (
          <div className="mt-2 rounded-lg bg-muted/60 px-3 py-2">
            <span className="font-bold uppercase tracking-wider text-[10px] text-muted-foreground">Reason </span>
            <span className="text-[11px] text-foreground/80">{diag.noTradeReason}</span>
          </div>
        )}

        {/* Execution block */}
        {diag.executionBlocked && diag.blockReason && (
          <div className="mt-2 rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2">
            <p className="font-mono-data text-[10px] font-bold uppercase tracking-wider text-amber-400">Signal: {diag.signal} — Execution blocked</p>
            <p className="mt-0.5 text-[11px] text-amber-300/80">{diag.blockReason}</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Collapsed strategy summary (single line) ─────────────────────────────────
function StrategyCollapsedSummary({ diag }: { diag: StrategyDiagnostic }) {
  const signalColor =
    diag.signal === 'LONG'    ? 'text-accent'
    : diag.signal === 'SHORT' ? 'text-destructive'
    : 'text-muted-foreground';

  const failedCount = diag.totalCount - diag.passCount;
  const reason = diag.noTradeReason
    ?? (diag.executionBlocked ? `Signal ${diag.signal} — execution blocked` : null);

  return (
    <p className="mt-0.5 text-[11px] text-muted-foreground">
      <span className="font-semibold text-muted-foreground/80">{(diag.strategy ?? 'CORE') === 'ACTIVE' ? 'ACTIVE' : 'HIGH-CONF'}</span>
      <span className="mx-1.5 text-border">·</span>
      <span className={`font-mono-data font-semibold ${signalColor}`}>{diag.signal.replace('_', ' ')}</span>
      <span className="mx-1.5 text-border">·</span>
      <span>
        {diag.directional
          ? `L ${diag.directional.longScore}/${diag.directional.maxScore} · S ${diag.directional.shortScore}/${diag.directional.maxScore}`
          : `${diag.passCount}/${diag.totalCount} conditions`}
      </span>
      {failedCount > 0 && (
        <>
          <span className="mx-1.5 text-border">·</span>
          <span className="text-destructive/80">{failedCount} failed</span>
        </>
      )}
      {reason && (
        <>
          <span className="mx-1.5 text-border">·</span>
          <span className="italic">{reason}</span>
        </>
      )}
    </p>
  );
}

// ─── Main component ───────────────────────────────────────────────────────────
export default function ActivityLog() {
  const activityQuery = useListActivityLog(
    { limit: 100 },
    { query: { queryKey: getListActivityLogQueryKey({ limit: 100 }), refetchInterval: 30000 } },
  );
  const events = activityQuery.data ?? [];
  const tradeEvents  = events.filter((e) => e.event === 'TRADE_OPENED' || e.event === 'TRADE_CLOSED');
  const errorEvents  = events.filter((e) => e.event === 'API_ERROR' || e.event === 'RISK_LIMIT_REACHED');
  const scanEvents   = events.filter((e) => e.event === 'STRATEGY_EVALUATED');

  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const toggle = (id: number) => setExpanded((prev) => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  return (
    <TradingShell eyebrow="Audit desk" title="Activity log" subtitle="Every engine decision and market event, timestamped.">
      <div className="space-y-6">
        {/* Summary row */}
        <section className="rise-in grid gap-3 sm:grid-cols-4">
          {[
            { label: 'Total events',   value: events.length,      sub: 'last 100 loaded',     tone: '' },
            { label: 'Trade events',   value: tradeEvents.length,  sub: 'opens and closes',    tone: 'text-accent' },
            { label: 'Strategy scans', value: scanEvents.length,   sub: 'tap any row to expand', tone: 'text-sky-400' },
            { label: 'Alerts',         value: errorEvents.length,  sub: 'errors and risk events', tone: errorEvents.length > 0 ? 'text-destructive' : '' },
          ].map(({ label, value, sub, tone }) => (
            <div key={label} className="rounded-2xl border border-border/80 bg-card px-5 py-4">
              <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-muted-foreground">{label}</p>
              <p className={`mt-2 font-mono-data text-2xl font-medium ${tone}`}>{value}</p>
              <p className="mt-1 text-[11px] text-muted-foreground">{sub}</p>
            </div>
          ))}
        </section>

        {/* Log feed */}
        <section className="rise-in rise-in-delay-1 overflow-hidden rounded-2xl border border-border/80 bg-card">
          <div className="flex items-center justify-between border-b border-border/70 px-5 py-4 sm:px-6">
            <div className="flex items-center gap-2">
              <Activity size={16} className="text-primary" />
              <h2 className="text-sm font-extrabold">Event stream</h2>
            </div>
            {activityQuery.isRefetching && <RefreshCw size={13} className="animate-spin text-muted-foreground" />}
          </div>

          {activityQuery.isLoading && !events.length ? (
            <div className="space-y-2 p-5 sm:p-6">
              {[1, 2, 3, 4, 5].map((i) => (
                <div key={i} className="h-10 rounded-lg skeleton-shimmer" />
              ))}
            </div>
          ) : events.length === 0 ? (
            <div className="flex flex-col items-center px-5 py-16 text-center">
              <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-muted text-muted-foreground">
                <Zap size={20} />
              </div>
              <p className="mt-3 text-sm font-bold">No activity yet</p>
              <p className="mt-1 max-w-xs text-xs leading-relaxed text-muted-foreground">Events appear here once the engine starts refreshing market data.</p>
            </div>
          ) : (
            <div className="divide-y divide-border/50">
              {events.map((event) => {
                const style     = EVENT_STYLES[event.event] ?? 'text-muted-foreground bg-muted/50';
                const coinStyle = COIN_COLORS[event.coin]   ?? 'bg-muted text-muted-foreground';
                const isSignificant = ['TRADE_OPENED', 'TRADE_CLOSED', 'RISK_LIMIT_REACHED'].includes(event.event);
                const isStrategy = event.event === 'STRATEGY_EVALUATED';
                const diag = isStrategy ? tryParseDiagnostic(event.message) : null;
                const isOpen = expanded.has(event.id);

                return (
                  <div
                    key={event.id}
                    className={`px-5 py-3 sm:px-6 ${isSignificant ? 'bg-card' : ''} ${isStrategy ? 'cursor-pointer hover:bg-muted/20 transition-colors' : ''}`}
                    onClick={isStrategy ? () => toggle(event.id) : undefined}
                  >
                    <div className="flex items-start gap-3">
                      <div className="flex min-w-0 flex-1 items-start gap-2.5">
                        <span className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 font-mono-data text-[9px] font-bold uppercase tracking-wider ${coinStyle}`}>
                          {event.coin}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className={`rounded px-1.5 py-0.5 font-mono-data text-[9px] font-semibold uppercase tracking-wider ${style}`}>
                              {event.event.replace(/_/g, ' ')}
                            </span>
                            {isStrategy && diag && (
                              <span className="text-[10px] text-muted-foreground/60">tap to expand</span>
                            )}
                          </div>

                          {/* Message / collapsed summary */}
                          {isStrategy && diag ? (
                            <StrategyCollapsedSummary diag={diag} />
                          ) : (
                            <p className="mt-0.5 break-words text-[11px] text-muted-foreground">{event.message}</p>
                          )}

                          {/* Expanded diagnostic */}
                          {isStrategy && diag && isOpen && (
                            <StrategyDiagnosticPanel diag={diag} />
                          )}
                        </div>
                      </div>

                      <div className="flex shrink-0 items-center gap-1 text-muted-foreground/60">
                        <Clock3 size={10} />
                        <span className="font-mono-data text-[10px]">{time(event.ts)}</span>
                        {isStrategy && (
                          <span className="ml-1 text-muted-foreground/40">
                            {isOpen ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </TradingShell>
  );
}
