from typing import Any

from sqlalchemy.orm import Session

from coin_trading.config import Settings
from coin_trading.db.models import AppState, IndicatorSnapshot, MarketCandle, NewsItem, Position, PositionSide, PositionStatus
from coin_trading.trade import PortfolioService


class LLMContextBuilder:
    def __init__(
        self,
        settings: Settings,
        account_client: Any | None = None,
        analysis_timeframes: list[str] | None = None,
        recent_candle_limit: int = 60,
    ) -> None:
        self.settings = settings
        self.account_client = account_client
        self.analysis_timeframes = analysis_timeframes or ["1h", "4h", "1d"]
        self.recent_candle_limit = recent_candle_limit

    def build(
        self,
        session: Session,
        symbol: str,
        timeframe: str,
        latest_price: float,
        news_limit: int | None = None,
    ) -> dict[str, Any]:
        news_limit = news_limit if news_limit is not None else self.settings.news_context_limit
        latest_candle = (
            session.query(MarketCandle)
            .filter_by(symbol=symbol, timeframe=timeframe)
            .order_by(MarketCandle.open_time.desc())
            .first()
        )
        indicators = self._latest_indicators(session, symbol, timeframe)
        news = session.query(NewsItem).order_by(NewsItem.collected_at.desc()).limit(news_limit).all()
        snapshot = PortfolioService(self.settings, self.account_client).snapshot(
            session, symbol=symbol, mark_price=latest_price
        )

        return {
            "symbol": symbol,
            "exchange": self.settings.exchange,
            "timeframe": timeframe,
            "latest_price": latest_price,
            "latest_candle": self._candle_payload(latest_candle) if latest_candle else None,
            "recent_candles": self._recent_candles(
                session,
                symbol=symbol,
                timeframe=timeframe,
                limit=self.recent_candle_limit,
            ),
            "monthly_summary": self._market_summary(session, symbol=symbol, days=30),
            "quarter_summary": self._market_summary(session, symbol=symbol, days=90),
            "portfolio": self._portfolio_payload(session, symbol=symbol, latest_price=latest_price, snapshot=snapshot),
            "technical_indicators": indicators.values if indicators else {},
            "multi_timeframe": self._multi_timeframe_indicators(session, symbol),
            "news": [
                {
                    "title": item.title,
                    "subtitle": item.summary or None,
                    "source": item.source,
                    "categories": item.categories or [],
                    "sentiment": item.sentiment,
                    "score": item.score,
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                }
                for item in news
            ],
            "instructions": self._position_instructions(snapshot),
        }

    @staticmethod
    def summarize(context: dict) -> str:
        indicators = context.get("technical_indicators") or {}
        return (
            f"{context.get('symbol')} {context.get('timeframe')} price={context.get('latest_price')} "
            f"trend={indicators.get('trend')} rsi={indicators.get('rsi_14')} "
            f"macd={indicators.get('macd')}"
        )

    @staticmethod
    def _candle_payload(candle: MarketCandle) -> dict[str, Any]:
        return {
            "open_time": candle.open_time.isoformat(),
            "open": round(candle.open, 8),
            "high": round(candle.high, 8),
            "low": round(candle.low, 8),
            "close": round(candle.close, 8),
            "volume": round(candle.volume, 8),
        }

    def _recent_candles(
        self,
        session: Session,
        symbol: str,
        timeframe: str,
        limit: int,
    ) -> list[dict]:
        candles = (
            session.query(MarketCandle)
            .filter_by(symbol=symbol, timeframe=timeframe)
            .order_by(MarketCandle.open_time.desc())
            .limit(limit)
            .all()
        )
        return [self._candle_payload(candle) for candle in reversed(candles)]

    def _multi_timeframe_indicators(self, session: Session, symbol: str) -> dict[str, dict[str, Any]]:
        payload: dict[str, dict] = {}
        for timeframe in self.analysis_timeframes:
            indicators = self._latest_indicators(session, symbol, timeframe)
            latest_candle = (
                session.query(MarketCandle)
                .filter_by(symbol=symbol, timeframe=timeframe)
                .order_by(MarketCandle.open_time.desc())
                .first()
            )
            payload[timeframe] = {
                "latest_close": round(latest_candle.close, 8) if latest_candle else None,
                "indicators": indicators.values if indicators else {},
            }
        return payload

    @staticmethod
    def _latest_indicators(
        session: Session,
        symbol: str,
        timeframe: str,
    ) -> IndicatorSnapshot | None:
        return (
            session.query(IndicatorSnapshot)
            .filter_by(symbol=symbol, timeframe=timeframe)
            .order_by(IndicatorSnapshot.calculated_at.desc())
            .first()
        )

    def _market_summary(self, session: Session, symbol: str, days: int) -> dict:
        candles = (
            session.query(MarketCandle)
            .filter_by(symbol=symbol, timeframe="1d")
            .order_by(MarketCandle.open_time.desc())
            .limit(days)
            .all()
        )
        candles = list(reversed(candles))
        if len(candles) < 2:
            return {}

        closes = [candle.close for candle in candles]
        highs = [candle.high for candle in candles]
        lows = [candle.low for candle in candles]
        volumes = [candle.volume for candle in candles]
        returns = [
            (closes[index] / closes[index - 1]) - 1
            for index in range(1, len(closes))
            if closes[index - 1] > 0
        ]

        recent_volume = sum(volumes[-7:]) / min(7, len(volumes))
        previous_volume_window = volumes[-14:-7] or volumes[:-7]
        previous_volume = (
            sum(previous_volume_window) / len(previous_volume_window)
            if previous_volume_window
            else recent_volume
        )

        return {
            "days": days,
            "start": candles[0].open_time.date().isoformat(),
            "end": candles[-1].open_time.date().isoformat(),
            "return_pct": self._round_pct((closes[-1] / closes[0]) - 1),
            "high": round(max(highs), 8),
            "low": round(min(lows), 8),
            "volatility_pct": self._round_pct(self._std(returns)),
            "volume_change_pct": self._round_pct(
                (recent_volume / previous_volume) - 1 if previous_volume else 0
            ),
            "max_drawdown_pct": self._round_pct(self._max_drawdown(closes)),
            "support_candidates": [round(value, 8) for value in self._support_levels(lows)],
            "resistance_candidates": [round(value, 8) for value in self._resistance_levels(highs)],
            "trend": "up" if closes[-1] >= closes[0] else "down",
        }

    @staticmethod
    def _std(values: list[float]) -> float:
        if len(values) < 2:
            return 0
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
        return variance**0.5

    @staticmethod
    def _max_drawdown(closes: list[float]) -> float:
        peak = closes[0]
        max_drawdown = 0.0
        for close in closes:
            peak = max(peak, close)
            if peak > 0:
                max_drawdown = min(max_drawdown, (close / peak) - 1)
        return max_drawdown

    @staticmethod
    def _support_levels(lows: list[float]) -> list[float]:
        sorted_lows = sorted(lows)
        return [sorted_lows[0], sorted_lows[len(sorted_lows) // 4]]

    @staticmethod
    def _resistance_levels(highs: list[float]) -> list[float]:
        sorted_highs = sorted(highs)
        return [sorted_highs[(len(sorted_highs) * 3) // 4], sorted_highs[-1]]

    @staticmethod
    def _round_pct(value: float) -> float:
        return round(value * 100, 4)

    @staticmethod
    def _position_instructions(snapshot: Any) -> str:
        open_positions = snapshot.open_positions
        if not open_positions:
            return (
                "CURRENT STATE: No open position. Cash available.\n"
                "Valid actions:\n"
                "  LONG  — enter long (expect price rise). Requires stop_loss < entry_price < take_profit.\n"
                "  SHORT — enter short (expect price fall). Requires take_profit < entry_price < stop_loss.\n"
                "  HOLD  — wait for better opportunity.\n"
                "Never output LONG/SHORT without stop_loss, take_profit, entry_price, allocation_pct."
            )
        return (
            "CURRENT STATE: Open position(s) exist. You can hold multiple positions concurrently.\n"
            "  CLOSE_POSITION signal — closes a specific position. MUST provide 'position_id'.\n"
            "  LONG signal  — opens a NEW long position independently.\n"
            "  SHORT signal — opens a NEW short position independently.\n"
            "  HOLD — maintain current positions without opening or closing any.\n"
            "Return one JSON object with: action, position_id, confidence, entry_price, stop_loss, take_profit, "
            "allocation_pct, leverage, time_horizon, rationale, risk_notes."
        )

    def _portfolio_payload(self, session: Session, symbol: str, latest_price: float, snapshot: Any = None) -> dict[str, Any]:
        from datetime import datetime, timezone
        if snapshot is None:
            snapshot = PortfolioService(self.settings, self.account_client).snapshot(
                session, symbol=symbol, mark_price=latest_price
            )
        # exchange 모드: DB에서 baseline 조회 / paper 모드: settings 값 사용
        if self.settings.portfolio_source == "exchange":
            stored = AppState.get(session, f"baseline_equity:{symbol}")
            baseline = float(stored) if stored else snapshot.equity
        else:
            baseline = self.settings.initial_equity or snapshot.equity
        now = datetime.now(timezone.utc)
        open_positions = (
            session.query(Position)
            .filter_by(symbol=symbol, status=PositionStatus.OPEN)
            .all()
        )
        position_details = []
        for pos in open_positions:
            opened = pos.opened_at
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            holding_minutes = int((now - opened).total_seconds() / 60)
            entry = round(pos.entry_price, 8)
            if pos.side == PositionSide.LONG:
                unrealized_pnl_pct = round((latest_price - entry) / entry * 100, 4) if entry > 0 else 0
            else:  # SHORT
                unrealized_pnl_pct = round((entry - latest_price) / entry * 100, 4) if entry > 0 else 0
            position_details.append({
                "position_id": pos.id,
                "side": pos.side.value,
                "entry_price": entry,
                "stop_loss": round(pos.stop_loss, 8) if pos.stop_loss is not None else None,
                "take_profit": round(pos.take_profit, 8) if pos.take_profit is not None else None,
                "unrealized_pnl_pct": unrealized_pnl_pct,
                "holding_minutes": holding_minutes,
            })
        return {
            "source": snapshot.source,
            "initial_equity": round(baseline, 8),
            "current_equity": round(snapshot.equity, 8),
            "cash_available": round(snapshot.cash_available, 8),
            "open_position_value": round(snapshot.open_position_value, 8),
            "base_asset_quantity": round(snapshot.base_asset_quantity, 12),
            "quote_locked": round(snapshot.quote_locked, 8),
            "base_locked": round(snapshot.base_locked, 12),
            "realized_pnl": round(snapshot.realized_pnl, 8),
            "unrealized_pnl": round(snapshot.unrealized_pnl, 8),
            "return_pct": round(snapshot.return_pct, 4),
            "open_positions": snapshot.open_positions,
            "open_position_details": position_details,
            "max_risk_per_trade_pct": round(self.settings.risk_per_trade * 100, 4),
            "max_position_allocation_pct": round(self.settings.max_position_allocation_pct, 4),
        }
