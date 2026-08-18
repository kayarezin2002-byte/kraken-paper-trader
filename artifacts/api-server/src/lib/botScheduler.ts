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

/**
 * Number of consecutive failed scans that trigger a webhook alert.
 * Override via ALERT_CONSECUTIVE_ERRORS env var.
 */
const ALERT_THRESHOLD = parseInt(process.env.ALERT_CONSECUTIVE_ERRORS ?? "3", 10);

/**
 * Webhook URL to POST alerts to when the scheduler enters a persistent
 * error state.  Set ALERT_WEBHOOK_URL in your environment.
 * Supports generic JSON webhooks, Slack incoming webhooks, Make/Zapier, etc.
 */
const ALERT_WEBHOOK_URL = process.env.ALERT_WEBHOOK_URL ?? null;

type EngineState = {
  status: "RUNNING" | "ERROR" | "STARTING";
  lastScanAt: string | null;
  nextScanAt: string | null;
  intervalSeconds: number;
  lastError: string | null;
  scansCompleted: number;
  consecutiveErrors: number;
};

const engine: EngineState = {
  status: "STARTING",
  lastScanAt: null,
  nextScanAt: null,
  intervalSeconds: SCAN_INTERVAL_SECONDS,
  lastError: null,
  scansCompleted: 0,
  consecutiveErrors: 0,
};

let scanning = false;
let timer: NodeJS.Timeout | null = null;

/** True while we are inside an unresolved error streak that has already been
 *  alerted — prevents re-firing the webhook on every subsequent failed scan. */
let streakAlerted = false;

/** Fire-and-forget webhook POST.  Logs on failure but never throws. */
async function fireAlert(message: string, detail: string | null): Promise<void> {
  if (!ALERT_WEBHOOK_URL) return;
  try {
    const body = JSON.stringify({
      // Slack-compatible field (rendered as the message text)
      text: message,
      // Extended fields for generic webhooks / Zapier / Make
      alert: "bot_scan_failure",
      message,
      detail: detail ?? "(no detail)",
      consecutiveErrors: engine.consecutiveErrors,
      threshold: ALERT_THRESHOLD,
      lastScanAt: engine.lastScanAt,
      nextScanAt: engine.nextScanAt,
      timestamp: new Date().toISOString(),
    });
    const res = await fetch(ALERT_WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) {
      logger.warn({ status: res.status }, "Alert webhook returned non-2xx status");
    } else {
      logger.info({ threshold: ALERT_THRESHOLD }, "Alert webhook fired successfully");
    }
  } catch (err) {
    logger.error({ err }, "Failed to send alert webhook");
  }
}

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
    // Recovery: reset the consecutive-error counter and allow the next streak
    // to alert again.
    if (engine.consecutiveErrors > 0) {
      logger.info({ after: engine.consecutiveErrors }, "Scheduler recovered — consecutive error streak cleared");
    }
    engine.consecutiveErrors = 0;
    streakAlerted = false;
  } catch (error) {
    engine.status = "ERROR";
    engine.lastError = error instanceof Error ? error.message.slice(0, 500) : String(error);
    engine.consecutiveErrors += 1;
    logger.error({ err: error, consecutiveErrors: engine.consecutiveErrors }, "Scheduled strategy scan failed");

    // Alert when we breach the threshold for the first time in this streak.
    if (engine.consecutiveErrors >= ALERT_THRESHOLD && !streakAlerted) {
      streakAlerted = true;
      const msg =
        `⚠️ Kraken paper-trader bot stopped scanning — ` +
        `${engine.consecutiveErrors} consecutive scan failures (threshold: ${ALERT_THRESHOLD}).\n` +
        `Last error: ${engine.lastError}`;
      void fireAlert(msg, engine.lastError);
    }
  } finally {
    engine.nextScanAt = new Date(Date.now() + SCAN_INTERVAL_SECONDS * 1000).toISOString();
    scanning = false;
  }
}

export function startBotScheduler(): void {
  if (timer) return;
  if (!ALERT_WEBHOOK_URL) {
    logger.info("ALERT_WEBHOOK_URL not set — scan-failure alerts are disabled");
  } else {
    logger.info(
      { threshold: ALERT_THRESHOLD, url: ALERT_WEBHOOK_URL.replace(/\?.*/, "?…") },
      "Scan-failure alerting enabled",
    );
  }
  logger.info({ intervalSeconds: SCAN_INTERVAL_SECONDS }, "Starting background strategy scan scheduler");
  void runScan(); // immediate first scan on boot
  timer = setInterval(() => void runScan(), SCAN_INTERVAL_SECONDS * 1000);
  timer.unref();
}

export function engineStatus(): EngineState {
  return { ...engine };
}
