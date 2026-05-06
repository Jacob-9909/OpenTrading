import logging

import httpx

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id

    def send(self, text: str) -> None:
        try:
            response = httpx.post(
                self._url,
                json={"chat_id": self._chat_id, "text": text},
                timeout=10,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("[telegram] 알림 전송 실패: %s", exc)
