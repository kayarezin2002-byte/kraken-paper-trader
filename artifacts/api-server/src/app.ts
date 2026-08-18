import express, { type Express } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import router from "./routes";
import { logger } from "./lib/logger";

const app: Express = express();

/**
 * Returns true when origin is allowed to make cross-origin requests to this
 * API.  Reads the Replit environment variables so the policy is automatically
 * correct in dev (REPLIT_DEV_DOMAIN) and on deployment (REPLIT_DOMAINS).
 *
 * Curl / server-to-server calls have no Origin header and are always allowed
 * (the cors library passes undefined when the header is absent).
 */
function isAllowedOrigin(origin: string): boolean {
  // Localhost and 127.0.0.1 — any port — for local / Replit workspace dev
  if (/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin)) return true;

  // Replit development domain (e.g. "abc123.replit.dev")
  const devDomain = process.env.REPLIT_DEV_DOMAIN;
  if (devDomain && origin === `https://${devDomain}`) return true;

  // Replit deployment domains (comma-separated)
  const domains = process.env.REPLIT_DOMAINS;
  if (domains) {
    for (const d of domains.split(",")) {
      const trimmed = d.trim();
      if (trimmed && origin === `https://${trimmed}`) return true;
    }
  }

  return false;
}

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
app.use(
  cors({
    origin(origin, callback) {
      // No Origin header = curl / server-to-server — always allowed
      if (!origin) return callback(null, true);
      if (isAllowedOrigin(origin)) return callback(null, true);
      callback(new Error(`CORS: origin '${origin}' is not in the allowlist`));
    },
    credentials: true,
  }),
);
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use("/api", router);

export default app;
