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

/** Minimum gap between test-alert deliveries (protects the webhook from spam). */
export const TEST_ALERT_COOLDOWN_MS = 60_000;
type ScanFailureEvent = {
  timestamp: string;
  error: string;
};

type EngineState = {
  status: "RUNNING" | "ERROR" | "STARTING";
  lastScanAt: string | null;
  nextScanAt: string | null;
  intervalSeconds: number;
  lastError: string | null;
  scansCompleted: number;
  consecutiveErrors: number;
  recentFailures: ScanFailureEvent[];
};

/** Maximum number of scan failure events kept in the circular buffer. */
const MAX_RECENT_FAILURES = 10;

const engine: EngineState = {
  status: "STARTING",
  lastScanAt: null,
  nextScanAt: null,
  intervalSeconds: SCAN_INTERVAL_SECONDS,
  lastError: null,
  scansCompleted: 0,
  consecutiveErrors: 0,
  recentFailures: [],
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

/**
 * Send a test webhook alert to verify ALERT_WEBHOOK_URL connectivity.
 *
 * Protected by:
 *  - Single-flight: rejects if a test is already in-flight.
 *  - Rate limit: rejects if called within TEST_ALERT_COOLDOWN_MS of the last attempt.
 *
 * Returns { ok, message } — never throws.
 */
export async function fireTestAlert(): Promise<{ ok: boolean; message: string }> {
  // Read at call time so tests can override via vi.stubEnv without reload
  const webhookUrl = process.env.ALERT_WEBHOOK_URL ?? null;

  if (!webhookUrl) {
    return { ok: false, message: "ALERT_WEBHOOK_URL is not configured — set the env var first." };
  }

  // Single-flight: reject if already in progress
  if (testAlertInProgress) {
    return { ok: false, message: "A test alert is already in progress — please wait." };
  }

  // Rate limit: at most one test per TEST_ALERT_COOLDOWN_MS
  if (lastTestAlertAt != null) {
    const elapsed = Date.now() - lastTestAlertAt;
    if (elapsed < TEST_ALERT_COOLDOWN_MS) {
      const remainingSecs = Math.ceil((TEST_ALERT_COOLDOWN_MS - elapsed) / 1000);
      return { ok: false, message: `Rate limited — please wait ${remainingSecs}s before retrying.` };
    }
  }

  testAlertInProgress = true;
  lastTestAlertAt = Date.now();

  try {
    const msg =
      "[TEST] Kraken paper-trader webhook test — " +
      "this is a connectivity check, not a real alert. " +
      `Sent at ${new Date().toISOString()}.`;
    const body = JSON.stringify({
      // Slack-compatible field
      text: msg,
      // Extended fields for generic webhooks / Zapier / Make
      alert: "bot_scan_failure",
      test: true,
      message: msg,
      detail: "This is a test payload sent from the dashboard to verify webhook connectivity.",
      consecutiveErrors: engine.consecutiveErrors,
      threshold: ALERT_THRESHOLD,
      lastScanAt: engine.lastScanAt,
      nextScanAt: engine.nextScanAt,
      timestamp: new Date().toISOString(),
    });
    const res = await fetch(webhookUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) {
      const detail = `Webhook returned HTTP ${res.status}`;
      logger.warn({ status: res.status }, "Test alert webhook returned non-2xx status");
      return { ok: false, message: detail };
    }
    logger.info("Test alert webhook fired successfully");
    return { ok: true, message: "Test alert delivered successfully." };
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    logger.error({ err }, "Test alert webhook request failed");
    return { ok: false, message: `Request failed: ${detail}` };
  } finally {
    testAlertInProgress = false;
  }
}

/** Expose rate-limit state for tests. */
export function _resetTestAlertState(): void {
  lastTestAlertAt = null;
  testAlertInProgress = false;
}
async function runScan(): Promise<void> {
  if (scanning) return; // overlap guard — never queue up scheduler scans
  scanning = true;
  try {
    // runBot serializes against every other bot invocation (browser refreshes,
    // resets) — a single process-wide queue, so no concurrent SQLite writers.
    await runBot("multi-refresh");
    // Whole-market crypto scanner: same tick cadence; only re-evaluates an
    // asset when a new completed 15m candle is due (lightweight otherwise).
    await runBot("scan-market");
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

    // Append to the circular failure history buffer (cap at MAX_RECENT_FAILURES).
    const failureEvent: ScanFailureEvent = {
      timestamp: new Date().toISOString(),
      error: (error instanceof Error ? error.message : String(error)).slice(0, 200),
    };
    engine.recentFailures.push(failureEvent);
    if (engine.recentFailures.length > MAX_RECENT_FAILURES) {
      engine.recentFailures.shift();
    }

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

/** Module-level state: last time a test alert was dispatched (epoch ms). */
let lastTestAlertAt: number | null = null;

/** True while a test-alert HTTP request is in-flight. */
let testAlertInProgress = false;
