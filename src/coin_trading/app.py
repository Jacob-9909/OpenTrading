import typer

from coin_trading.config import get_settings
from coin_trading.db import init_db
from coin_trading.scheduler import TradingPipeline

cli = typer.Typer(help="Batch Bithumb spot LLM trading system.")


@cli.command("init-db")
def init_database(
    reset: bool = typer.Option(
        False,
        "--reset",
        help="모든 데이터를 삭제하고 테이블을 재생성합니다 (구조 유지, 데이터 초기화).",
    ),
) -> None:
    if reset:
        settings = get_settings()
        if settings.trading_mode == "live":
            typer.confirm(
                "⚠️  실제 코인(LIVE) 모드입니다. 모든 거래 기록이 삭제됩니다. 계속하시겠습니까?",
                abort=True,
            )
        from coin_trading.db import reset_db
        reset_db()
        typer.echo("✅ 모든 데이터가 초기화되고 테이블이 재생성되었습니다.")
    else:
        init_db()
        typer.echo("Database tables are ready.")


@cli.command("run-once")
def run_once() -> None:
    init_db()
    result = TradingPipeline(get_settings()).run_once()
    typer.echo(
        f"price={result.latest_price} signal={result.signal_id}:{result.signal_status} "
        f"order={result.order_id} risk='{result.risk_reason}'"
    )


@cli.command("refresh-data")
def refresh_data() -> None:
    init_db()
    result = TradingPipeline(get_settings()).refresh_data_once()
    typer.echo(
        f"price={result.latest_price} refreshed_timeframes={','.join(result.refreshed_timeframes)}"
    )


@cli.command("decide-once")
def decide_once() -> None:
    init_db()
    result = TradingPipeline(get_settings()).decide_once()
    typer.echo(
        f"price={result.latest_price} signal={result.signal_id}:{result.signal_status} "
        f"order={result.order_id} risk='{result.risk_reason}'"
    )


@cli.command("serve-all")
def serve_all() -> None:
    init_db()
    TradingPipeline(get_settings()).serve_all()


def main() -> None:
    cli()
