import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { promisify } from "node:util";
import { Router, type IRouter } from "express";
import {
  GetPaperTraderStateResponse,
  ListPaperTradesQueryParams,
  ListPaperTradesResponse,
  RefreshPaperTraderResponse,
  ResetPaperTraderBody,
  ResetPaperTraderResponse,
} from "@workspace/api-zod";

const execFileAsync = promisify(execFile);
const router: IRouter = Router();

function botScriptPath() {
  const candidates = [
    path.resolve(__dirname, "../../../python_bot/paper_trader.py"),
    path.resolve(process.cwd(), "python_bot/paper_trader.py"),
  ];
  const script = candidates.find((candidate) => existsSync(candidate));
  if (!script) {
    throw new Error("Python paper trader script was not found");
  }
  return script;
}

async function runBot(command: string, argument?: string) {
  const args = [botScriptPath(), command];
  if (argument) args.push(argument);
  const { stdout } = await execFileAsync("python3", args, {
    cwd: path.dirname(botScriptPath()),
    maxBuffer: 2 * 1024 * 1024,
    timeout: 30_000,
  });
  return JSON.parse(stdout.trim()) as unknown;
}

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
    res.status(502).json({ error: "Unable to refresh public Kraken market data" });
  }
});

router.get("/paper-trader/trades", async (req, res) => {
  try {
    const params = ListPaperTradesQueryParams.parse(req.query);
    const data = ListPaperTradesResponse.parse(
      await runBot("trades", String(params.limit)),
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
      await runBot("reset", JSON.stringify(body)),
    );
    res.json(data);
  } catch (error) {
    req.log.error({ err: error }, "Unable to reset paper trader");
    res.status(500).json({ error: "Unable to reset paper trading account" });
  }
});

export default router;