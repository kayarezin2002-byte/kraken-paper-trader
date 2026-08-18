import { runBot } from "./bot";
import { logger } from "./logger";

/**
 * Server-side strategy scan scheduler.
 *
 * Before this existed, strategy scans only ran when a browser had the
 * dashboard open (the frontend polled the refresh endpoint) — with the page
 * closed, the bot never evaluated a single candle. This runs multi-refresh
 * on a fixed interval for as long as the API server process is up.
 *
 * PAPER TRADING ONLY — the Python bot has a LIVE_TRADING=False hard gate.
 */
export const SCAN_INTERVAL_SECONDS = 120;

type EngineState = {
  status: "RUNNING" | "ERROR" | "STARTING";
  lastScanAt: string | null;
  nextScanAt: string | null;
  intervalSeconds: number;
  lastError: string | null;
  scansCompleted: number;
};

const engine: EngineState = {
  status: "STARTING",
  lastScanAt: null,
  nextScanAt: null,
  intervalSeconds: SCAN_INTERVAL_SECONDS,
  lastError: null,
  scansCompleted: 0,
};

let scanning = false;
let timer: NodeJS.Timeout | null = null;

async function runScan(): Promise<void> {
  if (scanning) return; // overlap guard — never queue up scheduler scans
  scanning = true;
  try {
    // runBot serializes against every other bot invocation (browser refreshes,
    // resets) — a single process-wide queue, so no concurrent SQLite writers.
    await runBot("multi-refresh");
    engine.status = "RUNNING";
    engine.lastError = null;
    engine.scansCompleted += 1;
    engine.lastScanAt = new Date().toISOString();
  } catch (error) {
    engine.status = "ERROR";
    engine.lastError = error instanceof Error ? error.message.slice(0, 500) : String(error);
    logger.error({ err: error }, "Scheduled strategy scan failed");
  } finally {
    engine.nextScanAt = new Date(Date.now() + SCAN_INTERVAL_SECONDS * 1000).toISOString();
    scanning = false;
  }
}

export function startBotScheduler(): void {
  if (timer) return;
  logger.info({ intervalSeconds: SCAN_INTERVAL_SECONDS }, "Starting background strategy scan scheduler");
  void runScan(); // immediate first scan on boot
  timer = setInterval(() => void runScan(), SCAN_INTERVAL_SECONDS * 1000);
  timer.unref();
}

export function engineStatus(): EngineState {
  return { ...engine };
}
