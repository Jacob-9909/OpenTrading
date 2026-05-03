from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy.orm import Session

from coin_trading.config import Settings
from coin_trading.db.models import MarketCandle, Position, PositionStatus, RiskEventType, TradeSignal
from coin_trading.execution.live_bithumb import BithumbLiveExecutor
from coin_trading.db.session import SessionLocal
from coin_trading.exchange import create_exchange_client
from coin_trading.execution import create_executor
from coin_trading.indicators import IndicatorCalculator
from coin_trading.llm import create_llm
from coin_trading.market_data import MarketDataCollector
from coin_trading.news import NewsCollector
from coin_trading.risk import RiskEngine
from coin_trading.strategy.context import LLMContextBuilder
from coin_trading.strategy.service import StrategyService


@dataclass(frozen=True)
class PipelineResult:
    latest_price: float
    signal_id: int | None
    signal_status: str
    order_id: int | None
    risk_reason: str


@dataclass(frozen=True)
class DataRefreshResult:
    latest_price: float
    refreshed_timeframes: list[str]


class TradingPipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        client = create_exchange_client(settings)
        self.account_client = client
        self.market_data = MarketDataCollector(client)
        self.indicators = IndicatorCalculator()
        self.news = NewsCollector(settings.news_rss_urls)
        self.risk = RiskEngine(settings, account_client=self.account_client)
        self.executor = create_executor(settings, client)
        self.strategy = StrategyService(
            create_llm(settings),
            LLMContextBuilder(
                settings=settings,
                account_client=self.account_client,
                analysis_timeframes=settings.analysis_timeframes,
                recent_candle_limit=settings.recent_candle_limit,
            ),
        )

    def refresh_data_once(self, session: Session | None = None) -> DataRefreshResult:
        owns_session = session is None
        session = session or SessionLocal()
        try:
            candles = []
            collection_requests = self._collection_requests()
            for timeframe, limit in collection_requests.items():
                collected = self.market_data.collect_candles(
                    session,
                    symbol=self.settings.symbol,
                    timeframe=timeframe,
                    limit=limit,
                )
                if timeframe == self.settings.timeframe:
                    candles = collected
            latest_price = candles[-1].close if candles else self.market_data.get_mark_price(self.settings.symbol)
            self.news.collect(session)
            for timeframe in self._collection_timeframes():
                self.indicators.calculate_latest(
                    session,
                    symbol=self.settings.symbol,
                    timeframe=timeframe,
                    limit=self.settings.lookback_limit,
                )
            return DataRefreshResult(
                latest_price=latest_price,
                refreshed_timeframes=list(collection_requests),
            )
        finally:
            if owns_session:
                session.close()

    def decide_once(self, session: Session | None = None) -> PipelineResult:
        owns_session = session is None
        session = session or SessionLocal()
        try:
            latest_candle = self._latest_market_candle(session)
            if latest_candle is None:
                return PipelineResult(
                    latest_price=0,
                    signal_id=None,
                    signal_status="NO_DATA",
                    order_id=None,
                    risk_reason="No market candles available. Run refresh-data first.",
                )

            latest_price = latest_candle.close
            stale_reason = self._stale_data_reason(latest_candle)
            if stale_reason:
                return PipelineResult(
                    latest_price=latest_price,
                    signal_id=None,
                    signal_status="STALE_DATA",
                    order_id=None,
                    risk_reason=stale_reason,
                )

            duplicate_reason = self._duplicate_decision_reason(session)
            if duplicate_reason:
                return PipelineResult(
                    latest_price=latest_price,
                    signal_id=None,
                    signal_status="SKIPPED",
                    order_id=None,
                    risk_reason=duplicate_reason,
                )

            if isinstance(self.executor, BithumbLiveExecutor):
                self.executor.reconcile_submitted_orders(session, self.settings.symbol)
            events = self.risk.monitor_open_positions(session, latest_price, self.settings.symbol)
            if self.settings.trading_mode != "signal_only":
                self._execute_risk_exits(session, events, latest_price)
            signal = self.strategy.create_signal(
                session,
                symbol=self.settings.symbol,
                timeframe=self.settings.timeframe,
                latest_price=latest_price,
            )
            approval = self.risk.evaluate(session, signal, latest_price)
            order = None
            if self.settings.trading_mode != "signal_only":
                order = self.executor.execute(session, signal, approval, latest_price)
            return PipelineResult(
                latest_price=latest_price,
                signal_id=signal.id,
                signal_status=signal.status,
                order_id=order.id if order else None,
                risk_reason=approval.reason,
            )
        finally:
            if owns_session:
                session.close()

    def run_once(self, session: Session | None = None) -> PipelineResult:
        owns_session = session is None
        session = session or SessionLocal()
        try:
            self.refresh_data_once(session)
            return self.decide_once(session)
        finally:
            if owns_session:
                session.close()

    def serve(self) -> None:
        scheduler = BlockingScheduler(timezone=self.settings.scheduler_timezone)
        scheduler.add_job(
            self._safe_refresh,
            "interval",
            minutes=self.settings.data_refresh_interval_minutes,
            next_run_time=None,
        )
        for decision_time in self.settings.decision_times:
            hour, minute = self._parse_decision_time(decision_time)
            scheduler.add_job(self._safe_decide, "cron", hour=hour, minute=minute)
        self._safe_refresh()
        scheduler.start()

    def serve_decisions(self) -> None:
        scheduler = BlockingScheduler(timezone=self.settings.scheduler_timezone)
        scheduler.add_job(
            self._safe_decide,
            "interval",
            minutes=self.settings.decision_interval_minutes,
            next_run_time=datetime.now(ZoneInfo(self.settings.scheduler_timezone)),
        )
        scheduler.start()

    def serve_run_once(self) -> None:
        scheduler = BlockingScheduler(timezone=self.settings.scheduler_timezone)
        scheduler.add_job(
            self._run_once_with_log,
            "interval",
            minutes=self.settings.run_once_interval_minutes,
            next_run_time=datetime.now(ZoneInfo(self.settings.scheduler_timezone)),
        )
        print(
            "[serve-run-once] started "
            f"(interval={self.settings.run_once_interval_minutes}m, "
            f"timezone={self.settings.scheduler_timezone})"
        )
        scheduler.start()

    def _execute_risk_exits(self, session: Session, events: list, mark_price: float) -> None:
        for event in events:
            if event.event_type not in {RiskEventType.STOP_LOSS, RiskEventType.TAKE_PROFIT}:
                continue
            position_id = (event.payload or {}).get("position_id")
            if not position_id:
                continue
            position = session.get(Position, position_id)
            if position and position.status == PositionStatus.OPEN:
                self.executor.emergency_exit(session, position, mark_price, event.message)
                print(f"[risk] AUTO EXIT {event.event_type} {event.symbol} @ {mark_price}")

    def _collection_timeframes(self) -> list[str]:
        timeframes = [self.settings.timeframe, *self.settings.analysis_timeframes, "1d"]
        return list(dict.fromkeys(timeframes))

    def _collection_requests(self) -> dict[str, int]:
        requests = {
            timeframe: self.settings.lookback_limit
            for timeframe in self._collection_timeframes()
        }
        chart_limit = self._dashboard_chart_candle_limit()
        chart_timeframe = self.settings.dashboard_chart_timeframe
        requests[chart_timeframe] = max(requests.get(chart_timeframe, 0), chart_limit)
        return requests

    def _dashboard_chart_candle_limit(self) -> int:
        raw_limit = max(
            int(
                (self.settings.dashboard_chart_days * 24 * 60)
                / self._timeframe_minutes(self.settings.dashboard_chart_timeframe)
            ),
            1,
        )
        return min(raw_limit, 4_000)

    @staticmethod
    def _timeframe_minutes(timeframe: str) -> int:
        mapping = {
            "1m": 1,
            "3m": 3,
            "5m": 5,
            "10m": 10,
            "15m": 15,
            "30m": 30,
            "1h": 60,
            "4h": 240,
            "1d": 1440,
            "day": 1440,
            "days": 1440,
        }
        if timeframe not in mapping:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        return mapping[timeframe]

    def _latest_market_candle(self, session: Session) -> MarketCandle | None:
        return (
            session.query(MarketCandle)
            .filter_by(symbol=self.settings.symbol, timeframe=self.settings.timeframe)
            .order_by(MarketCandle.open_time.desc())
            .first()
        )

    def _stale_data_reason(self, candle: MarketCandle) -> str | None:
        freshness_time = self._as_utc(candle.close_time)
        stale_after = timedelta(minutes=self.settings.max_data_staleness_minutes)
        age = datetime.now(timezone.utc) - freshness_time
        if age <= stale_after:
            return None
        return (
            f"Latest {self.settings.timeframe} candle is stale: age {age} "
            f"> {stale_after}. Run refresh-data before deciding."
        )

    def _duplicate_decision_reason(self, session: Session) -> str | None:
        if self.settings.decision_cooldown_minutes == 0:
            return None
        cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=self.settings.decision_cooldown_minutes
        )
        existing = (
            session.query(TradeSignal)
            .filter(TradeSignal.symbol == self.settings.symbol)
            .filter(TradeSignal.created_at >= cutoff)
            .order_by(TradeSignal.created_at.desc())
            .first()
        )
        if existing is None:
            return None
        return (
            f"Decision cooldown is active for {self.settings.symbol}: "
            f"signal_id={existing.id}, cooldown={self.settings.decision_cooldown_minutes}m."
        )

    def _local_day_window_utc(self) -> tuple[datetime, datetime]:
        tz = ZoneInfo(self.settings.scheduler_timezone)
        now = datetime.now(tz)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    @staticmethod
    def _parse_decision_time(value: str) -> tuple[int, int]:
        hour, minute = value.split(":", maxsplit=1)
        return int(hour), int(minute)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _safe_refresh(self) -> None:
        try:
            self.refresh_data_once()
        except Exception as exc:
            print(f"[refresh] ERROR (continuing): {exc.__class__.__name__}: {exc}")

    def _safe_decide(self) -> None:
        try:
            self.decide_once()
        except Exception as exc:
            print(f"[decide] ERROR (continuing): {exc.__class__.__name__}: {exc}")

    def _run_once_with_log(self) -> None:
        started_at = datetime.now(ZoneInfo(self.settings.scheduler_timezone)).isoformat()
        print(f"[serve-run-once] cycle start: {started_at}")
        try:
            result = self.run_once()
            ended_at = datetime.now(ZoneInfo(self.settings.scheduler_timezone)).isoformat()
            print(
                "[serve-run-once] cycle end: "
                f"{ended_at} | signal={result.signal_id}:{result.signal_status} "
                f"order={result.order_id} risk='{result.risk_reason}'"
            )
        except Exception as exc:
            ended_at = datetime.now(ZoneInfo(self.settings.scheduler_timezone)).isoformat()
            print(f"[serve-run-once] cycle ERROR at {ended_at}: {exc.__class__.__name__}: {exc}")
