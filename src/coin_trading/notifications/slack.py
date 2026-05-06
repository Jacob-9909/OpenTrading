import logging

import httpx

logger = logging.getLogger(__name__)


class SlackNotifier:
    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def send(self, text: str) -> None:
        try:
            response = httpx.post(
                self.webhook_url,
                json={"text": text},
                timeout=10,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("[slack] 알림 전송 실패: %s", exc)
