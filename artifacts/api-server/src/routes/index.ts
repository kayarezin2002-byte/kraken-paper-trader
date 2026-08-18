import { Router, type IRouter } from "express";
import healthRouter from "./health";
import engineRouter from "./engine";
import paperTraderRouter from "./paperTrader";

const router: IRouter = Router();

router.use(healthRouter);
router.use(engineRouter);
router.use(paperTraderRouter);

export default router;
