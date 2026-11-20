"""Chat classifier double. Uses obvious synthetic marker tokens, never real abuse text."""

from __future__ import annotations

from collections.abc import Sequence

from ..chat import ChatMessage, ChatVerdict
from ..enums import Category, ChatAction

_RULES: tuple[tuple[str, Category, ChatAction], ...] = (
    ("[abuse-test]", Category.CHAT_ABUSE, ChatAction.HIDE_FROM_STREAMER),
    ("[scam-test]", Category.CHAT_SCAM_LINK, ChatAction.DELETE),
    ("[fakeupi-test]", Category.CHAT_FAKE_UPI, ChatAction.DELETE),
    ("[dox-test]", Category.CHAT_DOXXING, ChatAction.HOLD_FOR_MOD),
)


class FakeChatClassifier:
    async def classify(self, batch: Sequence[ChatMessage]) -> Sequence[ChatVerdict]:
        out: list[ChatVerdict] = []
        for m in batch:
            lowered = m.text.lower()
            for marker, cat, action in _RULES:
                if marker in lowered:
                    out.append(
                        ChatVerdict(
                            platform=m.platform,
                            message_id=m.message_id,
                            action=action,
                            categories=(cat,),
                            confidence=0.99,
                            reason="fake_marker",
                            stage="rules",
                            latency_ms=0.1,
                        )
                    )
                    break
            else:
                out.append(
                    ChatVerdict(
                        platform=m.platform,
                        message_id=m.message_id,
                        action=ChatAction.ALLOW,
                        categories=(),
                        confidence=0.9,
                        reason="clean",
                        stage="rules",
                        latency_ms=0.1,
                    )
                )
        return out
