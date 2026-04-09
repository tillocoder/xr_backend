from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.me import (
    PortfolioVoiceCommandResponse,
    PortfolioVoiceOperationResponse,
)


class PortfolioVoiceNotConfiguredError(RuntimeError):
    pass


class PortfolioVoiceUnavailableError(RuntimeError):
    pass


class PortfolioVoiceInterpretationError(RuntimeError):
    pass


class PortfolioVoiceCommandService:
    async def interpret_transcript(
        self,
        db: AsyncSession,
        *,
        transcript: str,
        app_language_code: str,
        speech_locale_id: str | None = None,
    ) -> PortfolioVoiceCommandResponse:
        clean_transcript = _clean_text(transcript)
        if not clean_transcript:
            raise ValueError("Transcript is required.")

        local_operations = _parse_simple_transcript(clean_transcript)
        if local_operations:
            return PortfolioVoiceCommandResponse(
                transcript=clean_transcript,
                message=_default_message(local_operations),
                applyMode="append",
                operations=local_operations,
            )
        raise PortfolioVoiceInterpretationError(
            "Portfolio command could not be understood. Use a simple format like '0.3 ETH 3200 dan oldim'."
        )

    async def interpret_audio(
        self,
        db: AsyncSession,
        *,
        audio_bytes: bytes,
        mime_type: str | None,
        filename: str | None,
        app_language_code: str,
        speech_locale_id: str | None = None,
    ) -> PortfolioVoiceCommandResponse:
        del db, audio_bytes, mime_type, filename, app_language_code, speech_locale_id
        raise PortfolioVoiceUnavailableError(
            "Portfolio voice audio is disabled. Send text like '0.3 ETH 3200 dan oldim'."
        )


_SYMBOL_ALIASES = {
    "ADA": "ADA",
    "AVALANCHE": "AVAX",
    "AVAX": "AVAX",
    "BINANCE": "BNB",
    "BINANCECOIN": "BNB",
    "BNB": "BNB",
    "BITCOIN": "BTC",
    "BTC": "BTC",
    "CARDANO": "ADA",
    "DOGE": "DOGE",
    "DOGECOIN": "DOGE",
    "DOT": "DOT",
    "ETH": "ETH",
    "ETHER": "ETH",
    "ETHEREUM": "ETH",
    "ETHERIUM": "ETH",
    "LINK": "LINK",
    "LITECOIN": "LTC",
    "LTC": "LTC",
    "POLKADOT": "DOT",
    "RIPPLE": "XRP",
    "SHIB": "SHIB",
    "SHIBAINU": "SHIB",
    "SOL": "SOL",
    "SOLANA": "SOL",
    "TON": "TON",
    "TONCOIN": "TON",
    "TRON": "TRX",
    "TRX": "TRX",
    "XRP": "XRP",
}

_SIMPLE_COMMAND_PATTERNS = [
    re.compile(
        r"""
        (?P<action>bought|buy|added|add|got)\s+
        (?P<amount>\d+(?:[.,]\d+)?)\s*
        (?P<symbol>[A-Za-z][A-Za-z0-9]{1,14})
        (?:
            \s+(?:at|@|from)\s+
            (?P<price>\d+(?:[.,]\d+)?)
        )?
        """,
        re.IGNORECASE | re.VERBOSE,
    ),
    re.compile(
        r"""
        (?P<amount>\d+(?:[.,]\d+)?)\s*
        (?P<symbol>[A-Za-z][A-Za-z0-9]{1,14})
        \s+
        (?P<price>\d+(?:[.,]\d+)?)
        (?:\s*(?:dan|da|at|@|from))?
        (?:\s+(?:oldim|sotib\s+oldim|bought|buy|added|add|got))?
        """,
        re.IGNORECASE | re.VERBOSE,
    ),
    re.compile(
        r"""
        (?P<amount>\d+(?:[.,]\d+)?)\s*
        (?P<symbol>[A-Za-z][A-Za-z0-9]{1,14})
        .*?
        (?:(?:at|@|from|dan|da)\s*)?
        (?P<price>\d+(?:[.,]\d+)?)
        """,
        re.IGNORECASE | re.VERBOSE,
    ),
]

_AUDIO_MIME_BY_EXTENSION = {
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
}

def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    normalized = raw.replace("```json", "```").replace("```JSON", "```").strip("` \n\t")
    start = normalized.find("{")
    end = normalized.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    snippet = normalized[start : end + 1]
    try:
        data = json.loads(snippet)
    except Exception:
        return None
    if isinstance(data, dict):
        return data
    return None


def _parse_simple_transcript(transcript: str) -> list[PortfolioVoiceOperationResponse]:
    match = None
    for pattern in _SIMPLE_COMMAND_PATTERNS:
        match = pattern.search(transcript)
        if match:
            break
    if match is None:
        return []

    symbol = _normalize_symbol(match.group("symbol"))
    amount = _read_float(match.group("amount"))
    buy_price = _read_float(match.group("price"))
    if symbol is None or amount is None or amount <= 0 or buy_price is None or buy_price <= 0:
        return []

    now = datetime.now(timezone.utc)
    return [
        PortfolioVoiceOperationResponse(
            type="add",
            symbol=symbol,
            amount=amount,
            buyPrice=buy_price,
            buyAt=None,
            note=transcript,
            coinId=None,
            createdAt=now,
        )
    ]


def _normalize_audio_mime_type(
    mime_type: str | None,
    *,
    filename: str | None,
) -> str | None:
    raw = _coerce_text(mime_type)
    if raw and raw.lower().startswith("audio/"):
        return raw.lower()
    suffix = Path(filename or "").suffix.lower()
    if suffix:
        return _AUDIO_MIME_BY_EXTENSION.get(suffix)
    return None


def _parse_apply_mode(payload: dict[str, Any]) -> str:
    raw = _normalize_token(
        payload.get("applyMode")
        or payload.get("mode")
        or payload.get("updateMode")
        or payload.get("operationMode")
    )
    if raw in {"replaceall", "replace", "overwrite", "reset"}:
        return "replaceAll"
    return "append"


def _parse_operations(
    payload: dict[str, Any],
    *,
    fallback_transcript: str,
) -> list[PortfolioVoiceOperationResponse]:
    raw_operations = _extract_operation_maps(payload)
    created_at = datetime.now(timezone.utc)
    operations: list[PortfolioVoiceOperationResponse] = []

    for item in raw_operations:
        action = _normalize_token(
            item.get("type")
            or item.get("action")
            or item.get("intent")
            or item.get("operation")
        )
        if action not in {None, "", "add", "buy", "append", "create"}:
            continue

        symbol = _normalize_symbol(item.get("symbol") or item.get("asset") or item.get("ticker"))
        amount = _read_float(item.get("amount") or item.get("quantity") or item.get("qty"))
        buy_price = _read_float(
            item.get("buyPrice")
            or item.get("entryPrice")
            or item.get("price")
            or item.get("avgPrice")
            or item.get("cost")
        )
        if not symbol or amount is None or amount <= 0 or buy_price is None or buy_price <= 0:
            continue

        operations.append(
            PortfolioVoiceOperationResponse(
                type="add",
                symbol=symbol,
                amount=amount,
                buyPrice=buy_price,
                buyAt=_read_datetime(
                    item.get("buyAt")
                    or item.get("entryDate")
                    or item.get("purchasedAt")
                    or item.get("date")
                ),
                note=_coerce_text(item.get("note") or item.get("reason")) or fallback_transcript,
                coinId=_coerce_text(item.get("coinId")),
                createdAt=_read_datetime(item.get("createdAt") or item.get("timestamp"))
                or created_at,
            )
        )

    return operations


def _extract_operation_maps(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("operations", "updates", "holdings", "items", "commands"):
        value = payload.get(key)
        if isinstance(value, list):
            items = [_as_map(item) for item in value]
            mapped = [item for item in items if item is not None]
            if mapped:
                return mapped

    for key in ("data", "payload", "result"):
        nested = _as_map(payload.get(key))
        if nested is None:
            continue
        mapped = _extract_operation_maps(nested)
        if mapped:
            return mapped

    if _as_map(payload) is not None and _normalize_symbol(
        payload.get("symbol") or payload.get("asset") or payload.get("ticker")
    ):
        return [payload]
    return []


def _as_map(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    return None


def _default_message(operations: list[PortfolioVoiceOperationResponse]) -> str:
    if len(operations) == 1:
        return f"{operations[0].symbol} portfolio command is ready."
    return f"{len(operations)} portfolio entries are ready."


def _clean_text(value: str | None) -> str:
    return re.sub(r"[\u0000-\u001f]+", " ", str(value or "")).strip()


def _coerce_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _normalize_token(value: object) -> str | None:
    text = _coerce_text(value)
    if text is None:
        return None
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _normalize_symbol(value: object) -> str | None:
    text = _coerce_text(value)
    if text is None:
        return None
    symbol = re.sub(r"[^A-Za-z0-9]+", "", text.upper())
    if not symbol:
        return None
    return _SYMBOL_ALIASES.get(symbol, symbol)


def _read_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)

    text = _coerce_text(value)
    if text is None:
        return None

    compact = text.replace(" ", "")
    if "," in compact and "." in compact:
        return float(compact.replace(",", "")) if compact.replace(",", "").replace(".", "", 1).isdigit() else None

    if "," in compact:
        last_comma = compact.rfind(",")
        digits_after = len(compact) - last_comma - 1
        compact = compact.replace(",", "" if digits_after == 3 else ".")

    try:
        return float(compact)
    except ValueError:
        return None


def _read_datetime(value: object) -> datetime | None:
    text = _coerce_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
