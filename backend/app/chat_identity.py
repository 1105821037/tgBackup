from __future__ import annotations


SAVED_MESSAGES_TITLE = "收藏夹"


def chat_display_title(
    telegram_user_id: int,
    peer_id: int,
    fallback: str,
) -> str:
    """Return the product-facing title for a Telegram dialog."""
    if peer_id == telegram_user_id:
        return SAVED_MESSAGES_TITLE
    return fallback
