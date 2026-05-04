import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy.orm import Session

from coin_trading.config import Settings
from coin_trading.db.models import MarketCandle, Position, PositionStatus, RiskEventType, TradeSignal
from coin_trading.market.exchange.bithumb_ws import BithumbCandleStreamer, BithumbTickerMonitor
from coin_trading.trade.execution.live_bithumb import BithumbLiveExecutor
from coin_trading.db.session import SessionLocal
from coin_trading.market.exchange import create_exchange_client
from coin_trading.trade.execution import create_executor
from coin_trading.market import IndicatorCalculator
from coin_trading.market.indicators import timeframe_minutes
from coin_trading.agent import create_agent_llm, create_llm
from coin_trading.market import MarketDataCollector
from coin_trading.market import NewsCollector
from coin_trading.trade import RiskEngine
from coin_trading.agent.context import LLMContextBuilder
from coin_trading.agent.service import StrategyService


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
        self._ws_active = False
        client = create_exchange_client(settings)
        self.account_client = client
        self.market_data = MarketDataCollector(client, settings)
        self.indicators = IndicatorCalculator()
        self.news = NewsCollector(settings.news_rss_urls)
        self.risk = RiskEngine(settings, account_client=self.account_client)
        self.executor = create_executor(settings, client)
        self.strategy = StrategyService(
            llm=create_llm(settings),
            context_builder=LLMContextBuilder(
                settings=settings,
                account_client=self.account_client,
                analysis_timeframes=settings.analysis_timeframes,
                recent_candle_limit=settings.recent_candle_limit,
            ),
            analyst_llm=create_agent_llm(
                settings, settings.analyst_llm_provider, settings.analyst_llm_model
            ),
            researcher_llm=create_agent_llm(
                settings, settings.researcher_llm_provider, settings.researcher_llm_model
            ),
        )

    def refresh_data_once(self, session: Session | None = None) -> DataRefreshResult:
        collection_requests = self._collection_requests()
        candles_by_tf: dict[str, list] = {}

        futures: dict = {}
        with ThreadPoolExecutor() as pool:
            futures["news"] = pool.submit(self._fetch_news_parallel)
            for timeframe, limit in collection_requests.items():
                futures[timeframe] = pool.submit(
                    self._fetch_candles_parallel, timeframe, limit
                )
            for key, future in futures.items():
                if key == "news":
                    future.result()
                else:
                    candles_by_tf[key] = future.result()

        candles = candles_by_tf.get(self.settings.timeframe, [])
        latest_price = (
            candles[-1].close if candles else self.market_data.get_mark_price(self.settings.symbol)
        )

        owns_session = session is None
        session = session or SessionLocal()
        try:
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

    def _fetch_candles_parallel(self, timeframe: str, limit: int) -> list:
        with SessionLocal() as s:
            return self.market_data.collect_candles(
                s, symbol=self.settings.symbol, timeframe=timeframe, limit=limit
            )

    def _fetch_news_parallel(self) -> None:
        with SessionLocal() as s:
            self.news.collect(s)

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
            snapshot_price = latest_price
            signal = self.strategy.create_signal(
                session,
                symbol=self.settings.symbol,
                timeframe=self.settings.timeframe,
                latest_price=latest_price,
            )
            if self._ws_active:
                current_price = self.market_data.get_mark_price(self.settings.symbol)
                drift = abs(current_price - snapshot_price) / snapshot_price
                threshold = self.settings.price_consistency_threshold_pct / 100
                if drift > threshold:
                    logger.warning(
                        "[decide] PRICE_DRIFTED: snapshot=%s current=%s drift=%.2f%% > threshold=%.2f%%",
                        snapshot_price, current_price, drift * 100, threshold * 100,
                    )
                    return PipelineResult(
                        latest_price=current_price,
                        signal_id=signal.id,
                        signal_status="PRICE_DRIFTED",
                        order_id=None,
                        risk_reason=f"price drifted {drift:.2%} during LLM decision",
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

    def serve_all(self) -> None:
        """Decision scheduler + WebSocket SL/TP monitor + candle streamer in one process."""
        self._ws_active = True
        monitor = BithumbTickerMonitor(
            symbol=self.settings.symbol,
            on_price=self._on_monitor_price,
        )
        monitor.start()

        streamer_timeframes = [tf for tf in self._collection_timeframes() if tf != "1d"]
        streamer = BithumbCandleStreamer(
            symbol=self.settings.symbol,
            timeframes=streamer_timeframes,
            indicators=self.indicators,
            lookback_limit=self.settings.lookback_limit,
        )
        streamer.start()

        logger.info(
            "[serve-all] started (interval=%dm, ws-monitor=active, ws-streamer=%s, timezone=%s)",
            self.settings.run_once_interval_minutes,
            streamer_timeframes,
            self.settings.scheduler_timezone,
        )
        scheduler = BlockingScheduler(timezone=self.settings.scheduler_timezone)
        scheduler.add_job(
            self._run_once_with_log,
            "interval",
            minutes=self.settings.run_once_interval_minutes,
            next_run_time=datetime.now(ZoneInfo(self.settings.scheduler_timezone)),
        )
        try:
            scheduler.start()
        except KeyboardInterrupt:
            logger.info("[serve-all] Stopping...")
            monitor.stop()
            streamer.stop()

    def _on_monitor_price(self, price: float) -> None:
        try:
            with SessionLocal() as session:
                events = self.risk.monitor_open_positions(session, price, self.settings.symbol)
                if events and self.settings.trading_mode != "signal_only":
                    self._execute_risk_exits(session, events, price)
        except Exception as exc:
            logger.error("[position-monitor] ERROR: %s: %s", exc.__class__.__name__, exc)

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
                logger.info("[risk] AUTO EXIT %s %s @ %s", event.event_type, event.symbol, mark_price)

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
                / timeframe_minutes(self.settings.dashboard_chart_timeframe)
            ),
            1,
        )
        return min(raw_limit, 4_000)

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
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _run_once_with_log(self) -> None:
        started_at = datetime.now(ZoneInfo(self.settings.scheduler_timezone)).isoformat()
        logger.info("[serve-all] cycle start: %s", started_at)
        try:
            result = self.run_once()
            ended_at = datetime.now(ZoneInfo(self.settings.scheduler_timezone)).isoformat()
            logger.info(
                "[serve-all] cycle end: %s | signal=%s:%s order=%s risk='%s'",
                ended_at, result.signal_id, result.signal_status,
                result.order_id, result.risk_reason,
            )
        except Exception as exc:
            ended_at = datetime.now(ZoneInfo(self.settings.scheduler_timezone)).isoformat()
            logger.error("[serve-all] cycle ERROR at %s: %s: %s", ended_at, exc.__class__.__name__, exc)
