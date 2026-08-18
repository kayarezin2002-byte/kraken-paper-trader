import { Router, type IRouter } from "express";
import { runBot } from "../lib/bot";
import { engineStatus } from "../lib/botScheduler";
import {
  GetChartDataQueryParams,
  GetChartDataResponse,
  GetEngineStatusResponse,
  GetMultiCoinStateResponse,
  GetPaperTraderStateResponse,
  GetPortfolioSummaryResponse,
  ListActivityLogQueryParams,
  ListActivityLogResponse,
  ListAllTradesQueryParams,
  ListAllTradesResponse,
  ListPaperTradesQueryParams,
  ListPaperTradesResponse,
  RefreshMultiCoinResponse,
  RefreshPaperTraderResponse,
  ResetAllCoinsBody,
  ResetAllCoinsResponse,
  ResetPaperTraderBody,
  ResetPaperTraderResponse,
} from "@workspace/api-zod";

const router: IRouter = Router();

// ── Legacy BTC-only endpoints (backward compat) ──────────────────────────────

router.get("/paper-trader/state", async (req, res) => {
  try {
    const data = GetPaperTraderStateResponse.parse(await runBot("state"));
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to read paper trader state");
    res.status(500).json({ error: "Unable to read paper trader state" });
  }
});

router.post("/paper-trader/refresh", async (req, res) => {
  try {
    const data = RefreshPaperTraderResponse.parse(await runBot("refresh"));
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to refresh paper trader");
    res.status(502).json({ error: "Unable to refresh Kraken market data" });
  }
});

router.get("/paper-trader/trades", async (req, res) => {
  try {
    const params = ListPaperTradesQueryParams.parse(req.query);
    const data = ListPaperTradesResponse.parse(
      await runBot("coin-trades", "BTC", String(params.limit)),
    );
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to list paper trades");
    res.status(500).json({ error: "Unable to read simulated trade history" });
  }
});

router.post("/paper-trader/reset", async (req, res) => {
  try {
    const body = ResetPaperTraderBody.parse(req.body ?? {});
    const data = ResetPaperTraderResponse.parse(
      await runBot("reset-coin", "BTC", JSON.stringify(body)),
    );
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to reset paper trader");
    res.status(500).json({ error: "Unable to reset paper trading account" });
  }
});

// ── Multi-coin endpoints ──────────────────────────────────────────────────────

router.get("/paper-trader/multi-state", async (req, res) => {
  try {
    const data = GetMultiCoinStateResponse.parse(await runBot("multi-state"));
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to read multi-coin state");
    res.status(500).json({ error: "Unable to read multi-coin state" });
  }
});

router.post("/paper-trader/multi-refresh", async (req, res) => {
  try {
    const data = RefreshMultiCoinResponse.parse(await runBot("multi-refresh"));
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to refresh multi-coin state");
    res.status(502).json({ error: "Unable to refresh Kraken market data for all coins" });
  }
});

router.get("/paper-trader/engine", (_req, res) => {
  res.json(GetEngineStatusResponse.parse(engineStatus()));
});

router.get("/paper-trader/portfolio", async (req, res) => {
  try {
    const data = GetPortfolioSummaryResponse.parse(await runBot("portfolio"));
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to read portfolio summary");
    res.status(500).json({ error: "Unable to compute portfolio summary" });
  }
});

router.get("/paper-trader/activity", async (req, res) => {
  try {
    const params = ListActivityLogQueryParams.parse(req.query);
    const data = ListActivityLogResponse.parse(
      await runBot("activity", String(params.limit)),
    );
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to list activity log");
    res.status(500).json({ error: "Unable to read activity log" });
  }
});

router.get("/paper-trader/chart", async (req, res) => {
  try {
    const params = GetChartDataQueryParams.parse(req.query);
    const args = ["chart", params.asset, params.range ?? "7D"];
    if (params.interval) args.push(params.interval);
    const data = GetChartDataResponse.parse(await runBot(...(args as [string, ...string[]])));
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to build chart data");
    res.status(502).json({ error: "Unable to fetch chart market data" });
  }
});

router.get("/paper-trader/all-trades", async (req, res) => {
  try {
    const params = ListAllTradesQueryParams.parse(req.query);
    const data = ListAllTradesResponse.parse(
      await runBot("all-trades", String(params.limit)),
    );
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to list all trades");
    res.status(500).json({ error: "Unable to read all trade history" });
  }
});

router.post("/paper-trader/reset-all", async (req, res) => {
  try {
    const body = ResetAllCoinsBody.parse(req.body ?? {});
    const data = ResetAllCoinsResponse.parse(
      await runBot("reset-all", JSON.stringify(body)),
    );
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to reset all coins");
    res.status(500).json({ error: "Unable to reset all paper trading accounts" });
  }
});

export default router;
