"""
NEWS ENGINE — Calendario Económico y Eventos Macro
Detecta noticias de alto impacto y pausa el bot
durante ventanas de alta volatilidad.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import aiohttp

logger = logging.getLogger("NewsEngine")

HIGH_IMPACT_EVENTS = [
    "NFP", "Non-Farm", "FOMC", "Fed Rate", "CPI", "GDP",
    "Unemployment", "Retail Sales", "ISM", "PMI", "PPI",
    "Jackson Hole", "Fed Chair", "ECB Rate", "BOE Rate",
    "BOJ Rate", "Interest Rate",
]

SYMBOL_CURRENCIES = {
    "EURUSD": ["EUR", "USD"],
    "XAUUSD": ["USD"],
    "BTCUSD": ["USD"],
    "ETHUSD": ["USD"],
    "NAS100": ["USD"],
}


@dataclass
class NewsEvent:
    title: str
    currency: str
    impact: str
    datetime_utc: datetime
    actual: str = ""
    forecast: str = ""
    previous: str = ""

    @property
    def minutes_until(self) -> float:
        delta = self.datetime_utc - datetime.now(timezone.utc)
        return delta.total_seconds() / 60

    @property
    def is_imminent(self):
        return -5 <= self.minutes_until <= 30

    @property
    def is_recent(self):
        return -15 <= self.minutes_until <= 0


@dataclass
class NewsAnalysis:
    events: list = field(default_factory=list)
    high_impact_imminent: bool = False
    high_impact_recent: bool = False
    should_pause: bool = False
    affected_symbols: list = field(default_factory=list)
    next_event: Optional[NewsEvent] = None
    volatility_warning: str = "none"

    def to_dict(self):
        return {
            "events_today": len(self.events),
            "pause": self.should_pause,
            "warning": self.volatility_warning,
            "next_event": {
                "title": self.next_event.title,
                "minutes": round(self.next_event.minutes_until, 0),
                "impact": self.next_event.impact,
            } if self.next_event else None,
            "affected": self.affected_symbols,
        }


class NewsEngine:
    def __init__(self):
        self._cache: Optional[NewsAnalysis] = None
        self._last_fetch: Optional[datetime] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache_minutes = 15
        logger.info("NewsEngine iniciado")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def analyze(self, symbols: list) -> NewsAnalysis:
        if self._cache and self._last_fetch:
            age = (datetime.now(timezone.utc) - self._last_fetch).seconds / 60
            if age < self._cache_minutes:
                return self._cache

        result = NewsAnalysis()
        try:
            events = await self._fetch_events()
            result.events = events
        except Exception as e:
            logger.warning(f"NewsEngine fetch error: {e}")
            result = self._session_based_analysis(symbols)
            self._cache = result
            self._last_fetch = datetime.now(timezone.utc)
            return result

        high_events = [e for e in events if e.impact == "HIGH"]
        relevant = self._filter_by_symbols(high_events, symbols)
        imminent = [e for e in relevant if e.is_imminent]
        recent   = [e for e in relevant if e.is_recent]

        result.high_impact_imminent = bool(imminent)
        result.high_impact_recent   = bool(recent)
        result.should_pause = bool(imminent) or bool(recent)
        result.affected_symbols = self._get_affected_symbols(imminent + recent, symbols)

        future = [e for e in relevant if e.minutes_until > 0]
        if future:
            result.next_event = min(future, key=lambda x: x.minutes_until)

        if imminent:
            result.volatility_warning = "extreme"
        elif recent:
            result.volatility_warning = "high"
        elif any(0 < e.minutes_until <= 60 for e in relevant):
            result.volatility_warning = "medium"

        self._cache = result
        self._last_fetch = datetime.now(timezone.utc)
        return result

    async def _fetch_events(self) -> list:
        events = []
        try:
            sess = await self._get_session()
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    for item in data:
                        try:
                            dt_str = item.get("date","") + " " + item.get("time","")
                            try:
                                dt = datetime.strptime(dt_str.strip(), "%m-%d-%Y %I:%M%p")
                                dt = dt.replace(tzinfo=timezone.utc)
                            except Exception:
                                continue
                            impact = item.get("impact","").upper()
                            if impact not in ["HIGH","MEDIUM","LOW"]:
                                impact = "LOW"
                            events.append(NewsEvent(
                                title=item.get("title",""),
                                currency=item.get("country","").upper(),
                                impact=impact,
                                datetime_utc=dt,
                                actual=str(item.get("actual","")),
                                forecast=str(item.get("forecast","")),
                                previous=str(item.get("previous","")),
                            ))
                        except Exception:
                            continue
        except Exception as e:
            logger.warning(f"ForexFactory fetch failed: {e}")
        return events

    def _filter_by_symbols(self, events, symbols):
        relevant_currencies = set()
        for sym in symbols:
            relevant_currencies.update(SYMBOL_CURRENCIES.get(sym, []))
        return [e for e in events if e.currency in relevant_currencies or e.currency == "USD"]

    def _get_affected_symbols(self, events, symbols):
        affected = set()
        for event in events:
            for sym in symbols:
                if event.currency in SYMBOL_CURRENCIES.get(sym, []):
                    affected.add(sym)
            if event.currency == "USD":
                for sym in symbols:
                    affected.add(sym)
        return list(affected)

    def _session_based_analysis(self, symbols):
        result = NewsAnalysis()
        now = datetime.now(timezone.utc)
        hour = now.hour
        weekday = now.weekday()
        is_first_friday = weekday == 4 and now.day <= 7
        if is_first_friday and 12 <= hour <= 14:
            result.volatility_warning = "extreme"
            result.should_pause = True
            result.affected_symbols = symbols
            return result
        for start, end in [(8,9),(12,14),(20,22)]:
            if start <= hour < end:
                result.volatility_warning = "medium"
                break
        return result

    async def is_safe_to_trade(self, symbol):
        analysis = await self.analyze([symbol])
        if analysis.should_pause:
            return False, f"Noticia alto impacto: {analysis.volatility_warning}"
        if analysis.volatility_warning == "medium":
            return True, "precaucion — evento proximo"
        return True, "OK"

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
