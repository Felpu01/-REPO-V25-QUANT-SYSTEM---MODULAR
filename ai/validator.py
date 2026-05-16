"""
AI VALIDATOR — Claude Sonnet
Capa de validación contextual e inteligencia adaptativa.
NO ejecuta trades. Valida, filtra y aprende del contexto.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
import aiohttp

from quant.score import QuantResult
from config import ANTHROPIC_API_KEY

logger = logging.getLogger("AIValidator")

CLAUDE_MODEL = "claude-sonnet-4-5"
CLAUDE_URL   = "https://api.anthropic.com/v1/messages"


@dataclass
class AIValidation:
    approved: bool
    score: float        # 0-100
    confidence: str     # HIGH | MEDIUM | LOW
    reasoning: str
    warnings: list[str]
    suggestions: list[str]
    macro_context: str
    market_regime: str  # TRENDING | RANGING | VOLATILE | COMPRESSION

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "score": self.score,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "warnings": self.warnings,
            "market_regime": self.market_regime,
        }


class AdaptiveAIEngine:
    def __init__(self):
        self._api_key = ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY", "")
        self._session: aiohttp.ClientSession = None
        self._trade_history: list[dict] = []   # memoria de trades recientes
        self._regime_cache: dict[str, str] = {}
        logger.info("AdaptiveAIEngine iniciado — Claude Sonnet")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            })
        return self._session

    async def validate(self, quant: QuantResult) -> AIValidation:
        """Valida el setup con IA antes de ejecutar."""
        if not self._api_key:
            logger.warning("Sin API key de Claude — usando validación básica")
            return self._basic_validation(quant)

        prompt = self._build_prompt(quant)

        try:
            sess = await self._get_session()
            payload = {
                "model": CLAUDE_MODEL,
                "max_tokens": 800,
                "system": (
                    "Eres un analista institucional de trading cuantitativo experto en "
                    "Smart Money Concepts (SMC), price action institucional y gestión de riesgo. "
                    "Analiza setups de trading y responde SOLO en JSON válido, sin texto adicional."
                ),
                "messages": [{"role": "user", "content": prompt}],
            }
            async with sess.post(
                CLAUDE_URL, json=payload,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                if r.status != 200:
                    err = await r.text()
                    logger.error(f"Claude API error {r.status}: {err[:200]}")
                    return self._basic_validation(quant)

                data   = await r.json()
                text   = data["content"][0]["text"].strip()
                parsed = json.loads(text)
                return self._parse_response(parsed, quant)

        except asyncio.TimeoutError:
            logger.warning("Claude timeout — usando validación básica")
            return self._basic_validation(quant)
        except json.JSONDecodeError as e:
            logger.error(f"Claude JSON parse error: {e}")
            return self._basic_validation(quant)
        except Exception as e:
            logger.error(f"Claude error: {e}")
            return self._basic_validation(quant)

    def _build_prompt(self, q: QuantResult) -> str:
        history_str = ""
        if self._trade_history:
            last_5 = self._trade_history[-5:]
            history_str = f"\nÚltimos {len(last_5)} trades: " + ", ".join(
                [f"{t['symbol']} {t['direction']} → {'✅' if t.get('win') else '❌'}"
                 for t in last_5]
            )

        return f"""
Analiza este setup institucional SMC:

ACTIVO: {q.symbol}
DIRECCIÓN: {q.direction.upper()}
HORA UTC: {datetime.now(timezone.utc).strftime('%H:%M')}
SESIÓN: Mercado activo

SCORES CUANTITATIVOS:
- Score total: {q.final_score*100:.1f}%
- Score SMC: {q.smc_score*100:.1f}%
- Score momentum: {q.momentum_score*100:.1f}%
- Score volatilidad: {q.volatility_score*100:.1f}%
- Score confluencia: {q.confluence_score*100:.1f}%
- Bias HTF: {q.htf_bias}
- Patrón: {q.pattern}

SETUP:
- Entry: {q.entry}
- Stop Loss: {q.stop_loss}
- Take Profit: {q.take_profit}
- Risk/Reward: 1:{q.rr}
- ATR H1: {q.atr_h1:.4f}
{history_str}

Considera:
1. ¿El R:R de 1:{q.rr} es viable para este activo?
2. ¿El patrón {q.pattern} tiene alta probabilidad estadística?
3. ¿Hay riesgos contextuales (sesión, volatilidad, correlaciones)?
4. ¿El momentum confirma la dirección {q.direction}?

Responde SOLO con este JSON exacto:
{{
  "approved": true/false,
  "score": 0-100,
  "confidence": "HIGH/MEDIUM/LOW",
  "reasoning": "explicación concisa en una oración",
  "warnings": ["warning1", "warning2"],
  "suggestions": ["sugerencia1"],
  "market_regime": "TRENDING/RANGING/VOLATILE/COMPRESSION"
}}
"""

    def _parse_response(self, parsed: dict, q: QuantResult) -> AIValidation:
        return AIValidation(
            approved     = bool(parsed.get("approved", False)),
            score        = float(parsed.get("score", 50)),
            confidence   = str(parsed.get("confidence", "LOW")),
            reasoning    = str(parsed.get("reasoning", "")),
            warnings     = list(parsed.get("warnings", [])),
            suggestions  = list(parsed.get("suggestions", [])),
            macro_context= "",
            market_regime= str(parsed.get("market_regime", "RANGING")),
        )

    def _basic_validation(self, q: QuantResult) -> AIValidation:
        """Validación sin IA cuando Claude no está disponible."""
        approved = (
            q.final_score >= 0.85 and
            q.rr >= 2.5 and
            q.momentum_score >= 0.5 and
            q.volatility_score >= 0.4
        )
        score = q.final_score * 100
        warnings = []
        if q.rr < 3.0:
            warnings.append(f"RR {q.rr} por debajo del óptimo 1:3")
        if q.momentum_score < 0.6:
            warnings.append("Momentum débil")
        if q.session_score < 0.5:
            warnings.append("Sesión de baja liquidez")

        return AIValidation(
            approved=approved, score=score,
            confidence="MEDIUM" if approved else "LOW",
            reasoning="Validación automática sin IA",
            warnings=warnings, suggestions=[],
            macro_context="", market_regime="UNKNOWN",
        )

    def record_trade_result(self, symbol: str, direction: str, win: bool, pnl: float):
        """Memoria adaptativa: registra resultados para aprender."""
        self._trade_history.append({
            "symbol": symbol,
            "direction": direction,
            "win": win,
            "pnl": pnl,
            "time": datetime.now(timezone.utc).isoformat(),
        })
        # Mantener solo los últimos 50
        if len(self._trade_history) > 50:
            self._trade_history = self._trade_history[-50:]

    def get_win_rate(self, symbol: str = None) -> float:
        history = self._trade_history
        if symbol:
            history = [t for t in history if t["symbol"] == symbol]
        if not history:
            return 0.0
        wins = sum(1 for t in history if t.get("win"))
        return wins / len(history)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
