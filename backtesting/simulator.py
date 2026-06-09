"""
SIMULATOR — Motor de simulación de trades históricos
Toma barras OHLCV y ejecuta la lógica SMC institucional:
detecta patrones (BOS/OB/FVG), simula entradas con slippage/spread real,
gestiona el trade con trailing stop + breakeven, y calcula métricas completas.

Walk-Forward 70/30 | Monte Carlo | Stats por sesión/patrón/año/hora
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from backtesting.data_fetcher import Bar, SPREAD_REAL, SYMBOL_START_YEAR

logger = logging.getLogger("Simulator")

# ─── Constantes ───────────────────────────────────────────────

CHECKPOINT_FILE = "backtest_checkpoint.json"
RESULTS_FILE    = "backtest_results.json"
LEARNING_FILE   = "backtest_learning.json"

SLIPPAGE_PIPS = {
    "BTCUSDm": 5.0,
    "ETHUSDm": 0.5,
    "XAUUSDm": 0.05,
    "EURUSDm": 0.00005,
    "USTECm":  1.0,
}


# ─── Estructuras ─────────────────────────────────────────────

@dataclass
class BacktestTrade:
    symbol: str
    direction: str       # "buy" | "sell"
    entry: float
    sl: float
    tp: float
    entry_bar: int
    exit_bar: int    = -1
    exit_price: float = 0.0
    outcome: str     = "pending"  # win | loss | breakeven
    pnl_r: float     = 0.0        # P&L en unidades de riesgo
    rr_achieved: float = 0.0
    bars_held: int   = 0
    pattern: str     = ""
    score: float     = 0.0
    slippage: float  = 0.0
    year: int        = 0
    hour: int        = 0
    session: str     = ""


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    source: str        = ""
    total_trades: int  = 0
    wins: int          = 0
    losses: int        = 0
    breakevens: int    = 0
    win_rate: float    = 0.0
    profit_factor: float = 0.0
    expectancy: float  = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    total_r: float     = 0.0
    avg_win_r: float   = 0.0
    avg_loss_r: float  = 0.0
    avg_rr: float      = 0.0
    avg_bars_held: float = 0.0
    avg_slippage: float = 0.0
    trades: list       = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    yearly_stats: dict = field(default_factory=dict)
    session_stats: dict = field(default_factory=dict)
    pattern_stats: dict = field(default_factory=dict)
    best_hours: list   = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol":        self.symbol,
            "timeframe":     self.timeframe,
            "source":        self.source,
            "period":        f"{self.start_date} → {self.end_date}",
            "trades":        self.total_trades,
            "win_rate":      round(self.win_rate * 100, 1),
            "profit_factor": round(self.profit_factor, 2),
            "expectancy":    round(self.expectancy, 4),
            "max_drawdown":  round(self.max_drawdown * 100, 2),
            "sharpe":        round(self.sharpe_ratio, 2),
            "total_r":       round(self.total_r, 2),
            "avg_rr":        round(self.avg_rr, 2),
            "avg_slippage":  round(self.avg_slippage, 5),
            "yearly_stats":  self.yearly_stats,
            "session_stats": self.session_stats,
            "pattern_stats": self.pattern_stats,
            "best_hours":    self.best_hours,
        }

    def print_report(self):
        print(f"\n{'='*60}")
        print(f"  BACKTEST — {self.symbol} {self.timeframe} [{self.source}]")
        print(f"{'='*60}")
        print(f"  Periodo:       {self.start_date} → {self.end_date}")
        print(f"  Total trades:  {self.total_trades}")
        print(f"  Win Rate:      {self.win_rate*100:.1f}%")
        print(f"  Profit Factor: {self.profit_factor:.2f}")
        print(f"  Expectancy:    {self.expectancy:.4f}R")
        print(f"  Max Drawdown:  {self.max_drawdown*100:.2f}%")
        print(f"  Sharpe:        {self.sharpe_ratio:.2f}")
        print(f"  Total R:       {self.total_r:.2f}R")
        print(f"  Avg RR:        {self.avg_rr:.2f}")
        if self.pattern_stats:
            print(f"\n  Mejores patrones:")
            sorted_p = sorted(
                self.pattern_stats.items(),
                key=lambda x: x[1].get("wr", 0), reverse=True
            )
            for pat, st in sorted_p[:3]:
                print(f"    {pat}: WR:{st['wr']:.0f}% Trades:{st['trades']}")
        if self.session_stats:
            print(f"\n  Stats por sesión:")
            for ses, st in self.session_stats.items():
                print(
                    f"    {ses}: WR:{st['wr']:.0f}% "
                    f"Trades:{st['trades']} R:{st['total_r']:.1f}"
                )
        if self.best_hours:
            print(f"\n  Mejores horas UTC: {self.best_hours[:5]}")
        if self.yearly_stats:
            print(f"\n  Stats por año:")
            for yr, st in sorted(self.yearly_stats.items()):
                print(
                    f"    {yr} | WR:{st['wr']:.0f}% | "
                    f"Trades:{st['trades']} | R:{st['total_r']:.1f}"
                )
        print(f"{'='*60}\n")


@dataclass
class MonteCarloResult:
    simulations: int
    median_r: float
    p5: float
    p25: float
    p75: float
    p95: float
    prob_positive: float
    max_dd_median: float

    def print_report(self, symbol: str):
        print(f"\n{'='*50}")
        print(f"  MONTE CARLO — {symbol} ({self.simulations} sims)")
        print(f"{'='*50}")
        print(f"  Mediana retorno: {self.median_r:.2f}R")
        print(f"  Percentil  5%:   {self.p5:.2f}R")
        print(f"  Percentil 95%:   {self.p95:.2f}R")
        print(f"  Prob. positivo:  {self.prob_positive*100:.1f}%")
        print(f"  Max DD mediana:  {self.max_dd_median*100:.1f}%")
        print(f"{'='*50}\n")


# ─── Helpers ──────────────────────────────────────────────────

def get_session(hour_utc: int) -> str:
    if 0 <= hour_utc < 7:
        return "asia"
    elif 7 <= hour_utc < 9:
        return "london_open"
    elif 9 <= hour_utc < 12:
        return "london"
    elif 12 <= hour_utc < 17:
        return "ny_overlap"
    elif 17 <= hour_utc < 22:
        return "ny"
    else:
        return "close"


# ─── Motor de simulación ─────────────────────────────────────

class Simulator:
    """
    Simula trades sobre barras históricas usando lógica SMC:
    BOS/CHoCH, Order Block, Fair Value Gap.
    Incluye trailing stop, breakeven y slippage real.
    """

    def __init__(self):
        self._checkpoint = self._load_checkpoint()

    # ── Checkpoint ────────────────────────────────────────────

    def _load_checkpoint(self) -> dict:
        if os.path.exists(CHECKPOINT_FILE):
            try:
                with open(CHECKPOINT_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_checkpoint(self, symbol: str, year: int, stats: dict):
        self._checkpoint[f"{symbol}_{year}"] = {
            "year":      year,
            "stats":     stats,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(CHECKPOINT_FILE, "w") as f:
                json.dump(self._checkpoint, f, indent=2)
        except Exception as e:
            logger.error(f"Checkpoint save: {e}")

    def _is_checkpointed(self, symbol: str, year: int) -> bool:
        return f"{symbol}_{year}" in self._checkpoint

    # ── Walk-Forward ──────────────────────────────────────────

    def walk_forward(
        self,
        bars: list,
        symbol: str,
        timeframe: str,
        source: str,
        min_rr: float = 2.5,
        score_threshold: float = 0.65,
    ) -> BacktestResult:
        """
        Divide los datos en 70% train / 30% test (OOS).
        Reporta los resultados del set de test.
        """
        split      = int(len(bars) * 0.70)
        train_bars = bars[:split]
        test_bars  = bars[split:]

        logger.info(
            f"{symbol}: Walk-Forward 70/30 | "
            f"Train: {train_bars[0].time[:10]}→{train_bars[-1].time[:10]} "
            f"({len(train_bars)}) | "
            f"Test: {test_bars[0].time[:10]}→{test_bars[-1].time[:10]} "
            f"({len(test_bars)})"
        )

        train_trades  = self.simulate_trades(train_bars, symbol, min_rr, score_threshold)
        train_result  = self.calculate_metrics(train_trades, train_bars, symbol, timeframe, "TRAIN")
        logger.info(
            f"  Train → WR:{train_result.win_rate*100:.1f}% "
            f"PF:{train_result.profit_factor:.2f} "
            f"Trades:{train_result.total_trades}"
        )

        test_trades  = self.simulate_trades(test_bars, symbol, min_rr, score_threshold)
        test_result  = self.calculate_metrics(test_trades, test_bars, symbol, timeframe, "TEST OOS")
        logger.info(
            f"  Test  → WR:{test_result.win_rate*100:.1f}% "
            f"PF:{test_result.profit_factor:.2f} "
            f"Trades:{test_result.total_trades}"
        )

        return test_result

    # ── Simulación de trades ──────────────────────────────────

    def simulate_trades(
        self,
        bars: list,
        symbol: str,
        min_rr: float = 2.5,
        score_threshold: float = 0.65,
    ) -> list:
        """
        Detecta patrones SMC en las barras y simula cada trade.
        Patrones: BOS_BULL/BEAR, OB_BULL/BEAR, FVG_BULL/BEAR.
        """
        trades   = []
        lookback = 20

        for i in range(lookback + 5, len(bars) - 10):
            window  = bars[max(0, i - lookback):i]
            bar     = bars[i]
            current = bar.close
            atr_val = self._atr(bars, i)

            if atr_val == 0 or not window:
                continue

            swing_high = max(b.high for b in window)
            swing_low  = min(b.low  for b in window)
            avg_vol    = float(np.mean([b.volume for b in window]))

            try:
                hour_utc = int(bar.time[11:13])
            except Exception:
                hour_utc = 0

            direction = score = None
            pattern   = ""

            # ── BOS Breakout ──────────────────────────────────
            if (
                current > swing_high
                and bar.close > window[-1].close
                and bar.volume > avg_vol * 1.3
            ):
                direction, score, pattern = "buy",  0.68, "BOS_BULL"

            elif (
                current < swing_low
                and bar.close < window[-1].close
                and bar.volume > avg_vol * 1.3
            ):
                direction, score, pattern = "sell", 0.68, "BOS_BEAR"

            # ── Order Block ───────────────────────────────────
            elif (
                i >= 3
                and bars[i-2].close < bars[i-2].open
                and bars[i-1].close > bars[i-2].high
                and current > bars[i-1].close
            ):
                direction, score, pattern = "buy",  0.72, "OB_BULL"

            elif (
                i >= 3
                and bars[i-2].close > bars[i-2].open
                and bars[i-1].close < bars[i-2].low
                and current < bars[i-1].close
            ):
                direction, score, pattern = "sell", 0.72, "OB_BEAR"

            # ── Fair Value Gap ────────────────────────────────
            elif (
                i >= 2
                and bar.low > bars[i-2].high
                and (bar.low - bars[i-2].high) / (current + 1e-9) > 0.001
            ):
                direction, score, pattern = "buy",  0.66, "FVG_BULL"

            elif (
                i >= 2
                and bar.high < bars[i-2].low
                and (bars[i-2].low - bar.high) / (current + 1e-9) > 0.001
            ):
                direction, score, pattern = "sell", 0.66, "FVG_BEAR"

            if direction is None or score < score_threshold:
                continue

            slip   = self._slippage(symbol)
            spread = bar.spread

            if direction == "buy":
                entry = current + slip + spread
                sl    = entry - atr_val * 1.5
                tp    = entry + atr_val * 1.5 * min_rr
            else:
                entry = current - slip - spread
                sl    = entry + atr_val * 1.5
                tp    = entry - atr_val * 1.5 * min_rr

            risk = abs(entry - sl)
            if risk == 0:
                continue

            year  = int(bar.time[:4]) if bar.time else 0
            trade = BacktestTrade(
                symbol=symbol, direction=direction,
                entry=entry, sl=sl, tp=tp,
                entry_bar=i, score=score,
                pattern=pattern, slippage=slip,
                year=year, hour=hour_utc,
                session=get_session(hour_utc),
            )
            trade = self._simulate_outcome(trade, bars, i)
            trades.append(trade)

            # Checkpoint por año
            if year and not self._is_checkpointed(symbol, year):
                yr_t = [t for t in trades if t.year == year and t.outcome != "pending"]
                if yr_t:
                    yr_w = sum(1 for t in yr_t if t.outcome == "win")
                    self._save_checkpoint(symbol, year, {
                        "trades":  len(yr_t),
                        "wr":      round(yr_w / len(yr_t) * 100, 1),
                        "total_r": round(sum(t.pnl_r for t in yr_t), 2),
                    })

        return trades

    def _simulate_outcome(
        self, trade: BacktestTrade, bars: list, entry_bar: int
    ) -> BacktestTrade:
        """
        Simula el resultado del trade barra a barra con:
        - Breakeven al 1R de ganancia
        - Trailing stop activado en breakeven
        - TP fijo al objetivo de RR
        - Máximo 100 barras antes de expirar como breakeven
        """
        be_activated = False
        trailing_sl  = trade.sl
        risk         = abs(trade.entry - trade.sl)

        for j in range(entry_bar + 1, min(entry_bar + 100, len(bars))):
            bar = bars[j]

            if trade.direction == "buy":
                # Activar breakeven al 1R
                if not be_activated and bar.high >= trade.entry + risk:
                    trailing_sl  = trade.entry
                    be_activated = True
                # Mover trailing
                if be_activated:
                    new_trail = bar.high - risk * 0.8
                    if new_trail > trailing_sl:
                        trailing_sl = new_trail
                # SL tocado
                if bar.low <= trailing_sl:
                    trade.outcome    = "win" if be_activated else "loss"
                    trade.exit_price = trailing_sl
                    trade.exit_bar   = j
                    pnl              = trailing_sl - trade.entry
                    trade.pnl_r      = pnl / (risk + 1e-9)
                    trade.rr_achieved = abs(trade.pnl_r)
                    break
                # TP tocado
                if bar.high >= trade.tp:
                    trade.outcome    = "win"
                    trade.exit_price = trade.tp
                    trade.exit_bar   = j
                    trade.pnl_r      = (trade.tp - trade.entry) / (risk + 1e-9)
                    trade.rr_achieved = abs(trade.pnl_r)
                    break

            else:  # sell
                if not be_activated and bar.low <= trade.entry - risk:
                    trailing_sl  = trade.entry
                    be_activated = True
                if be_activated:
                    new_trail = bar.low + risk * 0.8
                    if new_trail < trailing_sl:
                        trailing_sl = new_trail
                if bar.high >= trailing_sl:
                    trade.outcome    = "win" if be_activated else "loss"
                    trade.exit_price = trailing_sl
                    trade.exit_bar   = j
                    pnl              = trade.entry - trailing_sl
                    trade.pnl_r      = pnl / (risk + 1e-9)
                    trade.rr_achieved = abs(trade.pnl_r)
                    break
                if bar.low <= trade.tp:
                    trade.outcome    = "win"
                    trade.exit_price = trade.tp
                    trade.exit_bar   = j
                    trade.pnl_r      = (trade.entry - trade.tp) / (risk + 1e-9)
                    trade.rr_achieved = abs(trade.pnl_r)
                    break

        # Expiró sin resultado
        if trade.outcome == "pending":
            trade.outcome     = "breakeven"
            trade.exit_price  = bars[min(entry_bar + 30, len(bars) - 1)].close
            trade.pnl_r       = 0.0
            trade.rr_achieved = 0.0

        trade.bars_held = max(
            0,
            (trade.exit_bar if trade.exit_bar > 0 else entry_bar + 30) - entry_bar
        )
        return trade

    # ── Métricas ──────────────────────────────────────────────

    def calculate_metrics(
        self,
        trades: list,
        bars: list,
        symbol: str,
        timeframe: str,
        source: str,
    ) -> BacktestResult:
        result = BacktestResult(
            symbol=symbol, timeframe=timeframe,
            start_date="", end_date="", source=source,
        )
        result.trades       = trades
        result.total_trades = len(trades)

        if not trades:
            return result

        wins   = [t for t in trades if t.outcome == "win"]
        losses = [t for t in trades if t.outcome == "loss"]
        result.wins       = len(wins)
        result.losses     = len(losses)
        result.breakevens = len(trades) - len(wins) - len(losses)
        result.win_rate   = len(wins) / len(trades)

        win_rs  = [t.pnl_r for t in wins]
        loss_rs = [abs(t.pnl_r) for t in losses]
        result.avg_win_r     = float(np.mean(win_rs))  if win_rs  else 0.0
        result.avg_loss_r    = float(np.mean(loss_rs)) if loss_rs else 0.0
        result.profit_factor = sum(win_rs) / (sum(loss_rs) + 1e-9)
        result.total_r       = sum(t.pnl_r for t in trades)
        result.expectancy    = (
            result.win_rate * result.avg_win_r
            - (1 - result.win_rate) * result.avg_loss_r
        )

        rrs = [t.rr_achieved for t in trades if t.rr_achieved > 0]
        result.avg_rr = float(np.mean(rrs)) if rrs else 0.0

        bh = [t.bars_held for t in trades if t.bars_held > 0]
        result.avg_bars_held = float(np.mean(bh)) if bh else 0.0
        result.avg_slippage  = float(np.mean([t.slippage for t in trades]))

        # Equity curve y drawdown
        # FIX: usar max(peak, 1.0) como referencia mínima.
        # Si peak=0 (nunca hubo ganancia), la fórmula anterior daba
        # (0 - equity) / 1e-9 → valores astronómicos.
        equity = peak = max_dd = 0.0
        curve  = [0.0]
        for t in trades:
            equity += t.pnl_r
            curve.append(equity)
            if equity > peak:
                peak = equity
            ref = max(peak, 1.0)          # mínimo 1R de referencia
            dd  = max(0.0, (ref - equity) / ref)
            if dd > max_dd:
                max_dd = dd
        result.equity_curve = curve
        result.max_drawdown = max_dd

        # Sharpe anualizado
        returns = [t.pnl_r for t in trades]
        if len(returns) > 1:
            result.sharpe_ratio = (
                float(np.mean(returns)) / (float(np.std(returns)) + 1e-9)
            ) * np.sqrt(252)

        # Breakdowns
        result.yearly_stats  = self._yearly_stats(trades)
        result.session_stats = self._session_stats(trades)
        result.pattern_stats = self._pattern_stats(trades)
        result.best_hours    = self._best_hours(trades)
        return result

    def _yearly_stats(self, trades: list) -> dict:
        years: dict = {}
        for t in trades:
            y = str(t.year)
            if y not in years:
                years[y] = {"trades": 0, "wins": 0, "total_r": 0.0}
            years[y]["trades"]  += 1
            years[y]["total_r"] += t.pnl_r
            if t.outcome == "win":
                years[y]["wins"] += 1
        for st in years.values():
            st["wr"]      = round(st["wins"] / st["trades"] * 100, 1) if st["trades"] else 0
            st["total_r"] = round(st["total_r"], 2)
        return years

    def _session_stats(self, trades: list) -> dict:
        sessions: dict = {}
        for t in trades:
            s = t.session or "unknown"
            if s not in sessions:
                sessions[s] = {"trades": 0, "wins": 0, "total_r": 0.0}
            sessions[s]["trades"]  += 1
            sessions[s]["total_r"] += t.pnl_r
            if t.outcome == "win":
                sessions[s]["wins"] += 1
        for st in sessions.values():
            st["wr"]      = round(st["wins"] / st["trades"] * 100, 1) if st["trades"] else 0
            st["total_r"] = round(st["total_r"], 2)
        return sessions

    def _pattern_stats(self, trades: list) -> dict:
        patterns: dict = {}
        for t in trades:
            p = t.pattern or "unknown"
            if p not in patterns:
                patterns[p] = {"trades": 0, "wins": 0, "total_r": 0.0}
            patterns[p]["trades"]  += 1
            patterns[p]["total_r"] += t.pnl_r
            if t.outcome == "win":
                patterns[p]["wins"] += 1
        for st in patterns.values():
            st["wr"]      = round(st["wins"] / st["trades"] * 100, 1) if st["trades"] else 0
            st["total_r"] = round(st["total_r"], 2)
        return patterns

    def _best_hours(self, trades: list) -> list:
        hours: dict = {}
        for t in trades:
            h = t.hour
            if h not in hours:
                hours[h] = {"trades": 0, "wins": 0}
            hours[h]["trades"] += 1
            if t.outcome == "win":
                hours[h]["wins"] += 1
        ranked = sorted(
            [
                (h, st["wins"] / st["trades"] * 100)
                for h, st in hours.items()
                if st["trades"] >= 5
            ],
            key=lambda x: x[1],
            reverse=True,
        )
        return [h for h, _ in ranked[:8]]

    # ── Monte Carlo ───────────────────────────────────────────

    def monte_carlo(
        self,
        result: BacktestResult,
        simulations: int = 1000,
        trades_per_sim: int = 100,
    ) -> MonteCarloResult:
        if not result.trades:
            return MonteCarloResult(simulations, 0, 0, 0, 0, 0, 0, 0)

        returns       = [t.pnl_r for t in result.trades]
        sim_returns   = []
        sim_drawdowns = []

        for _ in range(simulations):
            sample = np.random.choice(returns, size=trades_per_sim, replace=True)
            sim_returns.append(float(np.sum(sample)))
            equity = peak = max_dd = 0.0
            for r in sample:
                equity += r
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / (abs(peak) + 1e-9)
                if dd > max_dd:
                    max_dd = dd
            sim_drawdowns.append(max_dd)

        arr = np.array(sim_returns)
        return MonteCarloResult(
            simulations=simulations,
            median_r=float(np.median(arr)),
            p5=float(np.percentile(arr, 5)),
            p25=float(np.percentile(arr, 25)),
            p75=float(np.percentile(arr, 75)),
            p95=float(np.percentile(arr, 95)),
            prob_positive=float(np.mean(arr > 0)),
            max_dd_median=float(np.median(sim_drawdowns)),
        )

    # ── Helpers internos ──────────────────────────────────────

    def _atr(self, bars: list, idx: int, period: int = 14) -> float:
        window = bars[max(0, idx - period):idx + 1]
        if len(window) < 2:
            return 0.0
        trs = [
            max(
                window[i].high - window[i].low,
                abs(window[i].high - window[i-1].close),
                abs(window[i].low  - window[i-1].close),
            )
            for i in range(1, len(window))
        ]
        return float(np.mean(trs)) if trs else 0.0

    def _slippage(self, symbol: str) -> float:
        return SLIPPAGE_PIPS.get(symbol, 0.0001) * np.random.uniform(0.5, 1.5)

    # ── Persistencia ──────────────────────────────────────────

    def save_result(self, result: BacktestResult):
        try:
            all_results = {}
            if os.path.exists(RESULTS_FILE):
                with open(RESULTS_FILE, "r") as f:
                    all_results = json.load(f)
            all_results[f"{result.symbol}_{result.timeframe}"] = result.to_dict()
            with open(RESULTS_FILE, "w") as f:
                json.dump(all_results, f, indent=2)
        except Exception as e:
            logger.error(f"Save result: {e}")

    def save_learning_insights(self, results: dict):
        """
        Genera backtest_learning.json — el archivo que carga el LearningEngine
        en Fase 2 para arrancar ya calibrado.
        """
        insights = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "symbols": {},
        }

        for sym, res in results.items():
            if res.total_trades < 10:
                continue

            best_pattern = (
                max(
                    res.pattern_stats.items(),
                    key=lambda x: x[1].get("wr", 0),
                    default=("N/A", {}),
                )[0]
                if res.pattern_stats else "N/A"
            )
            best_session = (
                max(
                    res.session_stats.items(),
                    key=lambda x: x[1].get("wr", 0),
                    default=("N/A", {}),
                )[0]
                if res.session_stats else "N/A"
            )

            insights["symbols"][sym] = {
                "win_rate":      round(res.win_rate * 100, 1),
                "profit_factor": round(res.profit_factor, 2),
                "best_pattern":  best_pattern,
                "best_session":  best_session,
                "best_hours":    res.best_hours[:5],
                "pattern_stats": res.pattern_stats,
                "session_stats": res.session_stats,
                "yearly_stats":  res.yearly_stats,
                "total_trades":  res.total_trades,
                "source":        res.source,
            }

        try:
            with open(LEARNING_FILE, "w") as f:
                json.dump(insights, f, indent=2)
            logger.info(f"✅ backtest_learning.json guardado → {LEARNING_FILE}")
            for sym, data in insights["symbols"].items():
                logger.info(
                    f"  {sym}: WR:{data['win_rate']}% | "
                    f"Patrón:{data['best_pattern']} | "
                    f"Sesión:{data['best_session']} | "
                    f"Horas:{data['best_hours']}"
                )
        except Exception as e:
            logger.error(f"Error guardando learning insights: {e}")
