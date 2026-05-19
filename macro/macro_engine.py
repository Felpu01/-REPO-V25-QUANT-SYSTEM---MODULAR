"""
MACRO ENGINE — Correlaciones, DXY, VIX, Contexto Global
Analiza el contexto macro para validar o invalidar setups.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import numpy as np

logger = logging.getLogger("MacroEngine")

try:
    import aiohttp
    AIOHTTP_OK = True
except ImportError:
    AIOHTTP_OK = False

# Correlaciones históricas conocidas
CORRELATIONS = {
    "XAUUSD": {"DXY": -0.75, "VIX": +0.60, "SPX": -0.30},
    "BTCUSD": {"DXY": -0.45, "VIX": -0.40, "SPX": +0.55},
    "ETHUSD": {"DXY": -0.45, "VIX": -0.40, "SPX": +0.55},
    "EURUSD": {"DXY": -0.95, "VIX": -0.20, "SPX": +0.25},
    "NAS100": {"DXY": -0.40, "VIX": -0.85, "SPX": +0.95},
}

YAHOO_MACRO = {
    "DXY":  "DX-Y.NYB",
    "VIX":  "^VIX",
    "SPX":  "^GSPC",
    "GOLD": "GC=F",
    "TNX":  "^TNX",   # US 10Y yield
}


@dataclass
class MacroData:
    symbol: str
    price: float
    change_pct: float
    trend: str      # up | down | neutral
    level: str      # high | normal | low (para VIX)


@dataclass
class MacroAnalysis:
    timestamp: str = ""
    dxy: Optional[MacroData] = None
    vix: Optional[MacroData] = None
    spx: Optional[MacroData] = None
    tnx: Optional[MacroData] = None
    risk_sentiment: str = "neutral"   # risk_on | risk_off | neutral
    macro_bias: dict = field(default_factory=dict)
    macro_score: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self):
        return {
            "risk_sentiment": self.risk_sentiment,
            "dxy_trend": self.dxy.trend if self.dxy else "unknown",
            "vix_level": self.vix.level if self.vix else "unknown",
            "macro_bias": self.macro_bias,
            "warnings": self.warnings,
            "confidence": round(self.confidence, 1),
        }

    def get_symbol_score(self, symbol: str) -> float:
        return self.macro_score.get(symbol, 0.5)


class MacroEngine:
    def __init__(self):
        self._cache: Optional[MacroAnalysis] = None
        self._last_fetch: Optional[datetime] = None
        self._session = None
        self._cache_minutes = 30
        logger.info("MacroEngine iniciado")

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def analyze(self, symbols: list) -> MacroAnalysis:
        # Cache de 30 minutos
        if self._cache and self._last_fetch:
            age = (datetime.now(timezone.utc) - self._last_fetch).seconds / 60
            if age < self._cache_minutes:
                return self._cache

        result = MacroAnalysis(
            timestamp=datetime.now(timezone.utc).isoformat()
        )

        try:
            # Fetch datos macro
            macro_data = await self._fetch_macro_data()
            result.dxy = macro_data.get("DXY")
            result.vix = macro_data.get("VIX")
            result.spx = macro_data.get("SPX")
            result.tnx = macro_data.get("TNX")

            # Determinar sentimiento de riesgo
            result.risk_sentiment = self._risk_sentiment(result)

            # Calcular bias y score por símbolo
            for sym in symbols:
                bias, score = self._symbol_macro_bias(sym, result)
                result.macro_bias[sym]  = bias
                result.macro_score[sym] = score

            # Warnings
            result.warnings = self._generate_warnings(result)
            result.confidence = 80.0 if result.dxy else 30.0

        except Exception as e:
            logger.warning(f"MacroEngine error: {e} — usando análisis básico")
            result = self._basic_analysis(symbols)

        self._cache = result
        self._last_fetch = datetime.now(timezone.utc)
        return result

    async def _fetch_macro_data(self) -> dict:
        data = {}
        sess = await self._get_session()
        headers = {"User-Agent": "Mozilla/5.0"}

        for name, yahoo_sym in YAHOO_MACRO.items():
            try:
                url = (
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}"
                    f"?interval=1d&range=5d"
                )
                async with sess.get(url, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status != 200:
                        continue
                    raw = await r.json()
                    result = raw.get("chart", {}).get("result", [])
                    if not result:
                        continue

                    chart = result[0]
                    closes = chart.get("indicators", {}).get("quote", [{}])[0].get("close", [])
                    closes = [c for c in closes if c is not None]

                    if len(closes) < 2:
                        continue

                    current = closes[-1]
                    prev    = closes[-2]
                    change  = (current - prev) / (prev + 1e-9) * 100

                    trend = "up" if change > 0.1 else ("down" if change < -0.1 else "neutral")

                    # Nivel para VIX
                    level = "normal"
                    if name == "VIX":
                        if current > 30:   level = "high"
                        elif current < 15: level = "low"

                    data[name] = MacroData(
                        symbol=name,
                        price=round(current, 4),
                        change_pct=round(change, 3),
                        trend=trend,
                        level=level,
                    )

            except Exception as e:
                logger.debug(f"Macro fetch {name}: {e}")
                continue

        return data

    def _risk_sentiment(self, result: MacroAnalysis) -> str:
        risk_on_signals  = 0
        risk_off_signals = 0

        if result.vix:
            if result.vix.price < 18:
                risk_on_signals += 2
            elif result.vix.price > 25:
                risk_off_signals += 2
            elif result.vix.trend == "up":
                risk_off_signals += 1
            else:
                risk_on_signals += 1

        if result.spx:
            if result.spx.trend == "up":
                risk_on_signals += 2
            elif result.spx.trend == "down":
                risk_off_signals += 2

        if result.dxy:
            if result.dxy.trend == "up":
                risk_off_signals += 1
            else:
                risk_on_signals += 1

        if risk_on_signals > risk_off_signals + 1:
            return "risk_on"
        elif risk_off_signals > risk_on_signals + 1:
            return "risk_off"
        return "neutral"

    def _symbol_macro_bias(self, symbol: str, result: MacroAnalysis) -> tuple:
        corr = CORRELATIONS.get(symbol, {})
        score = 0.5
        bias_signals = []

        if result.dxy and "DXY" in corr:
            dxy_corr = corr["DXY"]
            if result.dxy.trend == "up" and dxy_corr < 0:
                score -= abs(dxy_corr) * 0.2
                bias_signals.append("DXY↑ bearish")
            elif result.dxy.trend == "down" and dxy_corr < 0:
                score += abs(dxy_corr) * 0.2
                bias_signals.append("DXY↓ bullish")

        if result.vix and "VIX" in corr:
            vix_corr = corr["VIX"]
            if result.vix.level == "high" and vix_corr > 0:
                score += vix_corr * 0.15
                bias_signals.append("VIX alto bullish")
            elif result.vix.level == "high" and vix_corr < 0:
                score -= abs(vix_corr) * 0.15
                bias_signals.append("VIX alto bearish")

        if result.spx and "SPX" in corr:
            spx_corr = corr["SPX"]
            if result.spx.trend == "up" and spx_corr > 0:
                score += spx_corr * 0.10
            elif result.spx.trend == "down" and spx_corr > 0:
                score -= spx_corr * 0.10

        score = max(0.1, min(0.9, score))

        if score > 0.6:   bias = "bullish"
        elif score < 0.4: bias = "bearish"
        else:             bias = "neutral"

        return bias, round(score, 3)

    def _generate_warnings(self, result: MacroAnalysis) -> list:
        warnings = []

        if result.vix and result.vix.price > 30:
            warnings.append(f"VIX extremo ({result.vix.price:.1f}) — volatilidad muy alta")

        if result.vix and result.vix.price > 20:
            warnings.append(f"VIX elevado ({result.vix.price:.1f}) — reducir tamaño")

        if result.dxy and abs(result.dxy.change_pct) > 0.5:
            warnings.append(f"DXY movimiento fuerte ({result.dxy.change_pct:+.2f}%)")

        if result.tnx and result.tnx.price > 4.5:
            warnings.append(f"Yields 10Y altos ({result.tnx.price:.2f}%) — presión sobre activos")

        if result.risk_sentiment == "risk_off":
            warnings.append("Sentimiento risk-off — favorecer XAUUSD y cash")

        return warnings

    def _basic_analysis(self, symbols: list) -> MacroAnalysis:
        result = MacroAnalysis(
            timestamp=datetime.now(timezone.utc).isoformat(),
            risk_sentiment="neutral",
            confidence=20.0,
        )
        for sym in symbols:
            result.macro_bias[sym]  = "neutral"
            result.macro_score[sym] = 0.5
        return result

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
