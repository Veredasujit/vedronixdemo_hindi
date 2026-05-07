import datetime
import json
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

EVENT_LOG_SINK_ID: Optional[int] = None
CURRENT_CALL_ID: Optional[str] = None


def configure_event_log_sink(recordings_dir: Path) -> None:
    global EVENT_LOG_SINK_ID
    if EVENT_LOG_SINK_ID is not None:
        return

    EVENT_LOG_SINK_ID = logger.add(
        recordings_dir / "call_events.log",
        rotation="10 MB",
        level="INFO",
        filter=lambda record: record["message"].startswith("[CALL_EVENT]"),
    )


def set_call_context(call_id: Optional[str]) -> None:
    global CURRENT_CALL_ID
    CURRENT_CALL_ID = str(call_id).strip() if call_id else None


def _compact_text(value: Any, max_len: int = 220) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def log_call_event(event: str, *, call_id: Optional[str] = None, **data: Any) -> None:
    payload: Dict[str, Any] = {
        "ts": datetime.datetime.utcnow().replace(tzinfo=datetime.UTC).isoformat(),
        "event": event,
        "call_id": call_id or CURRENT_CALL_ID or "",
        "data": data,
    }
    logger.info("[CALL_EVENT] {}", json.dumps(payload, ensure_ascii=False, default=_compact_text))
