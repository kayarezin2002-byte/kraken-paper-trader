import { Router, type IRouter } from "express";
import { engineStatus } from "../lib/botScheduler";
import { GetEngineHealthStatusResponse } from "@workspace/api-zod";

const router: IRouter = Router();

const ALERT_THRESHOLD = parseInt(process.env.ALERT_CONSECUTIVE_ERRORS ?? "3", 10);

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

export default router;
