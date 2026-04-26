import jwt
from datetime import datetime, timezone

from coin_trading.exchange.bithumb import BithumbSpotClient


def test_bithumb_query_string_expands_arrays() -> None:
    query = BithumbSpotClient._query_string({"order_ids": ["id1", "id2"], "market": "KRW-BTC"})

    assert query == "order_ids[]=id1&order_ids[]=id2&market=KRW-BTC"


def test_bithumb_auth_headers_include_query_hash() -> None:
    client = BithumbSpotClient(access_key="access", secret_key="s" * 32)

    headers = client._auth_headers("market=KRW-BTC")
    token = headers["Authorization"].removeprefix("Bearer ")
    payload = jwt.decode(token, "s" * 32, algorithms=["HS256"])

    assert payload["access_key"] == "access"
    assert payload["query_hash_alg"] == "SHA512"
    assert len(payload["query_hash"]) == 128


def test_bithumb_candle_endpoint_includes_backfill_to_parameter() -> None:
    endpoint, params = BithumbSpotClient._candle_endpoint(
        "KRW-BTC",
        "1h",
        200,
        datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert endpoint == "/v1/candles/minutes/60"
    assert params["to"] == "2026-01-01T09:00:00"


def test_bithumb_decimal_formatter_avoids_scientific_notation() -> None:
    assert BithumbSpotClient._format_decimal(0.001) == "0.001"
    assert BithumbSpotClient._format_decimal(100000000.0) == "100000000"


def test_bithumb_payload_rows_accepts_data_wrapped_response() -> None:
    rows = BithumbSpotClient._payload_rows({"status": "0000", "data": [{"c": 1}]}, "/v1/test")

    assert rows == [{"c": 1}]


def test_bithumb_payload_rows_rejects_unexpected_response() -> None:
    try:
        BithumbSpotClient._payload_rows({"error": {"message": "bad request"}}, "/v1/test")
    except ValueError as exc:
        assert "Unexpected Bithumb response" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
