import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_SUMMARY_PROMPT = """당신은 암호화폐 트레이딩 봇의 상태를 팀에 간결하게 알려주는 어시스턴트입니다.
아래 트레이딩 정보를 바탕으로 텔레그램에 보낼 요약 메시지를 한국어로 작성하세요.

형식 규칙:
- 이모지를 활용하지말고 줄바꿈을 활용해 가독성을 높이세요
- 핵심 수치(가격, PnL, SL/TP)는 반드시 포함
- 3~6줄 이내로 간결하게
- 마크다운 없이 plain text로 작성

트레이딩 정보:
{info}
"""


@dataclass(frozen=True)
class TradeContext:
    symbol: str
    side: str
    entry_price: float
    stop_loss: float | None
    take_profit: float | None
    confidence: float
    rationale: str
    mark_price: float
    realized_pnl: float
    unrealized_pnl: float
    equity: float
    open_positions: int


class GeminiSummarizer:
    def __init__(
        self,
        project_id: str,
        model_id: str,
        location: str = "us-central1",
        credentials_path: str | None = None,
    ) -> None:
        if credentials_path:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path

        from google import genai

        self._client = genai.Client(vertexai=True, project=project_id, location=location)
        self._model_id = model_id

    def summarize(self, ctx: TradeContext) -> str:
        info = self._format_context(ctx)
        prompt = _SUMMARY_PROMPT.format(info=info)
        try:
            response = self._client.models.generate_content(model=self._model_id, contents=prompt)
            return (response.text or "").strip()
        except Exception as exc:
            logger.warning("[gemini-summarizer] 요약 생성 실패: %s", exc)
            return self._fallback_summary(ctx)

    @staticmethod
    def _format_context(ctx: TradeContext) -> str:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        sl_str = f"{ctx.stop_loss:,.0f}" if ctx.stop_loss else "없음"
        tp_str = f"{ctx.take_profit:,.0f}" if ctx.take_profit else "없음"
        return (
            f"시각: {now}\n"
            f"심볼: {ctx.symbol}\n"
            f"신호: {ctx.side} (신뢰도 {ctx.confidence:.0%})\n"
            f"현재가: {ctx.mark_price:,.0f}\n"
            f"진입가: {ctx.entry_price:,.0f}\n"
            f"손절(SL): {sl_str}\n"
            f"목표가(TP): {tp_str}\n"
            f"미실현 PnL: {ctx.unrealized_pnl:+,.0f}\n"
            f"실현 PnL: {ctx.realized_pnl:+,.0f}\n"
            f"총 자산: {ctx.equity:,.0f}\n"
            f"오픈 포지션 수: {ctx.open_positions}\n"
            f"판단 근거: {ctx.rationale}"
        )

    @staticmethod
    def _fallback_summary(ctx: TradeContext) -> str:
        sl_str = f"{ctx.stop_loss:,.0f}" if ctx.stop_loss else "-"
        tp_str = f"{ctx.take_profit:,.0f}" if ctx.take_profit else "-"
        side_emoji = "📈" if ctx.side == "LONG" else "📉"
        return (
            f"{side_emoji} [{ctx.symbol}] {ctx.side} 신호 발생\n"
            f"현재가: {ctx.mark_price:,.0f} | 진입: {ctx.entry_price:,.0f}\n"
            f"SL: {sl_str} | TP: {tp_str}\n"
            f"신뢰도: {ctx.confidence:.0%} | 미실현PnL: {ctx.unrealized_pnl:+,.0f}\n"
            f"근거: {ctx.rationale[:100]}"
        )
