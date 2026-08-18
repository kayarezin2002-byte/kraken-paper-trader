import { Router, type IRouter, type Request, type Response } from "express";
import { engineStatus, fireTestAlert } from "../lib/botScheduler";
import { GetEngineHealthStatusResponse, TestEngineAlertResponse } from "@workspace/api-zod";

const router: IRouter = Router();

const ALERT_THRESHOLD = parseInt(process.env.ALERT_CONSECUTIVE_ERRORS ?? "3", 10);

/** Express middleware: validates the Bearer token for admin-only routes.
 *
 * Reads ALERT_ADMIN_TOKEN from process.env at request time so tests can
 * override it via vi.stubEnv without reloading the module.
 */
function requireAdminToken(req: Request, res: Response, next: () => void): void {
  const adminToken = process.env.ALERT_ADMIN_TOKEN ?? null;
  if (!adminToken) {
    res.status(503).json({
      ok: false,
      message:
        "Test alerts are disabled — set ALERT_ADMIN_TOKEN (server) and " +
        "VITE_ALERT_ADMIN_TOKEN (frontend) env vars to enable this feature.",
    });
    return;
  }
  const auth = req.headers.authorization;
  const provided = auth?.startsWith("Bearer ") ? auth.slice(7) : null;
  if (!provided || provided !== adminToken) {
    res.status(401).json({ ok: false, message: "Unauthorized — valid ALERT_ADMIN_TOKEN required." });
    return;
  }
  next();
}

/**
 * GET /api/engine/status
 *
 * Uptime-monitor-friendly health check for the background scan scheduler.
 * Returns HTTP 200 when healthy (RUNNING or STARTING) and HTTP 503 when the
 * scheduler is stuck in ERROR state so external monitors (UptimeRobot,
 * Better Uptime, Checkly, etc.) can trigger an alert automatically.
 */
router.get("/engine/status", (_req, res) => {
  const s = engineStatus();
  const isError = s.status === "ERROR";

  const payload = GetEngineHealthStatusResponse.parse({
    status: isError ? "error" : "ok",
    engine: s.status,
    consecutiveErrors: s.consecutiveErrors,
    lastError: s.lastError,
    lastScanAt: s.lastScanAt,
    alertThreshold: ALERT_THRESHOLD,
    alertWebhookConfigured: Boolean(process.env.ALERT_WEBHOOK_URL),
  });

  res.status(isError ? 503 : 200).json(payload);
});

/**
 * POST /api/engine/test-alert
 *
 * Fires a test payload to ALERT_WEBHOOK_URL so you can confirm connectivity
 * without waiting for 3 consecutive scan failures.  The payload is clearly
 * marked as a test (`"test": true` and a "[TEST]" prefix in the message).
 *
 * Returns 200 in all cases so the browser can read the body; check `ok` for
 * actual success/failure.
 */
router.post("/engine/test-alert", requireAdminToken, async (_req, res) => {
  const result = await fireTestAlert();
  const payload = TestEngineAlertResponse.parse(result);
  res.status(200).json(payload);
});

export default router;
