import { Router, type IRouter } from "express";
import { runBot } from "../lib/bot";
import { engineStatus } from "../lib/botScheduler";
import {
  GetChartDataQueryParams,
  GetChartDataResponse,
  GetEngineStatusResponse,
  GetMarketAssetParams,
  GetMarketAssetResponse,
  GetMarketDirectoryResponse,
  GetMultiCoinStateResponse,
  GetScannerPositionsResponse,
  GetElliottLabResponse,
  GetLabOverviewResponse,
  GetLabStrategyQueryParams,
  GetLabStrategyResponse,
  GetPaperTraderStateResponse,
  GetPnlSeriesResponse,
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
  SetActiveModeBody,
  SetActiveModeResponse,
  ToggleWatchlistBody,
  ToggleWatchlistResponse,
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

// ── Market scanner endpoints ────────────────────────────────────────────────
router.get("/market/scanner", async (req, res) => {
  try {
    const data = GetMarketDirectoryResponse.parse(await runBot("market-directory"));
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to load market directory");
    res.status(502).json({ error: "Unable to load market directory" });
  }
});

router.get("/market/asset/:ticker", async (req, res) => {
  try {
    const params = GetMarketAssetParams.parse(req.params);
    const data = GetMarketAssetResponse.parse(await runBot("market-asset", params.ticker));
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to load market asset");
    res.status(502).json({ error: "Unable to load market asset" });
  }
});

router.post("/market/watchlist", async (req, res) => {
  try {
    const body = ToggleWatchlistBody.parse(req.body ?? {});
    const data = ToggleWatchlistResponse.parse(await runBot("watchlist-toggle", body.ticker));
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to toggle watchlist");
    res.status(502).json({ error: "Unable to toggle watchlist" });
  }
});

router.get("/market/elliott-lab", async (req, res) => {
  try {
    const data = GetElliottLabResponse.parse(await runBot("elliott-lab"));
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to load Elliott lab");
    res.status(502).json({ error: "Unable to load Elliott lab" });
  }
});

router.get("/market/lab-overview", async (req, res) => {
  try {
    const data = GetLabOverviewResponse.parse(await runBot("lab-overview"));
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to load Strategy Lab overview");
    res.status(502).json({ error: "Unable to load Strategy Lab overview" });
  }
});

router.get("/market/lab-strategy", async (req, res) => {
  try {
    const params = GetLabStrategyQueryParams.parse(req.query);
    const data = GetLabStrategyResponse.parse(
      await runBot(
        "lab-strategy",
        params.strategy,
        String(params.start ?? 1000),
        String(params.risk ?? 1),
      ),
    );
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to load Strategy Lab strategy");
    res.status(502).json({ error: "Unable to load Strategy Lab strategy" });
  }
});

router.get("/market/scanner-positions", async (req, res) => {
  try {
    const data = GetScannerPositionsResponse.parse(await runBot("scanner-positions"));
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to load scanner positions");
    res.status(502).json({ error: "Unable to load scanner positions" });
  }
});

router.get("/paper-trader/pnl-series", async (req, res) => {
  try {
    const data = GetPnlSeriesResponse.parse(await runBot("pnl-series"));
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to compute P&L series");
    res.status(500).json({ error: "Unable to compute P&L time series" });
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

router.post("/paper-trader/active-mode", async (req, res) => {
  try {
    const body = SetActiveModeBody.parse(req.body);
    const data = SetActiveModeResponse.parse(await runBot("set-active-mode", body.mode));
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to set ACTIVE mode");
    res.status(500).json({ error: "Unable to set ACTIVE strategy mode" });
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
