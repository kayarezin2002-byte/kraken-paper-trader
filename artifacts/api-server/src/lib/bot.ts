import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export function botScriptPath(): string {
  const candidates = [
    path.resolve(__dirname, "../../../../python_bot/paper_trader.py"),
    path.resolve(__dirname, "../../../python_bot/paper_trader.py"),
    path.resolve(process.cwd(), "python_bot/paper_trader.py"),
    path.resolve(process.cwd(), "../../python_bot/paper_trader.py"),
  ];
  const script = candidates.find((c) => existsSync(c));
  if (!script) {
    throw new Error("Python paper trader script was not found");
  }
  return script;
}

// Single process-wide lock: every bot invocation (scheduler scans, browser
// refreshes, resets, reads) is serialized so two Python processes never
// write the SQLite database concurrently ("database is locked" / racing
// entry decisions). Each run is quick, so queuing is preferable to failing.
let chain: Promise<unknown> = Promise.resolve();

export async function runBot(command: string, ...args: string[]): Promise<unknown> {
  const run = async () => {
    const script = botScriptPath();
    const { stdout } = await execFileAsync("python3", [script, command, ...args], {
      cwd: path.dirname(script),
      maxBuffer: 20 * 1024 * 1024,
      timeout: 90_000,
    });
    return JSON.parse(stdout.trim()) as unknown;
  };
  const next = chain.then(run, run);
  chain = next.catch(() => undefined); // keep the queue alive after failures
  return next;
}
