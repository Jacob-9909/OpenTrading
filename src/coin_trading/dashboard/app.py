import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from coin_trading.config import get_settings
from coin_trading.db.models import MarketCandle, PaperOrder, Position, TradeSignal
from coin_trading.db.session import SessionLocal, init_db
from coin_trading.exchange import create_exchange_client
from coin_trading.market_data import MarketDataCollector
from coin_trading.portfolio import PortfolioService


def main() -> None:
    st.set_page_config(page_title="CoinTrading Dashboard", layout="wide")
    st.title("Bithumb LLM Trading Dashboard")

    settings = get_settings()
    client = create_exchange_client(settings)
    chart_timeframe, chart_days, sections, auto_refresh_chart, auto_refresh_seconds = _sidebar_controls(
        settings
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
        currency = settings.symbol.split("-", maxsplit=1)[0] if "-" in settings.symbol else "USD"
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Equity", _money(portfolio.equity, currency))
        col2.metric("Total Return", f"{portfolio.return_pct:.2f}%")
        col3.metric("Unrealized PnL", _money(portfolio.unrealized_pnl, currency))
        col4.metric("Open Positions", portfolio.open_positions)

        pos_col1, pos_col2, pos_col3, pos_col4 = st.columns(4)
        pos_col1.metric("Avg Entry Price", _money(portfolio.avg_entry_price, currency))
        pos_col2.metric("Position Return", f"{portfolio.position_return_pct:.2f}%")
        pos_col3.metric("Position Value", _money(portfolio.open_position_value, currency))
        pos_col4.metric("Cash Available", _money(portfolio.cash_available, currency))

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

        if "Chart" in sections:
            st.subheader(f"{settings.symbol} Chart ({chart_timeframe}, {chart_days}d)")
            if raw_chart_limit > chart_limit:
                st.caption(
                    f"Requested {raw_chart_limit:,} candles, showing latest {chart_limit:,} "
                    "to keep dashboard loading responsive."
                )
            if candles:
                candle_df = pd.DataFrame(
                    [
                        {
                            "time": candle.open_time,
                            "open": candle.open,
                            "high": candle.high,
                            "low": candle.low,
                            "close": candle.close,
                        }
                        for candle in candles
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
                order_df = _orders_in_range(orders, candle_df["time"].min(), candle_df["time"].max())
                if not order_df.empty:
                    fig.add_trace(
                        go.Scatter(
                            x=order_df["time"],
                            y=order_df["price"],
                            mode="markers",
                            marker={"size": 10},
                            text=order_df["side"],
                            name="Orders",
                        )
                    )
                fig.update_layout(height=620, xaxis_rangeslider_visible=False)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No chart candles yet. Enable chart auto-refresh or run `refresh-data`.")

        table_sections = [section for section in ["Positions", "Signals", "Orders"] if section in sections]
        if table_sections:
            tabs = st.tabs(table_sections)
            for tab, section in zip(tabs, table_sections, strict=True):
                with tab:
                    if section == "Positions":
                        positions = session.query(Position).order_by(Position.opened_at.desc()).all()
                        st.dataframe(_position_rows(positions))
                    elif section == "Signals":
                        st.dataframe(
                            _rows(session.query(TradeSignal).order_by(TradeSignal.created_at.desc()).all())
                        )
                    elif section == "Orders":
                        st.dataframe(
                            _rows(session.query(PaperOrder).order_by(PaperOrder.created_at.desc()).all())
                        )
    finally:
        session.close()
    _auto_refresh(auto_refresh_seconds)


def _sidebar_controls(settings):
    st.sidebar.header("Dashboard Filters")
    timeframe_options = ["1m", "3m", "5m", "10m", "15m", "30m", "1h", "4h"]
    default_index = (
        timeframe_options.index(settings.dashboard_chart_timeframe)
        if settings.dashboard_chart_timeframe in timeframe_options
        else timeframe_options.index("10m")
    )
    chart_timeframe = st.sidebar.selectbox(
        "Chart Timeframe",
        timeframe_options,
        index=default_index,
        help="Dashboard chart timeframe is collected separately from trading TIMEFRAME.",
    )
    chart_days = st.sidebar.number_input(
        "Chart Days",
        min_value=1,
        max_value=90,
        value=settings.dashboard_chart_days,
        step=1,
    )
    sections = st.sidebar.multiselect(
        "Visible Sections",
        ["Chart", "Positions", "Signals", "Orders"],
        default=["Chart", "Positions", "Signals", "Orders"],
    )
    auto_refresh_chart = st.sidebar.checkbox("Refresh chart candles on load", value=True)
    auto_refresh_enabled = st.sidebar.checkbox("Auto refresh page", value=False)
    auto_refresh_seconds = st.sidebar.number_input(
        "Auto refresh interval (seconds)",
        min_value=10,
        max_value=3600,
        value=60,
        step=10,
        disabled=not auto_refresh_enabled,
    )
    return chart_timeframe, int(chart_days), sections, auto_refresh_chart, int(
        auto_refresh_seconds if auto_refresh_enabled else 0
    )


def _chart_candles(
    session,
    symbol: str,
    timeframe: str,
    limit: int,
) -> list[MarketCandle]:
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
    mapping = {
        "1m": 1,
        "3m": 3,
        "5m": 5,
        "10m": 10,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
    }
    if timeframe not in mapping:
        raise ValueError(f"Unsupported dashboard timeframe: {timeframe}")
    return mapping[timeframe]


def _orders_in_range(orders: list[PaperOrder], start, end) -> pd.DataFrame:
    start_ts = _as_utc_timestamp(start)
    end_ts = _as_utc_timestamp(end)
    rows = [
        {"time": order.created_at, "price": order.price, "side": order.side.value}
        for order in orders
        if start_ts <= _as_utc_timestamp(order.created_at) <= end_ts
    ]
    return pd.DataFrame(rows)


def _as_utc_timestamp(value) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _auto_refresh(seconds: int) -> None:
    if seconds <= 0:
        return
    milliseconds = seconds * 1000
    components.html(
        f"""
        <script>
            const ms = {milliseconds};
            setTimeout(function() {{
                window.parent.location.reload();
            }}, ms);
        </script>
        """,
        height=0,
    )


def _rows(models: list[object]) -> pd.DataFrame:
    rows = []
    for model in models:
        row = {
            column.name: getattr(model, column.name)
            for column in model.__table__.columns  # type: ignore[attr-defined]
        }
        rows.append(row)
    return pd.DataFrame(rows)


def _position_rows(positions: list[Position]) -> pd.DataFrame:
    rows = []
    for position in positions:
        row = {
            column.name: getattr(position, column.name)
            for column in position.__table__.columns
        }
        row["position_return_pct"] = _position_return_pct(position)
        rows.append(row)
    return pd.DataFrame(rows)


def _position_return_pct(position: Position) -> float:
    if position.entry_price <= 0:
        return 0
    if position.side.value in {"SPOT", "LONG"}:
        return (position.mark_price / position.entry_price - 1) * 100
    return (position.entry_price / position.mark_price - 1) * 100 if position.mark_price > 0 else 0


def _money(value: float, currency: str) -> str:
    return f"{value:,.0f} {currency}" if currency == "KRW" else f"{value:,.2f} {currency}"


if __name__ == "__main__":
    main()
