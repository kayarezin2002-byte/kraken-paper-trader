import { Activity, BookOpen, Briefcase, CircleHelp, FlaskConical, Gauge, Globe, History, ShieldCheck, Waves, Zap } from 'lucide-react';
import { Link, useLocation } from 'wouter';
import { useHealthCheck } from '@workspace/api-client-react';
import type { ReactNode } from 'react';

type TradingShellProps = {
  children: ReactNode;
  eyebrow: string;
  title: string;
  subtitle: string;
};

const NAV_LINKS = [
  { href: '/',         label: 'Dashboard',    Icon: Gauge,   testId: 'link-dashboard'  },
  { href: '/markets',  label: 'Markets',      Icon: Globe,   testId: 'link-markets'    },
  { href: '/lab',      label: 'Strategy Lab', Icon: FlaskConical, testId: 'link-strategy-lab' },
  { href: '/open',     label: 'Open trades',  Icon: Briefcase, testId: 'link-open-trades' },
  { href: '/history',  label: 'Trade history', Icon: History,  testId: 'link-history'   },
  { href: '/activity', label: 'Activity log',  Icon: Zap,      testId: 'link-activity'  },
];

export function TradingShell({ children, eyebrow, title, subtitle }: TradingShellProps) {
  const [location] = useLocation();
  const health = useHealthCheck({
    query: { queryKey: ['/api/healthz'], refetchInterval: 30000 },
  });
  const isHealthy = health.data?.status === 'ok' || health.data?.status === 'healthy';

  return (
    <div className="noise-overlay min-h-[100dvh] bg-background text-foreground">
      {/* ─── Desktop sidebar ────────────────────────────────────────────── */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[246px] flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground lg:flex">
        <div className="flex h-[86px] items-center gap-3 border-b border-sidebar-border px-6">
          <div className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm">
            <Waves size={20} strokeWidth={2.5} />
            <span className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full border-2 border-sidebar bg-accent" />
          </div>
          <div>
            <p className="text-[15px] font-extrabold tracking-tight text-white">TIDE</p>
            <p className="font-mono-data text-[9px] uppercase tracking-[0.18em] text-sidebar-foreground/55">6-instrument learning cockpit</p>
          </div>
        </div>
        <div className="px-4 pt-8">
          <p className="mb-3 px-3 text-[10px] font-bold uppercase tracking-[0.18em] text-sidebar-foreground/45">Workspace</p>
          <nav className="space-y-1">
            {NAV_LINKS.map(({ href, label, Icon, testId }) => {
              const active = href === '/' ? location === '/' : location.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  data-testid={testId}
                  className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-semibold transition-colors ${active ? 'bg-sidebar-accent text-white' : 'text-sidebar-foreground/65 hover:bg-sidebar-accent/70 hover:text-white'}`}
                >
                  <Icon size={17} /> {label}
                  {active && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-primary" />}
                </Link>
              );
            })}
          </nav>
        </div>
        <div className="mt-auto px-4 pb-5">
          <div className="rounded-xl border border-sidebar-border bg-sidebar-accent/55 p-4">
            <div className="mb-3 flex items-center gap-2">
              <ShieldCheck size={16} className="text-primary" />
              <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-white">Risk desk</p>
            </div>
            <p className="text-xs leading-relaxed text-sidebar-foreground/60">Your guardrails are always on. No order ever reaches an exchange.</p>
            <div className="mt-4 flex items-center gap-2 border-t border-sidebar-border pt-3">
              <span className={`h-1.5 w-1.5 rounded-full ${isHealthy ? 'bg-accent pulse-dot' : 'bg-destructive'}`} />
              <span className="font-mono-data text-[10px] uppercase tracking-widest text-sidebar-foreground/55">{isHealthy ? 'system online' : 'checking service'}</span>
            </div>
          </div>
        </div>
      </aside>

      {/* ─── Main content ────────────────────────────────────────────────── */}
      <div className="lg:pl-[246px]">
        <header className="sticky top-0 z-20 border-b border-border/80 bg-background/90 backdrop-blur-xl">
          <div className="mx-auto flex min-h-[86px] max-w-[1480px] items-center justify-between gap-4 px-5 py-4 sm:px-8 xl:px-10">
            <div className="flex min-w-0 items-center gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-sidebar text-primary lg:hidden"><Waves size={18} /></div>
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.17em] text-muted-foreground">
                  <span>{eyebrow}</span><span className="text-border">/</span><span className="text-accent">paper mode</span>
                </div>
                <h1 className="mt-1 truncate text-xl font-extrabold tracking-[-0.03em] sm:text-[25px]">{title}</h1>
                <p className="hidden text-xs text-muted-foreground sm:block">{subtitle}</p>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2 sm:gap-4">
              <div data-testid="status-paper-trading" className="hidden items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-3 py-2 sm:flex">
                <span className="h-1.5 w-1.5 rounded-full bg-primary pulse-dot" />
                <span className="font-mono-data text-[10px] font-medium uppercase tracking-wider text-primary-foreground/80">paper trading only</span>
              </div>
              <div className="hidden items-center gap-2 text-muted-foreground md:flex">
                <Activity size={15} className={isHealthy ? 'text-accent' : 'text-destructive'} />
                <span className="font-mono-data text-[10px] uppercase tracking-wider">{isHealthy ? 'API connected' : 'API pending'}</span>
              </div>
            </div>
          </div>
          <div className="border-t border-primary/15 bg-primary/[0.06] px-5 py-2 sm:px-8 xl:px-10">
            <div className="mx-auto flex max-w-[1480px] items-center gap-2 text-[10px] font-extrabold uppercase tracking-[0.13em] text-foreground/70">
              <ShieldCheck size={13} className="text-primary" />
              Paper trading only <span className="font-medium normal-case tracking-normal text-muted-foreground">— no real money is being traded</span>
            </div>
          </div>
          {/* Mobile nav */}
          <nav className="flex border-t border-border/70 bg-card px-1 py-1 lg:hidden" aria-label="Mobile workspace navigation">
            {NAV_LINKS.map(({ href, label, Icon, testId }) => {
              const active = href === '/' ? location === '/' : location.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  data-testid={`link-mobile-${testId}`}
                  className={`flex flex-1 flex-col items-center justify-center gap-0.5 rounded-md py-1.5 text-[10px] font-bold sm:flex-row sm:gap-1.5 sm:text-xs ${active ? 'bg-muted text-foreground' : 'text-muted-foreground'}`}
                >
                  <Icon size={13} /> {label.replace(' log', '').replace('Trade history', 'History').replace('Open trades', 'Open').replace('Strategy Lab', 'Lab')}
                </Link>
              );
            })}
          </nav>
        </header>

        <main className="mx-auto max-w-[1480px] px-5 py-7 sm:px-8 sm:py-9 xl:px-10">{children}</main>
        <footer className="mx-auto flex max-w-[1480px] flex-col gap-2 px-5 pb-8 pt-2 text-[10px] uppercase tracking-[0.12em] text-muted-foreground/70 sm:flex-row sm:items-center sm:justify-between sm:px-8 xl:px-10">
          <span className="flex items-center gap-2"><BookOpen size={12} /> built for deliberate practice</span>
          <span className="flex items-center gap-2"><CircleHelp size={12} /> market data is informational only</span>
        </footer>
      </div>
    </div>
  );
}
