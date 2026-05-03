import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from coin_trading.config import get_settings
from coin_trading.db.models import AppState, MarketCandle, PaperOrder, Position, PositionStatus, TradeSignal
from coin_trading.db.session import SessionLocal, init_db
from coin_trading.exchange import create_exchange_client
from coin_trading.market_data import MarketDataCollector
from coin_trading.portfolio import PortfolioService


def main() -> None:
    st.set_page_config(page_title="LLM Trading Dashboard", layout="wide")

    settings = get_settings()
    client = create_exchange_client(settings)
    mode_label, mode_color = _mode_info(settings)

    st.title(f"LLM Trading Dashboard — {settings.symbol}")
    st.markdown(
        f'<span style="background:{mode_color};color:white;padding:4px 12px;'
        f'border-radius:4px;font-weight:bold;font-size:14px">{mode_label}</span>',
        unsafe_allow_html=True,
    )
    st.markdown("---")

    chart_timeframe, chart_days, sections, auto_refresh_chart, auto_refresh_seconds = _sidebar_controls(
        settings, mode_label
    )
    init_db()
    session = SessionLocal()
    try:
        latest_candle = (
            session.query(MarketCandle)
            .filter_by(symbol=settings.symbol, timeframe=settings.timeframe)
            .order_by(MarketCandle.open_time.desc())
            .first()
        )
        latest_price = latest_candle.close if latest_candle else client.get_mark_price(settings.symbol)
        portfolio = PortfolioService(settings, client).snapshot(
            session,
            symbol=settings.symbol,
            mark_price=latest_price,
        )
        currency = _currency(settings.symbol)

        # ── Row 1: account overview ───────────────────────────────────────────
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("현재 자산", _money(portfolio.equity, currency))
        c2.metric(
            "총 수익률",
            f"{portfolio.return_pct:+.2f}%",
            delta=f"{portfolio.return_pct:+.2f}%",
        )
        c3.metric("실현 손익", _money(portfolio.realized_pnl, currency))
        c4.metric("미실현 손익", _money(portfolio.unrealized_pnl, currency))
        c5.metric("초기 자산", _money(_baseline_equity(session, settings, portfolio.equity), currency))

        # ── Row 2: position detail ────────────────────────────────────────────
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("보유 수량", f"{portfolio.base_asset_quantity:.6g}")
        p2.metric("평균 매수가", _money(portfolio.avg_entry_price, currency))
        p3.metric("포지션 수익률", f"{portfolio.position_return_pct:+.2f}%")
        p4.metric("사용 가능 현금", _money(portfolio.cash_available, currency))

        # ── Chart ─────────────────────────────────────────────────────────────
        chart_limit = _chart_candle_limit(chart_days, chart_timeframe)
        raw_chart_limit = _raw_chart_candle_limit(chart_days, chart_timeframe)
        if auto_refresh_chart:
            candles = MarketDataCollector(client).collect_candles(
                session,
                symbol=settings.symbol,
                timeframe=chart_timeframe,
                limit=chart_limit,
            )
        else:
            candles = _chart_candles(session, settings.symbol, chart_timeframe, chart_limit)
        orders = session.query(PaperOrder).order_by(PaperOrder.created_at.asc()).all()

        if "차트" in sections:
            st.subheader(f"{settings.symbol} 가격 차트 ({chart_timeframe}, {chart_days}일)")
            if raw_chart_limit > chart_limit:
                st.caption(
                    f"요청 {raw_chart_limit:,}개 캔들 → 최신 {chart_limit:,}개만 표시"
                )
            if candles:
                candle_df = pd.DataFrame(
                    [
                        {
                            "time": c.open_time,
                            "open": c.open,
                            "high": c.high,
                            "low": c.low,
                            "close": c.close,
                        }
                        for c in candles
                    ]
                )
                fig = go.Figure(
                    data=[
                        go.Candlestick(
                            x=candle_df["time"],
                            open=candle_df["open"],
                            high=candle_df["high"],
                            low=candle_df["low"],
                            close=candle_df["close"],
                            name=settings.symbol,
                        )
                    ]
                )
                candle_duration = pd.Timedelta(minutes=_timeframe_minutes(chart_timeframe))
                order_df = _orders_in_range(orders, candle_df["time"].min(), candle_df["time"].max() + candle_duration)
                if not order_df.empty:
                    buy_df = order_df[order_df["side"] == "BUY"]
                    sell_df = order_df[order_df["side"] == "SELL"]
                    if not buy_df.empty:
                        fig.add_trace(
                            go.Scatter(
                                x=buy_df["time"],
                                y=buy_df["price"],
                                mode="markers",
                                marker={"size": 12, "color": "#26a69a", "symbol": "triangle-up"},
                                name="매수",
                            )
                        )
                    if not sell_df.empty:
                        fig.add_trace(
                            go.Scatter(
                                x=sell_df["time"],
                                y=sell_df["price"],
                                mode="markers",
                                marker={"size": 12, "color": "#ef5350", "symbol": "triangle-down"},
                                name="매도",
                            )
                        )
                fig.update_layout(height=620, xaxis_rangeslider_visible=False)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("차트 캔들 없음. 자동 수집을 켜거나 `refresh-data`를 실행하세요.")

        # ── Tables ────────────────────────────────────────────────────────────
        available_sections = [s for s in ["보유 포지션", "매매 기록", "신호", "주문"] if s in sections]
        if available_sections:
            tabs = st.tabs(available_sections)
            for tab, section in zip(tabs, available_sections, strict=True):
                with tab:
                    if section == "보유 포지션":
                        open_positions = (
                            session.query(Position)
                            .filter_by(status=PositionStatus.OPEN)
                            .order_by(Position.opened_at.desc())
                            .all()
                        )
                        if open_positions:
                            st.dataframe(_open_position_rows(open_positions, currency))
                        else:
                            st.info("현재 보유 포지션 없음")

                    elif section == "매매 기록":
                        closed_positions = (
                            session.query(Position)
                            .filter_by(status=PositionStatus.CLOSED)
                            .order_by(Position.closed_at.desc())
                            .all()
                        )
                        if closed_positions:
                            _render_trade_history(closed_positions, currency)
                        else:
                            st.info("완료된 매매 기록 없음")

                    elif section == "신호":
                        signals = (
                            session.query(TradeSignal)
                            .order_by(TradeSignal.created_at.desc())
                            .all()
                        )
                        st.dataframe(_rows(signals))

                    elif section == "주문":
                        all_orders = (
                            session.query(PaperOrder)
                            .order_by(PaperOrder.created_at.desc())
                            .all()
                        )
                        st.dataframe(_rows(all_orders))
    finally:
        session.close()
    _auto_refresh(auto_refresh_seconds)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mode_info(settings) -> tuple[str, str]:
    if settings.trading_mode == "live":
        return "실제 코인 [LIVE]", "#c0392b"
    if settings.exchange == "yfinance":
        return "모의 주식", "#2980b9"
    return "모의 코인", "#27ae60"


def _currency(symbol: str) -> str:
    if "-" in symbol:
        return symbol.split("-", maxsplit=1)[0]
    if symbol.endswith(".KS") or symbol.endswith(".KQ"):
        return "KRW"
    return "USD"


def _money(value: float, currency: str) -> str:
    if currency == "KRW":
        return f"{value:,.0f} KRW"
    return f"{value:,.2f} {currency}"


def _baseline_equity(session, settings, current_equity: float) -> float:
    """exchange 모드는 DB의 baseline을, paper 모드는 settings.initial_equity를 반환."""
    if settings.portfolio_source == "exchange":
        stored = AppState.get(session, f"baseline_equity:{settings.symbol}")
        return float(stored) if stored else current_equity
    return settings.initial_equity or current_equity


def _sidebar_controls(settings, mode_label: str):
    st.sidebar.header("대시보드 설정")
    st.sidebar.markdown(
        f'<span style="font-size:12px;color:gray">모드: {mode_label}</span>',
        unsafe_allow_html=True,
    )
    timeframe_options = ["1m", "3m", "5m", "10m", "15m", "30m", "1h", "4h"]
    default_index = (
        timeframe_options.index(settings.dashboard_chart_timeframe)
        if settings.dashboard_chart_timeframe in timeframe_options
        else timeframe_options.index("10m")
    )
    chart_timeframe = st.sidebar.selectbox("차트 타임프레임", timeframe_options, index=default_index)
    chart_days = st.sidebar.number_input("차트 기간 (일)", min_value=1, max_value=90, value=settings.dashboard_chart_days, step=1)
    sections = st.sidebar.multiselect(
        "표시 섹션",
        ["차트", "보유 포지션", "매매 기록", "신호", "주문"],
        default=["차트", "보유 포지션", "매매 기록", "신호", "주문"],
    )
    auto_refresh_chart = st.sidebar.checkbox("로드 시 차트 캔들 새로고침", value=True)
    auto_refresh_enabled = st.sidebar.checkbox("페이지 자동 새로고침", value=False)
    auto_refresh_seconds = st.sidebar.number_input(
        "자동 새로고침 간격 (초)",
        min_value=10,
        max_value=3600,
        value=60,
        step=10,
        disabled=not auto_refresh_enabled,
    )
    return chart_timeframe, int(chart_days), sections, auto_refresh_chart, int(
        auto_refresh_seconds if auto_refresh_enabled else 0
    )


def _render_trade_history(positions: list[Position], currency: str) -> None:
    rows = []
    total_pnl = 0.0
    wins = 0
    for pos in positions:
        pnl = pos.realized_pnl or 0
        total_pnl += pnl
        pnl_pct = (pnl / (pos.entry_price * pos.quantity) * 100) if pos.entry_price and pos.quantity else 0
        if pnl > 0:
            wins += 1
        rows.append({
            "종목": pos.symbol,
            "방향": pos.side.value,
            "수량": f"{pos.quantity:.6g}",
            "매수가": _money(pos.entry_price, currency),
            "매도가": _money(pos.mark_price, currency),
            "실현 손익": _money(pnl, currency),
            "수익률": f"{pnl_pct:+.2f}%",
            "매수 시각": pos.opened_at.strftime("%Y-%m-%d %H:%M") if pos.opened_at else "",
            "매도 시각": pos.closed_at.strftime("%Y-%m-%d %H:%M") if pos.closed_at else "",
        })

    total = len(positions)
    s1, s2, s3 = st.columns(3)
    s1.metric("총 실현 손익", _money(total_pnl, currency))
    s2.metric("승률", f"{wins/total*100:.1f}%" if total > 0 else "—", f"{wins}승 {total-wins}패")
    s3.metric("총 매매 횟수", total)
    st.dataframe(pd.DataFrame(rows), use_container_width=True)


def _open_position_rows(positions: list[Position], currency: str) -> pd.DataFrame:
    rows = []
    for pos in positions:
        cost = pos.entry_price * pos.quantity if pos.entry_price and pos.quantity else 0
        unrealized_pct = (
            (pos.mark_price / pos.entry_price - 1) * 100
            if pos.entry_price and pos.mark_price and pos.side.value in {"SPOT", "LONG"}
            else 0
        )
        rows.append({
            "종목": pos.symbol,
            "방향": pos.side.value,
            "수량": f"{pos.quantity:.6g}",
            "평균 매수가": _money(pos.entry_price, currency),
            "현재가": _money(pos.mark_price, currency),
            "미실현 손익": _money(pos.unrealized_pnl or 0, currency),
            "수익률": f"{unrealized_pct:+.2f}%",
            "투자 금액": _money(cost, currency),
            "손절가": _money(pos.stop_loss, currency) if pos.stop_loss else "—",
            "익절가": _money(pos.take_profit, currency) if pos.take_profit else "—",
            "매수 시각": pos.opened_at.strftime("%Y-%m-%d %H:%M") if pos.opened_at else "",
        })
    return pd.DataFrame(rows)


def _chart_candles(session, symbol: str, timeframe: str, limit: int) -> list[MarketCandle]:
    candles = (
        session.query(MarketCandle)
        .filter_by(symbol=symbol, timeframe=timeframe)
        .order_by(MarketCandle.open_time.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(candles))


def _raw_chart_candle_limit(days: int, timeframe: str) -> int:
    return max(int((days * 24 * 60) / _timeframe_minutes(timeframe)), 1)


def _chart_candle_limit(days: int, timeframe: str) -> int:
    return min(_raw_chart_candle_limit(days, timeframe), 4_000)


def _timeframe_minutes(timeframe: str) -> int:
    mapping = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
    if timeframe not in mapping:
        raise ValueError(f"Unsupported dashboard timeframe: {timeframe}")
    return mapping[timeframe]


def _orders_in_range(orders: list[PaperOrder], start, end) -> pd.DataFrame:
    start_ts = _as_utc_timestamp(start)
    end_ts = _as_utc_timestamp(end)
    rows = [
        {"time": order.created_at, "price": order.price, "side": order.side.value}
        for order in orders
        if order.price and start_ts <= _as_utc_timestamp(order.created_at) <= end_ts
    ]
    return pd.DataFrame(rows)


def _as_utc_timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _auto_refresh(seconds: int) -> None:
    if seconds <= 0:
        return
    components.html(
        f"""<script>
            setTimeout(function() {{ window.parent.location.reload(); }}, {seconds * 1000});
        </script>""",
        height=0,
    )


def _rows(models: list[object]) -> pd.DataFrame:
    rows = [
        {col.name: getattr(model, col.name) for col in model.__table__.columns}  # type: ignore[attr-defined]
        for model in models
    ]
    return pd.DataFrame(rows)


if __name__ == "__main__":
    main()
