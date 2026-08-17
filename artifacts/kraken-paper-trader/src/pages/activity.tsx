import { Activity, Clock3, RefreshCw, Zap } from 'lucide-react';
import { useListActivityLog, getListActivityLogQueryKey } from '@workspace/api-client-react';
import { TradingShell } from '@/components/trading-shell';

const time = (v: string) => new Date(v).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' });

const EVENT_STYLES: Record<string, string> = {
  TRADE_OPENED:          'text-accent bg-accent/10',
  TRADE_CLOSED:          'text-primary bg-primary/10',
  MARKET_DATA_UPDATED:   'text-muted-foreground bg-muted/50',
  STRATEGY_EVALUATED:    'text-muted-foreground bg-muted/50',
  API_ERROR:             'text-destructive bg-destructive/10',
  RISK_LIMIT_REACHED:    'text-amber-400 bg-amber-400/10',
  ACCOUNT_RESET:         'text-primary bg-primary/10',
};

const COIN_COLORS: Record<string, string> = {
  BTC: 'bg-amber-500/15 text-amber-400',
  ETH: 'bg-violet-500/15 text-violet-400',
  SOL: 'bg-green-500/15 text-green-400',
  XRP: 'bg-blue-500/15 text-blue-400',
};

export default function ActivityLog() {
  const activityQuery = useListActivityLog(
    { limit: 100 },
    { query: { queryKey: getListActivityLogQueryKey({ limit: 100 }), refetchInterval: 30000 } },
  );
  const events = activityQuery.data ?? [];
  const tradeEvents = events.filter((e) => e.event === 'TRADE_OPENED' || e.event === 'TRADE_CLOSED');
  const errorEvents = events.filter((e) => e.event === 'API_ERROR' || e.event === 'RISK_LIMIT_REACHED');

  return (
    <TradingShell eyebrow="Audit desk" title="Activity log" subtitle="Every engine decision and market event, timestamped.">
      <div className="space-y-6">
        {/* Summary row */}
        <section className="rise-in grid gap-3 sm:grid-cols-3">
          <div className="rounded-2xl border border-border/80 bg-card px-5 py-4">
            <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-muted-foreground">Total events</p>
            <p className="mt-2 font-mono-data text-2xl font-medium">{events.length}</p>
            <p className="mt-1 text-[11px] text-muted-foreground">last 100 loaded</p>
          </div>
          <div className="rounded-2xl border border-border/80 bg-card px-5 py-4">
            <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-muted-foreground">Trade events</p>
            <p className="mt-2 font-mono-data text-2xl font-medium text-accent">{tradeEvents.length}</p>
            <p className="mt-1 text-[11px] text-muted-foreground">opens and closes</p>
          </div>
          <div className="rounded-2xl border border-border/80 bg-card px-5 py-4">
            <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-muted-foreground">Alerts</p>
            <p className={`mt-2 font-mono-data text-2xl font-medium ${errorEvents.length > 0 ? 'text-destructive' : 'text-foreground'}`}>{errorEvents.length}</p>
            <p className="mt-1 text-[11px] text-muted-foreground">errors and risk events</p>
          </div>
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
                const style = EVENT_STYLES[event.event] ?? 'text-muted-foreground bg-muted/50';
                const coinStyle = COIN_COLORS[event.coin] ?? 'bg-muted text-muted-foreground';
                const isSignificant = event.event === 'TRADE_OPENED' || event.event === 'TRADE_CLOSED' || event.event === 'RISK_LIMIT_REACHED';
                return (
                  <div key={event.id} className={`flex items-start gap-3 px-5 py-3 sm:px-6 ${isSignificant ? 'bg-card' : ''}`}>
                    <div className="flex min-w-0 flex-1 items-start gap-2.5">
                      <span className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 font-mono-data text-[9px] font-bold uppercase tracking-wider ${coinStyle}`}>
                        {event.coin}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className={`rounded px-1.5 py-0.5 font-mono-data text-[9px] font-semibold uppercase tracking-wider ${style}`}>
                            {event.event.replace(/_/g, ' ')}
                          </span>
                        </div>
                        <p className="mt-0.5 truncate text-[11px] text-muted-foreground">{event.message}</p>
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1 text-muted-foreground/60">
                      <Clock3 size={10} />
                      <span className="font-mono-data text-[10px]">{time(event.ts)}</span>
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
