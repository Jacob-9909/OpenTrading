import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from streamlit_autorefresh import st_autorefresh

from coin_trading.config import get_settings
from coin_trading.db.models import (
    AppState,
    LLMDecision,
    MarketCandle,
    PaperOrder,
    Position,
    PositionSide,
    PositionStatus,
    RiskEvent,
    SignalSide,
    TradeSignal,
)
from coin_trading.db.session import SessionLocal, init_db
from coin_trading.market.exchange import create_exchange_client
from coin_trading.market import MarketDataCollector
from coin_trading.market.indicators import timeframe_minutes
from coin_trading.trade import PortfolioService


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
        open_positions_query = (
            session.query(Position)
            .filter_by(symbol=settings.symbol, status=PositionStatus.OPEN)
            .all()
        )
        long_positions = [p for p in open_positions_query if p.side == PositionSide.LONG]
        short_positions = [p for p in open_positions_query if p.side == PositionSide.SHORT]
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("LONG 수량", f"{sum(p.quantity for p in long_positions):.6g}")
        p2.metric("SHORT 수량", f"{sum(p.quantity for p in short_positions):.6g}")
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
                st.caption(f"요청 {raw_chart_limit:,}개 캔들 → 최신 {chart_limit:,}개만 표시")
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
                candle_duration = pd.Timedelta(minutes=timeframe_minutes(chart_timeframe))
                order_df = _orders_in_range(orders, candle_df["time"].min(), candle_df["time"].max() + candle_duration)
                if not order_df.empty:
                    long_open_df = order_df[order_df["signal_side"] == "LONG"]
                    short_open_df = order_df[order_df["signal_side"] == "SHORT"]
                    close_df = order_df[~order_df["signal_side"].isin(["LONG", "SHORT"])]
                    if not long_open_df.empty:
                        fig.add_trace(
                            go.Scatter(
                                x=long_open_df["time"],
                                y=long_open_df["price"],
                                mode="markers",
                                marker={"size": 13, "color": "#26a69a", "symbol": "triangle-up"},
                                name="LONG 진입",
                            )
                        )
                    if not short_open_df.empty:
                        fig.add_trace(
                            go.Scatter(
                                x=short_open_df["time"],
                                y=short_open_df["price"],
                                mode="markers",
                                marker={"size": 13, "color": "#ef5350", "symbol": "triangle-down"},
                                name="SHORT 진입",
                            )
                        )
                    if not close_df.empty:
                        long_close_df = close_df[close_df["side"] == "SELL"]
                        short_close_df = close_df[close_df["side"] == "BUY"]
                        if not long_close_df.empty:
                            fig.add_trace(
                                go.Scatter(
                                    x=long_close_df["time"],
                                    y=long_close_df["price"],
                                    mode="markers",
                                    marker={"size": 13, "color": "#80cbc4", "symbol": "x", "line": {"width": 2, "color": "#26a69a"}},
                                    name="LONG 청산",
                                )
                            )
                        if not short_close_df.empty:
                            fig.add_trace(
                                go.Scatter(
                                    x=short_close_df["time"],
                                    y=short_close_df["price"],
                                    mode="markers",
                                    marker={"size": 13, "color": "#ef9a9a", "symbol": "x", "line": {"width": 2, "color": "#ef5350"}},
                                    name="SHORT 청산",
                                )
                            )
                hold_df = _hold_signals_in_range(
                    session,
                    settings.symbol,
                    candle_df,
                    candle_df["time"].min(),
                    candle_df["time"].max() + candle_duration,
                )
                if not hold_df.empty:
                    fig.add_trace(
                        go.Scatter(
                            x=hold_df["time"],
                            y=hold_df["price"],
                            mode="markers",
                            marker={"size": 10, "color": "#78909c", "symbol": "circle", "line": {"width": 1, "color": "#455a64"}},
                            name="관망 (HOLD)",
                        )
                    )
                fig.update_layout(height=620, xaxis_rangeslider_visible=False)
                st.plotly_chart(fig, width="stretch")
            else:
                st.info("차트 캔들 없음. 자동 수집을 켜거나 `refresh-data`를 실행하세요.")

        # ── Tabs ──────────────────────────────────────────────────────────────
        tab_현황, tab_매매, tab_llm = st.tabs(["📊 현황", "📋 매매 기록", "🤖 LLM 분석"])

        with tab_현황:
            open_positions = (
                session.query(Position)
                .filter_by(status=PositionStatus.OPEN)
                .order_by(Position.opened_at.desc())
                .all()
            )
            st.subheader("보유 포지션")
            if open_positions:
                st.dataframe(
                    _open_position_rows(open_positions, currency),
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.info("현재 보유 포지션 없음")

            st.subheader("최근 신호 (20건)")
            recent_signals = (
                session.query(TradeSignal)
                .order_by(TradeSignal.created_at.desc())
                .limit(20)
                .all()
            )
            if recent_signals:
                _sig_df = _signal_rows(recent_signals, currency)
                st.dataframe(
                    _sig_df.style.map(_dir_cell_style, subset=["방향"]),
                    width="stretch",
                    hide_index=True,
                )

            st.subheader("리스크 이벤트 (최근 30건)")
            risk_events = (
                session.query(RiskEvent)
                .order_by(RiskEvent.created_at.desc())
                .limit(30)
                .all()
            )
            if risk_events:
                _render_risk_events(risk_events)
            else:
                st.info("리스크 이벤트 없음")

        with tab_매매:
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

        with tab_llm:
            decisions = (
                session.query(LLMDecision)
                .order_by(LLMDecision.created_at.desc())
                .limit(100)
                .all()
            )
            if decisions:
                _render_llm_decisions(decisions)
            else:
                st.info("LLM 결정 기록 없음")

            st.subheader("전체 신호")
            all_signals = (
                session.query(TradeSignal)
                .order_by(TradeSignal.created_at.desc())
                .limit(200)
                .all()
            )
            if all_signals:
                _all_sig_df = _signal_rows(all_signals, currency)
                st.dataframe(
                    _all_sig_df.style.map(_dir_cell_style, subset=["방향"]),
                    width="stretch",
                    hide_index=True,
                )
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


def _pnl_row_style(row: pd.Series) -> list[str]:
    pnl = row.get("_pnl_raw") or 0
    if pnl > 0:
        bg = "background-color: rgba(38, 166, 154, 0.12)"
    elif pnl < 0:
        bg = "background-color: rgba(239, 83, 80, 0.12)"
    else:
        bg = ""
    return [bg] * len(row)


def _dir_cell_style(val: str) -> str:
    v = str(val)
    if v == "LONG":
        return "background-color: rgba(38, 166, 154, 0.28); font-weight: bold"
    if v == "SHORT":
        return "background-color: rgba(239, 83, 80, 0.28); font-weight: bold"
    return "color: #607d8b"


def _signal_action_style(val: str) -> str:
    v = str(val).upper()
    if "LONG" in v:
        return "background-color: rgba(38, 166, 154, 0.28); font-weight: bold"
    if "SHORT" in v:
        return "background-color: rgba(239, 83, 80, 0.28); font-weight: bold"
    if "HOLD" in v:
        return "color: #607d8b"
    return ""


def _baseline_equity(session, settings, current_equity: float) -> float:
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
    chart_days = st.sidebar.number_input(
        "차트 기간 (일)", min_value=1, max_value=90, value=settings.dashboard_chart_days, step=1
    )
    sections = st.sidebar.multiselect(
        "표시 섹션",
        ["차트", "보유 포지션", "매매 기록", "신호", "주문", "리스크 이벤트", "LLM 결정"],
        default=["차트", "보유 포지션", "매매 기록", "신호", "주문", "리스크 이벤트", "LLM 결정"],
    )
    if "auto_refresh_enabled" not in st.session_state:
        st.session_state.auto_refresh_enabled = False
    if "auto_refresh_seconds" not in st.session_state:
        st.session_state.auto_refresh_seconds = 60

    auto_refresh_chart = st.sidebar.checkbox("로드 시 차트 캔들 새로고침", value=True)
    auto_refresh_enabled = st.sidebar.checkbox(
        "페이지 자동 새로고침",
        key="auto_refresh_enabled",
    )
    auto_refresh_seconds = st.sidebar.number_input(
        "자동 새로고침 간격 (초)",
        min_value=10,
        max_value=3600,
        step=10,
        disabled=not auto_refresh_enabled,
        key="auto_refresh_seconds",
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
            "_pnl_raw": pnl,
        })

    total = len(positions)
    s1, s2, s3 = st.columns(3)
    s1.metric("총 실현 손익", _money(total_pnl, currency))
    s2.metric("승률", f"{wins / total * 100:.1f}%" if total > 0 else "—", f"{wins}승 {total - wins}패")
    s3.metric("총 매매 횟수", total)
    df = pd.DataFrame(rows)
    st.dataframe(
        df.style
        .apply(_pnl_row_style, axis=1)
        .map(_dir_cell_style, subset=["방향"])
        .hide(["_pnl_raw"], axis="columns"),
        width="stretch",
        hide_index=True,
    )


def _render_risk_events(events: list[RiskEvent]) -> None:
    type_counts: dict[str, int] = {}
    for e in events:
        type_counts[e.event_type.value] = type_counts.get(e.event_type.value, 0) + 1

    cols = st.columns(len(type_counts) or 1)
    for col, (etype, count) in zip(cols, type_counts.items()):
        col.metric(etype, count)

    rows = [
        {
            "시각": e.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "이벤트": e.event_type.value,
            "종목": e.symbol,
            "메시지": e.message,
        }
        for e in events
    ]
    st.dataframe(
        pd.DataFrame(rows),
        width="stretch",
        hide_index=True,
        column_config={
            "시각": st.column_config.TextColumn(width="medium"),
            "이벤트": st.column_config.TextColumn(width="medium"),
            "종목": st.column_config.TextColumn(width="small"),
            "메시지": st.column_config.TextColumn(width="large"),
        },
    )


def _render_llm_decisions(decisions: list[LLMDecision]) -> None:
    total_input = sum((d.token_usage or {}).get("input_tokens", 0) for d in decisions)
    total_output = sum((d.token_usage or {}).get("output_tokens", 0) for d in decisions)

    m1, m2, m3 = st.columns(3)
    m1.metric("총 결정 수", len(decisions))
    m2.metric("총 입력 토큰", f"{total_input:,}")
    m3.metric("총 출력 토큰", f"{total_output:,}")

    rows = []
    for d in decisions:
        usage = d.token_usage or {}
        signal_side = ""
        if isinstance(d.response, dict):
            signal_side = d.response.get("side", d.response.get("action", ""))
        rows.append({
            "시각": d.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "공급자": d.provider,
            "모델": d.model,
            "신호": signal_side if signal_side else "—",
            "입력 토큰": usage.get("input_tokens", "—"),
            "출력 토큰": usage.get("output_tokens", "—"),
            "프롬프트 요약": d.prompt_summary,
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df.style.map(_signal_action_style, subset=["신호"]),
        width="stretch",
        hide_index=True,
    )


def _open_position_rows(positions: list[Position], currency: str):
    rows = []
    for pos in positions:
        cost = pos.entry_price * pos.quantity if pos.entry_price and pos.quantity else 0
        if pos.entry_price and pos.mark_price:
            if pos.side == PositionSide.LONG:
                unrealized_pct = (pos.mark_price / pos.entry_price - 1) * 100
            else:
                unrealized_pct = (pos.entry_price / pos.mark_price - 1) * 100
        else:
            unrealized_pct = 0
        rows.append({
            "종목": pos.symbol,
            "방향": pos.side.value,
            "수량": f"{pos.quantity:.6g}",
            "평균 매수가": _money(pos.entry_price, currency),
            "현재가": _money(pos.mark_price, currency),
            "미실현 손익": _money(pos.unrealized_pnl or 0, currency),
            "수익률 %": f"{unrealized_pct:+.2f}%",
            "투자 금액": _money(cost, currency),
            "손절가": _money(pos.stop_loss, currency) if pos.stop_loss else "—",
            "익절가": _money(pos.take_profit, currency) if pos.take_profit else "—",
            "매수 시각": pos.opened_at.strftime("%Y-%m-%d %H:%M") if pos.opened_at else "",
            "_pnl_raw": unrealized_pct,
        })
    df = pd.DataFrame(rows)
    return (
        df.style
        .apply(_pnl_row_style, axis=1)
        .map(_dir_cell_style, subset=["방향"])
        .hide(["_pnl_raw"], axis="columns")
    )


def _signal_rows(signals: list[TradeSignal], currency: str) -> pd.DataFrame:
    rows = []
    for s in signals:
        rows.append({
            "시각": s.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "방향": s.side.value,
            "신뢰도": s.confidence,
            "진입가": _money(s.entry_price, currency) if s.entry_price else "—",
            "손절": _money(s.stop_loss, currency) if s.stop_loss else "—",
            "익절": _money(s.take_profit, currency) if s.take_profit else "—",
            "상태": s.status,
            "근거": s.rationale,
        })
    return pd.DataFrame(rows)


def _order_rows(orders: list[PaperOrder], currency: str) -> pd.DataFrame:
    rows = []
    for o in orders:
        rows.append({
            "시각": o.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "방향": o.side.value,
            "수량": f"{o.quantity:.6g}",
            "가격": _money(o.price, currency),
            "상태": o.status.value,
            "사유": o.reason or "—",
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
    return max(int((days * 24 * 60) / timeframe_minutes(timeframe)), 1)


def _chart_candle_limit(days: int, timeframe: str) -> int:
    return min(_raw_chart_candle_limit(days, timeframe), 4_000)


def _orders_in_range(orders: list[PaperOrder], start, end) -> pd.DataFrame:
    start_ts = _as_utc_timestamp(start)
    end_ts = _as_utc_timestamp(end)
    rows = []
    for order in orders:
        if not order.price or not (start_ts <= _as_utc_timestamp(order.created_at) <= end_ts):
            continue
        if order.signal is not None:
            signal_side = order.signal.side.value
        else:
            signal_side = "CLOSE_POSITION"
        rows.append({"time": order.created_at, "price": order.price, "side": order.side.value, "signal_side": signal_side})
    return pd.DataFrame(rows)


def _hold_signals_in_range(
    session,
    symbol: str,
    candle_df: pd.DataFrame,
    start,
    end,
) -> pd.DataFrame:
    """HOLD 신호는 주문이 없어 차트에 안 나오므로, 캔들 구간 안의 HOLD를 마커로 표시."""
    if candle_df.empty:
        return pd.DataFrame(columns=["time", "price"])
    start_ts = _as_utc_timestamp(start)
    end_ts = _as_utc_timestamp(end)
    candle_times = candle_df["time"].map(_as_utc_timestamp)

    signals = (
        session.query(TradeSignal)
        .filter(TradeSignal.symbol == symbol, TradeSignal.side == SignalSide.HOLD)
        .order_by(TradeSignal.created_at.asc())
        .all()
    )
    rows: list[dict] = []
    for sig in signals:
        ts = _as_utc_timestamp(sig.created_at)
        if not (start_ts <= ts <= end_ts):
            continue
        if sig.entry_price is not None:
            price = float(sig.entry_price)
        else:
            mask = candle_times <= ts
            if not mask.any():
                continue
            price = float(candle_df.loc[mask, "close"].iloc[-1])
        rows.append({"time": sig.created_at, "price": price})
    return pd.DataFrame(rows)


def _as_utc_timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _auto_refresh(seconds: int) -> None:
    if seconds <= 0:
        return
    st_autorefresh(interval=seconds * 1000, key="dashboard_autorefresh")


if __name__ == "__main__":
    main()
