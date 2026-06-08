"""
GITHUB CHECKPOINT — Persistencia via GitHub API
Guarda checkpoints de backtest directamente en el repo.
Sobrevive redeploys de Railway sin necesidad de Volume.

Variables de entorno requeridas (Railway → Variables):
  GITHUB_TOKEN  — Personal Access Token (scope: repo)
  GITHUB_REPO   — owner/repo  (ej: matias/smc-quant-bot)
  GITHUB_BRANCH — rama donde guardar (default: main)

Los archivos se guardan en bot_data/ dentro del repo:
  bot_data/BTCUSDm_checkpoint.json
  bot_data/ETHUSDm_checkpoint.json
  ...
  bot_data/backtest_learning.json
"""

import base64
import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("GitHubCheckpoint")

try:
    import aiohttp
    AIOHTTP_OK = True
except ImportError:
    AIOHTTP_OK = False

API_BASE    = "https://api.github.com"
DATA_FOLDER = "bot_data"   # carpeta en el repo


class GitHubCheckpoint:
    """
    Guarda y carga checkpoints de backtest en el repo de GitHub.
    Si GITHUB_TOKEN o GITHUB_REPO no están configurados,
    todas las operaciones retornan None/False silenciosamente.
    """

    def __init__(self):
        self.token  = os.getenv("GITHUB_TOKEN", "")
        self.repo   = os.getenv("GITHUB_REPO",  "")
        self.branch = os.getenv("GITHUB_BRANCH", "main")
        self._session = None

        if self.token and self.repo:
            logger.info(
                f"GitHubCheckpoint iniciado | "
                f"repo: {self.repo} | branch: {self.branch} | "
                f"folder: {DATA_FOLDER}/"
            )
            self.enabled = True
        else:
            logger.warning(
                "GitHubCheckpoint: GITHUB_TOKEN o GITHUB_REPO no configurados "
                "— checkpoints desactivados (usar Railway Volume o configurar vars)"
            )
            self.enabled = False

    # ─── Session ──────────────────────────────────────────────

    async def _get_session(self) -> "aiohttp.ClientSession":
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={
                "Authorization":        f"Bearer {self.token}",
                "Accept":               "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            })
        return self._session

    # ─── GitHub API helpers ───────────────────────────────────

    def _repo_path(self, filename: str) -> str:
        return f"{DATA_FOLDER}/{filename}"

    async def _get_file(self, filename: str) -> tuple:
        """
        Descarga un archivo del repo.
        Retorna (contenido_dict, sha) o (None, None) si no existe.
        """
        url = f"{API_BASE}/repos/{self.repo}/contents/{self._repo_path(filename)}"
        try:
            sess = await self._get_session()
            async with sess.get(
                url,
                params={"ref": self.branch},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status == 404:
                    return None, None
                if r.status != 200:
                    logger.warning(f"GitHub GET {filename}: HTTP {r.status}")
                    return None, None
                data = await r.json()
                # content viene en base64 con saltos de línea
                raw = base64.b64decode(data["content"].replace("\n", ""))
                content = json.loads(raw.decode("utf-8"))
                return content, data["sha"]
        except Exception as e:
            logger.warning(f"GitHub GET {filename}: {e}")
            return None, None

    async def _put_file(
        self,
        filename: str,
        content: dict,
        sha: str = None,
        message: str = None,
    ) -> bool:
        """Crea o actualiza un archivo en el repo."""
        url = f"{API_BASE}/repos/{self.repo}/contents/{self._repo_path(filename)}"
        body = {
            "message": message or f"🤖 bot_data: {filename}",
            "content": base64.b64encode(
                json.dumps(content, indent=2, ensure_ascii=False).encode("utf-8")
            ).decode("ascii"),
            "branch": self.branch,
        }
        if sha:
            body["sha"] = sha  # necesario para actualizar un archivo existente

        try:
            sess = await self._get_session()
            async with sess.put(
                url,
                json=body,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                if r.status in [200, 201]:
                    return True
                text = await r.text()
                logger.error(f"GitHub PUT {filename}: HTTP {r.status} — {text[:300]}")
                return False
        except Exception as e:
            logger.error(f"GitHub PUT {filename}: {e}")
            return False

    # ─── API pública ──────────────────────────────────────────

    async def exists(self, symbol: str) -> bool:
        """Retorna True si el símbolo ya tiene checkpoint en GitHub."""
        if not self.enabled:
            return False
        _, sha = await self._get_file(f"{symbol}_checkpoint.json")
        return sha is not None

    async def save(self, result) -> bool:
        """
        Guarda BacktestResult como checkpoint en GitHub.
        Se llama automáticamente después de cada backtest completado.
        """
        if not self.enabled:
            return False

        filename = f"{result.symbol}_checkpoint.json"
        data = {
            "saved_at":      datetime.now(timezone.utc).isoformat(),
            "symbol":        result.symbol,
            "timeframe":     result.timeframe,
            "source":        getattr(result, "source",        ""),
            "start_date":    getattr(result, "start_date",    "N/A"),
            "end_date":      getattr(result, "end_date",      "N/A"),
            "total_trades":  result.total_trades,
            "wins":          result.wins,
            "losses":        result.losses,
            "breakevens":    getattr(result, "breakevens",    0),
            "win_rate":      result.win_rate,
            "profit_factor": result.profit_factor,
            "expectancy":    result.expectancy,
            "max_drawdown":  result.max_drawdown,
            "sharpe_ratio":  getattr(result, "sharpe_ratio",  0.0),
            "total_r":       result.total_r,
            "avg_win_r":     getattr(result, "avg_win_r",     0.0),
            "avg_loss_r":    getattr(result, "avg_loss_r",    0.0),
            "avg_rr":        getattr(result, "avg_rr",        0.0),
            "avg_bars_held": getattr(result, "avg_bars_held", 0.0),
            "avg_slippage":  getattr(result, "avg_slippage",  0.0),
            "yearly_stats":  result.yearly_stats,
            "session_stats": result.session_stats,
            "pattern_stats": result.pattern_stats,
            "best_hours":    result.best_hours,
        }

        # Obtener SHA si el archivo ya existe (necesario para actualizar)
        _, sha = await self._get_file(filename)
        ok = await self._put_file(
            filename, data, sha,
            message=f"🤖 checkpoint: {result.symbol} | WR:{result.win_rate*100:.1f}% PF:{result.profit_factor:.2f}"
        )
        if ok:
            logger.info(f"☁️  GitHub checkpoint guardado: {result.symbol} → {self.repo}/{DATA_FOLDER}/{filename}")
        return ok

    async def load(self, symbol: str):
        """
        Carga BacktestResult desde GitHub.
        Retorna BacktestResult reconstruido o None si no existe.
        """
        if not self.enabled:
            return None

        filename = f"{symbol}_checkpoint.json"
        data, sha = await self._get_file(filename)
        if data is None:
            return None

        try:
            from backtesting.simulator import BacktestResult
            result = BacktestResult(
                symbol       = data["symbol"],
                timeframe    = data["timeframe"],
                source       = data.get("source",        "github_checkpoint"),
                start_date   = data.get("start_date",    "N/A"),
                end_date     = data.get("end_date",      "N/A"),
                total_trades = data.get("total_trades",  0),
                wins         = data.get("wins",          0),
                losses       = data.get("losses",        0),
                breakevens   = data.get("breakevens",    0),
                win_rate     = data.get("win_rate",      0.0),
                profit_factor= data.get("profit_factor", 0.0),
                expectancy   = data.get("expectancy",    0.0),
                max_drawdown = data.get("max_drawdown",  0.0),
                sharpe_ratio = data.get("sharpe_ratio",  0.0),
                total_r      = data.get("total_r",       0.0),
                avg_win_r    = data.get("avg_win_r",     0.0),
                avg_loss_r   = data.get("avg_loss_r",    0.0),
                avg_rr       = data.get("avg_rr",        0.0),
                avg_bars_held= data.get("avg_bars_held", 0.0),
                avg_slippage = data.get("avg_slippage",  0.0),
                yearly_stats = data.get("yearly_stats",  {}),
                session_stats= data.get("session_stats", {}),
                pattern_stats= data.get("pattern_stats", {}),
                best_hours   = data.get("best_hours",   []),
            )
            saved_at = data.get("saved_at", "")[:10]
            logger.info(
                f"☁️  GitHub checkpoint cargado: {symbol} | "
                f"WR:{result.win_rate*100:.1f}% | PF:{result.profit_factor:.2f} | "
                f"Guardado: {saved_at}"
            )
            return result
        except Exception as e:
            logger.error(f"Error reconstruyendo BacktestResult {symbol}: {e}")
            return None

    async def save_learning_file(self, local_path: str) -> bool:
        """
        Sube backtest_learning.json al repo después de completar la Fase 1.
        La Fase 2 lo descarga al arrancar si no existe localmente.
        """
        if not self.enabled:
            return False
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                content = json.load(f)
            _, sha = await self._get_file("backtest_learning.json")
            ok = await self._put_file(
                "backtest_learning.json", content, sha,
                message="🤖 backtest_learning.json actualizado — Fase 1 completa"
            )
            if ok:
                logger.info(f"☁️  backtest_learning.json subido a GitHub → {self.repo}/{DATA_FOLDER}/")
            return ok
        except Exception as e:
            logger.error(f"Error subiendo backtest_learning.json: {e}")
            return False

    async def load_learning_file(self, local_path: str) -> bool:
        """
        Descarga backtest_learning.json desde GitHub y lo guarda localmente.
        Llamar al inicio de Fase 2 si el archivo no existe localmente.
        Retorna True si lo descargó correctamente.
        """
        if not self.enabled:
            return False
        try:
            content, sha = await self._get_file("backtest_learning.json")
            if content is None:
                logger.warning("GitHub: backtest_learning.json no encontrado")
                return False
            os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
            with open(local_path, "w", encoding="utf-8") as f:
                json.dump(content, f, indent=2)
            logger.info(f"☁️  backtest_learning.json descargado desde GitHub → {local_path}")
            return True
        except Exception as e:
            logger.error(f"Error descargando backtest_learning.json: {e}")
            return False

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
