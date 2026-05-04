import hashlib
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
import jwt
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from coin_trading.market.exchange.base import Candle


class BithumbSpotClient:
    def __init__(
        self,
        access_key: str | None = None,
        secret_key: str | None = None,
        base_url: str = "https://api.bithumb.com",
        timeout: float = 10.0,
    ) -> None:
        self.access_key = access_key
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        reraise=True,
    )
    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 200,
        end_time: datetime | None = None,
    ) -> list[Candle]:
        endpoint, params = self._candle_endpoint(symbol, interval, limit, end_time)
        response = httpx.get(f"{self.base_url}{endpoint}", params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        rows = self._payload_rows(payload, endpoint)
        candles = [self._parse_candle(symbol, interval, row) for row in rows]
        return sorted(candles, key=lambda candle: candle.open_time)

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        reraise=True,
    )
    def get_mark_price(self, symbol: str) -> float:
        response = httpx.get(
            f"{self.base_url}/v1/ticker",
            params={"markets": symbol},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload:
            raise ValueError(f"No Bithumb ticker returned for {symbol}.")
        return float(payload[0]["trade_price"])

    def get_accounts(self) -> list[dict[str, Any]]:
        response = httpx.get(
            f"{self.base_url}/v1/accounts",
            headers=self._auth_headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_order_chance(self, market: str) -> dict[str, Any]:
        params = {"market": market}
        query = self._query_string(params)
        response = httpx.get(
            f"{self.base_url}/v1/orders/chance",
            params=params,
            headers=self._auth_headers(query),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def place_order(self, body: dict[str, Any]) -> dict[str, Any]:
        query = self._query_string(body)
        response = httpx.post(
            f"{self.base_url}/v1/orders",
            json=body,
            headers=self._auth_headers(query) | {"Content-Type": "application/json; charset=utf-8"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def place_limit_order(
        self,
        market: str,
        side: Literal["bid", "ask"],
        volume: float,
        price: float,
    ) -> dict[str, Any]:
        return self.place_order(
            {
                "market": market,
                "side": side,
                "volume": self._format_decimal(volume),
                "price": self._format_decimal(price),
                "ord_type": "limit",
            }
        )

    def place_market_buy(self, market: str, quote_amount: float) -> dict[str, Any]:
        return self.place_order(
            {
                "market": market,
                "side": "bid",
                "price": self._format_decimal(quote_amount),
                "ord_type": "price",
            }
        )

    def place_market_sell(self, market: str, volume: float) -> dict[str, Any]:
        return self.place_order(
            {
                "market": market,
                "side": "ask",
                "volume": self._format_decimal(volume),
                "ord_type": "market",
            }
        )

    def get_order(self, order_id: str) -> dict[str, Any]:
        params = {"uuid": order_id}
        query = self._query_string(params)
        response = httpx.get(
            f"{self.base_url}/v1/order",
            params=params,
            headers=self._auth_headers(query),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        params = {"order_id": order_id}
        query = self._query_string(params)
        response = httpx.delete(
            f"{self.base_url}/v2/order",
            params=params,
            headers=self._auth_headers(query),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def _auth_headers(self, query: str = "") -> dict[str, str]:
        if not self.access_key or not self.secret_key:
            raise ValueError("BITHUMB_ACCESS_KEY and BITHUMB_SECRET_KEY are required.")

        payload: dict[str, Any] = {
            "access_key": self.access_key,
            "nonce": str(uuid.uuid4()),
            "timestamp": round(time.time() * 1000),
        }
        if query:
            payload["query_hash"] = hashlib.sha512(query.encode("utf-8")).hexdigest()
            payload["query_hash_alg"] = "SHA512"

        token = jwt.encode(payload, self.secret_key, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _query_string(params: dict[str, Any]) -> str:
        pairs: list[tuple[str, Any]] = []
        for key, value in params.items():
            if isinstance(value, list):
                pairs.extend((f"{key}[]", item) for item in value)
            else:
                pairs.append((key, value))
        return urlencode(pairs, safe="[]")

    @staticmethod
    def _format_decimal(value: float) -> str:
        return f"{value:.8f}".rstrip("0").rstrip(".")

    @staticmethod
    def _payload_rows(payload: Any, endpoint: str) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            return payload["data"]
        raise ValueError(f"Unexpected Bithumb response for {endpoint}: {payload!r}")

    @staticmethod
    def _candle_endpoint(
        symbol: str,
        interval: str,
        limit: int,
        end_time: datetime | None = None,
    ) -> tuple[str, dict[str, Any]]:
        params: dict[str, Any] = {"market": symbol, "count": limit}
        if end_time:
            kst = timezone(timedelta(hours=9))
            params["to"] = end_time.astimezone(kst).strftime("%Y-%m-%dT%H:%M:%S")
        if interval in {"1d", "day", "days"}:
            return "/v1/candles/days", params

        unit = BithumbSpotClient._minute_unit(interval)
        return f"/v1/candles/minutes/{unit}", params

    @staticmethod
    def _minute_unit(interval: str) -> int:
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
        if interval not in mapping:
            raise ValueError(f"Unsupported Bithumb candle interval: {interval}")
        return mapping[interval]

    @staticmethod
    def _parse_candle(symbol: str, timeframe: str, row: dict[str, Any] | list[Any]) -> Candle:
        if isinstance(row, list):
            return BithumbSpotClient._parse_legacy_candle(symbol, timeframe, row)
        if not isinstance(row, dict):
            raise ValueError(f"Unexpected Bithumb candle row: {row!r}")

        open_time = datetime.fromisoformat(row["candle_date_time_utc"]).replace(tzinfo=timezone.utc)
        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=open_time,
            close_time=open_time + BithumbSpotClient._time_delta(timeframe),
            open=float(row["opening_price"]),
            high=float(row["high_price"]),
            low=float(row["low_price"]),
            close=float(row["trade_price"]),
            volume=float(row["candle_acc_trade_volume"]),
        )

    @staticmethod
    def _parse_legacy_candle(symbol: str, timeframe: str, row: list[Any]) -> Candle:
        if len(row) < 6:
            raise ValueError(f"Unexpected Bithumb legacy candle row: {row!r}")
        open_time = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc)
        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=open_time,
            close_time=open_time + BithumbSpotClient._time_delta(timeframe),
            open=float(row[1]),
            close=float(row[2]),
            high=float(row[3]),
            low=float(row[4]),
            volume=float(row[5]),
        )

    @staticmethod
    def _time_delta(interval: str) -> timedelta:
        if interval in {"1d", "day", "days"}:
            return timedelta(days=1)
        return timedelta(minutes=BithumbSpotClient._minute_unit(interval))
