from __future__ import annotations

from datetime import datetime
from typing import Any


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if hasattr(value, "to_dict"):
        return json_safe(value.to_dict())
    return str(value)


def _type_name(value: Any) -> str:
    return type(value).__name__ if value is not None else ""


def _text(value: Any) -> str | None:
    if value is None:
        return None
    nested = getattr(value, "text", None)
    if isinstance(nested, str):
        return nested
    return value if isinstance(value, str) else str(value)


def _geo(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    latitude = getattr(value, "lat", None)
    longitude = getattr(value, "long", None)
    if latitude is None or longitude is None:
        return None
    return {
        "latitude": latitude,
        "longitude": longitude,
        "accuracy_radius": getattr(value, "accuracy_radius", None),
    }


def _peer_id(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    for field in ("user_id", "channel_id", "chat_id"):
        peer_id = getattr(value, field, None)
        if peer_id is not None:
            return int(peer_id)
    return None


def _extended_media_items(values: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for value in values or []:
        value_name = _type_name(value)
        nested_media = getattr(value, "media", None)
        if nested_media is not None:
            items.append(
                {
                    "state": "available",
                    "media_type": _type_name(nested_media),
                    "raw": json_safe(nested_media),
                }
            )
        else:
            items.append(
                {
                    "state": "preview",
                    "width": getattr(value, "w", None),
                    "height": getattr(value, "h", None),
                    "duration": getattr(value, "video_duration", None),
                    "media_type": value_name,
                    "raw": json_safe(value),
                }
            )
    return items


def _document_descriptor(message: Any, media_type: str) -> dict[str, Any]:
    document = getattr(message, "document", None)
    result: dict[str, Any] = {
        "media_type": media_type,
        "mime_type": getattr(document, "mime_type", None),
        "size": getattr(document, "size", None),
        "file_name": getattr(getattr(message, "file", None), "name", None),
        "duration": getattr(getattr(message, "file", None), "duration", None),
        "width": getattr(getattr(message, "file", None), "width", None),
        "height": getattr(getattr(message, "file", None), "height", None),
        "spoiler": bool(getattr(getattr(message, "media", None), "spoiler", False)),
        "ttl_seconds": getattr(getattr(message, "media", None), "ttl_seconds", None),
    }
    for attribute in getattr(document, "attributes", None) or []:
        name = _type_name(attribute)
        if name == "DocumentAttributeVideo":
            result.update(
                {
                    "duration": getattr(attribute, "duration", None),
                    "width": getattr(attribute, "w", None),
                    "height": getattr(attribute, "h", None),
                    "round_message": bool(getattr(attribute, "round_message", False)),
                    "supports_streaming": bool(getattr(attribute, "supports_streaming", False)),
                    "nosound": bool(getattr(attribute, "nosound", False)),
                }
            )
        elif name == "DocumentAttributeAudio":
            result.update(
                {
                    "duration": getattr(attribute, "duration", None),
                    "voice": bool(getattr(attribute, "voice", False)),
                    "title": getattr(attribute, "title", None),
                    "performer": getattr(attribute, "performer", None),
                    "waveform": json_safe(getattr(attribute, "waveform", None)),
                }
            )
        elif name == "DocumentAttributeSticker":
            result.update(
                {
                    "emoji": getattr(attribute, "alt", None),
                    "sticker_set": json_safe(getattr(attribute, "stickerset", None)),
                    "mask": bool(getattr(attribute, "mask", False)),
                }
            )
        elif name == "DocumentAttributeAnimated":
            result["animated"] = True
        elif name == "DocumentAttributeFilename":
            result["file_name"] = getattr(attribute, "file_name", None)
    result["raw"] = json_safe(getattr(message, "media", None))
    return result


def _poll_content(media: Any) -> dict[str, Any]:
    poll = getattr(media, "poll", None)
    results = getattr(media, "results", None)
    answer_results = {
        bytes(getattr(item, "option", b"") or b""): item
        for item in (getattr(results, "results", None) or [])
    }
    answers: list[dict[str, Any]] = []
    for answer in getattr(poll, "answers", None) or []:
        option = bytes(getattr(answer, "option", b"") or b"")
        answer_result = answer_results.get(option)
        answers.append(
            {
                "text": _text(getattr(answer, "text", None)),
                "option": option.hex(),
                "voters": getattr(answer_result, "voters", 0) if answer_result else 0,
                "chosen": bool(getattr(answer_result, "chosen", False)),
                "correct": bool(getattr(answer_result, "correct", False)),
            }
        )
    return {
        "id": str(getattr(poll, "id", "")),
        "question": _text(getattr(poll, "question", None)),
        "answers": answers,
        "closed": bool(getattr(poll, "closed", False)),
        "public_voters": bool(getattr(poll, "public_voters", False)),
        "multiple_choice": bool(getattr(poll, "multiple_choice", False)),
        "quiz": bool(getattr(poll, "quiz", False)),
        "total_voters": getattr(results, "total_voters", None),
        "solution": getattr(results, "solution", None),
        "raw": json_safe(media),
    }


def _todo_content(media: Any) -> dict[str, Any]:
    todo = getattr(media, "todo", None)
    completions = {
        getattr(completion, "id", None): completion
        for completion in (getattr(media, "completions", None) or [])
    }
    items: list[dict[str, Any]] = []
    for item in getattr(todo, "list", None) or []:
        item_id = getattr(item, "id", None)
        completion = completions.get(item_id)
        items.append(
            {
                "id": item_id,
                "title": _text(getattr(item, "title", None)),
                "completed": completion is not None,
                "completed_by": json_safe(getattr(completion, "completed_by", None)),
                "completed_at": json_safe(getattr(completion, "date", None)),
            }
        )
    return {
        "title": _text(getattr(todo, "title", None)) or "待办事项",
        "items": items,
        "others_can_append": bool(getattr(todo, "others_can_append", False)),
        "others_can_complete": bool(getattr(todo, "others_can_complete", False)),
        "raw": json_safe(media),
    }


SERVICE_LABELS = {
    "MessageActionChatCreate": "创建了群组",
    "MessageActionChatEditTitle": "修改了群组名称",
    "MessageActionChatEditPhoto": "更新了群组头像",
    "MessageActionChatDeletePhoto": "移除了群组头像",
    "MessageActionChatAddUser": "有新成员加入",
    "MessageActionChatDeleteUser": "有成员离开",
    "MessageActionChatJoinedByLink": "通过邀请链接加入",
    "MessageActionChannelCreate": "创建了频道",
    "MessageActionPinMessage": "置顶了一条消息",
    "MessageActionHistoryClear": "清除了聊天记录",
    "MessageActionPhoneCall": "通话",
    "MessageActionGroupCall": "群组通话",
    "MessageActionGroupCallScheduled": "安排了群组通话",
    "MessageActionSetMessagesTTL": "修改了消息自动删除时间",
    "MessageActionTopicCreate": "创建了话题",
    "MessageActionTopicEdit": "修改了话题",
    "MessageActionGameScore": "更新了游戏得分",
    "MessageActionPaymentSentMe": "收到了一笔付款",
    "MessageActionPaymentSent": "发送了一笔付款",
    "MessageActionGiftPremium": "赠送了 Telegram Premium",
    "MessageActionGiftStars": "赠送了 Telegram Stars",
    "MessageActionBoostApply": "为聊天助力",
}


def _service_content(action: Any) -> dict[str, Any]:
    action_type = _type_name(action)
    summary = SERVICE_LABELS.get(action_type, action_type.removeprefix("MessageAction") or "服务消息")
    title = getattr(action, "title", None)
    if title and action_type in {"MessageActionChatEditTitle", "MessageActionTopicCreate"}:
        summary = f"{summary}：{title}"
    return {
        "action_type": action_type,
        "summary": summary,
        "raw": json_safe(action),
    }


def serialize_message_content(message: Any, media_type: str | None) -> tuple[str, dict[str, Any]]:
    action = getattr(message, "action", None)
    if action is not None:
        return "service", _service_content(action)

    media = getattr(message, "media", None)
    media_name = _type_name(media)

    if media_name == "MessageMediaContact":
        name = " ".join(
            part for part in (getattr(media, "first_name", None), getattr(media, "last_name", None)) if part
        )
        return "contact", {
            "name": name or "联系人",
            "first_name": getattr(media, "first_name", None),
            "last_name": getattr(media, "last_name", None),
            "phone_number": getattr(media, "phone_number", None),
            "user_id": getattr(media, "user_id", None),
            "vcard": getattr(media, "vcard", None),
            "raw": json_safe(media),
        }
    if media_name == "MessageMediaGeo":
        return "location", {"geo": _geo(getattr(media, "geo", None)), "raw": json_safe(media)}
    if media_name == "MessageMediaVenue":
        return "venue", {
            "title": getattr(media, "title", None),
            "address": getattr(media, "address", None),
            "provider": getattr(media, "provider", None),
            "venue_id": getattr(media, "venue_id", None),
            "venue_type": getattr(media, "venue_type", None),
            "geo": _geo(getattr(media, "geo", None)),
            "raw": json_safe(media),
        }
    if media_name == "MessageMediaPoll":
        return "poll", _poll_content(media)
    if media_name == "MessageMediaDice":
        return "dice", {
            "emoticon": getattr(media, "emoticon", None) or "🎲",
            "value": getattr(media, "value", None),
            "raw": json_safe(media),
        }
    if media_name == "MessageMediaGame":
        game = getattr(media, "game", None)
        return "game", {
            "id": str(getattr(game, "id", "")),
            "access_hash": str(getattr(game, "access_hash", "")),
            "short_name": getattr(game, "short_name", None),
            "title": getattr(game, "title", None),
            "description": getattr(game, "description", None),
            "has_photo": getattr(game, "photo", None) is not None,
            "has_document": getattr(game, "document", None) is not None,
            "raw": json_safe(media),
        }
    if media_name == "MessageMediaInvoice":
        extended_media = getattr(media, "extended_media", None)
        return "invoice", {
            "title": getattr(media, "title", None),
            "description": getattr(media, "description", None),
            "currency": getattr(media, "currency", None),
            "total_amount": getattr(media, "total_amount", None),
            "start_param": getattr(media, "start_param", None),
            "test": bool(getattr(media, "test", False)),
            "shipping_address_requested": bool(
                getattr(media, "shipping_address_requested", False)
            ),
            "receipt_message_id": getattr(media, "receipt_msg_id", None),
            "has_photo": getattr(media, "photo", None) is not None,
            "extended_media": _extended_media_items(
                [extended_media] if extended_media is not None else []
            ),
            "raw": json_safe(media),
        }
    if media_name == "MessageMediaStory":
        story = getattr(media, "story", None)
        story_type = _type_name(story)
        return "story", {
            "story_id": getattr(media, "id", None),
            "peer_id": _peer_id(getattr(media, "peer", None)),
            "peer": json_safe(getattr(media, "peer", None)),
            "via_mention": bool(getattr(media, "via_mention", False)),
            "state": (
                "expired"
                if story_type == "StoryItemDeleted"
                else "available" if story_type == "StoryItem" else "unavailable"
            ),
            "date": json_safe(getattr(story, "date", None)),
            "expire_date": json_safe(getattr(story, "expire_date", None)),
            "caption": getattr(story, "caption", None),
            "media_type": _type_name(getattr(story, "media", None)),
            "raw": json_safe(media),
        }
    if "PaidMedia" in media_name:
        items = _extended_media_items(getattr(media, "extended_media", None))
        return "paid_media", {
            "stars_amount": getattr(media, "stars_amount", None),
            "items": items,
            "item_count": len(items),
            "purchased": bool(items) and all(item["state"] == "available" for item in items),
            "raw": json_safe(media),
        }
    if "GiveawayResults" in media_name:
        winners = [
            peer_id
            for value in (getattr(media, "winners", None) or [])
            if (peer_id := _peer_id(value)) is not None
        ]
        return "giveaway_results", {
            "channel_id": _peer_id(getattr(media, "channel_id", None)),
            "launch_message_id": getattr(media, "launch_msg_id", None),
            "winners_count": getattr(media, "winners_count", None),
            "unclaimed_count": getattr(media, "unclaimed_count", None),
            "winner_ids": winners,
            "additional_peers_count": getattr(media, "additional_peers_count", None),
            "months": getattr(media, "months", None),
            "stars": getattr(media, "stars", None),
            "prize_description": getattr(media, "prize_description", None),
            "until_date": json_safe(getattr(media, "until_date", None)),
            "only_new_subscribers": bool(
                getattr(media, "only_new_subscribers", False)
            ),
            "refunded": bool(getattr(media, "refunded", False)),
            "raw": json_safe(media),
        }
    if "Giveaway" in media_name:
        channels = [
            peer_id
            for value in (getattr(media, "channels", None) or [])
            if (peer_id := _peer_id(value)) is not None
        ]
        return "giveaway", {
            "channel_ids": channels,
            "quantity": getattr(media, "quantity", None),
            "months": getattr(media, "months", None),
            "stars": getattr(media, "stars", None),
            "until_date": json_safe(getattr(media, "until_date", None)),
            "countries": list(getattr(media, "countries_iso2", None) or []),
            "only_new_subscribers": bool(
                getattr(media, "only_new_subscribers", False)
            ),
            "winners_are_visible": bool(getattr(media, "winners_are_visible", False)),
            "prize_description": getattr(media, "prize_description", None),
            "raw": json_safe(media),
        }
    if "Todo" in media_name or "ToDo" in media_name:
        return "todo", _todo_content(media)
    if media_name == "MessageMediaWebPage":
        webpage = getattr(media, "webpage", None)
        return "webpage", {
            "url": getattr(webpage, "url", None),
            "title": getattr(webpage, "title", None),
            "description": getattr(webpage, "description", None),
            "site_name": getattr(webpage, "site_name", None),
            "raw": json_safe(media),
        }

    if media_type:
        descriptor = _document_descriptor(message, media_type) if getattr(message, "document", None) else {
            "media_type": media_type,
            "spoiler": bool(getattr(media, "spoiler", False)),
            "ttl_seconds": getattr(media, "ttl_seconds", None),
            "raw": json_safe(media),
        }
        if media_type == "video" and descriptor.get("round_message"):
            return "round_video", descriptor
        return media_type, descriptor

    if getattr(message, "message", None):
        return "text", {}
    if media is not None:
        return "unsupported", {"media_type": media_name, "raw": json_safe(media)}
    return "unsupported", {}
