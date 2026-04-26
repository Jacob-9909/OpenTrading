import typer

from coin_trading.config import get_settings
from coin_trading.db import init_db
from coin_trading.scheduler import TradingPipeline

cli = typer.Typer(help="Batch Bithumb spot LLM trading system.")


@cli.command("init-db")
def init_database() -> None:
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


@cli.command("serve")
def serve() -> None:
    init_db()
    TradingPipeline(get_settings()).serve()


@cli.command("serve-decisions")
def serve_decisions() -> None:
    init_db()
    TradingPipeline(get_settings()).serve_decisions()


@cli.command("serve-run-once")
def serve_run_once() -> None:
    init_db()
    TradingPipeline(get_settings()).serve_run_once()


def main() -> None:
    cli()
