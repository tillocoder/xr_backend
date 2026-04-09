from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.me import (
    PortfolioVoiceCommandResponse,
    PortfolioVoiceOperationResponse,
)
from app.services.ai_provider_config_service import (
    GEMINI_SCOPE_DEFAULT,
    GEMINI_SCOPE_PORTFOLIO,
)
from app.services.gemini_service import build_gemini_client


class PortfolioVoiceNotConfiguredError(RuntimeError):
    pass


class PortfolioVoiceUnavailableError(RuntimeError):
    pass


class PortfolioVoiceInterpretationError(RuntimeError):
    pass


GeminiClientBuilder = Callable[..., Awaitable[object | None]]


class PortfolioVoiceCommandService:
    def __init__(
        self,
        *,
        gemini_client_builder: GeminiClientBuilder | None = None,
    ) -> None:
        self._gemini_client_builder = gemini_client_builder or build_gemini_client

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

        gemini = await self._gemini_client_builder(
            db,
            usage_scope=GEMINI_SCOPE_PORTFOLIO,
            fallback_scopes=(GEMINI_SCOPE_DEFAULT,),
        )
        if gemini is None:
            raise PortfolioVoiceNotConfiguredError(
                "Portfolio Gemini API key is not configured."
            )

        result = await gemini.generate_text(
            prompt=_build_prompt(
                transcript=clean_transcript,
                app_language_code=app_language_code,
                speech_locale_id=speech_locale_id,
            ),
            temperature=0.0,
            timeout_seconds=18,
            response_mime_type="application/json",
            max_output_tokens=256,
        )
        if result is None or not str(getattr(result, "text", "") or "").strip():
            raise PortfolioVoiceUnavailableError(
                "Portfolio voice command is unavailable right now."
            )

        payload = _extract_json_object(result.text)
        if payload is None:
            raise PortfolioVoiceInterpretationError(
                "Portfolio command response was not valid JSON."
            )

        operations = _parse_operations(payload, fallback_transcript=clean_transcript)
        if not operations:
            raise PortfolioVoiceInterpretationError(
                _coerce_text(payload.get("message"))
                or "Portfolio command could not be understood."
            )

        return PortfolioVoiceCommandResponse(
            transcript=_coerce_text(payload.get("transcript")) or clean_transcript,
            message=_coerce_text(payload.get("message")) or _default_message(operations),
            applyMode=_parse_apply_mode(payload),
            operations=operations,
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
        clean_mime_type = _normalize_audio_mime_type(mime_type, filename=filename)
        if not audio_bytes:
            raise ValueError("Audio is required.")
        if clean_mime_type is None:
            raise ValueError("Unsupported audio format.")

        gemini = await self._gemini_client_builder(
            db,
            usage_scope=GEMINI_SCOPE_PORTFOLIO,
            fallback_scopes=(GEMINI_SCOPE_DEFAULT,),
        )
        if gemini is None:
            raise PortfolioVoiceNotConfiguredError(
                "Portfolio Gemini API key is not configured."
            )

        result = await gemini.generate_audio_text(
            prompt=_build_audio_prompt(
                app_language_code=app_language_code,
                speech_locale_id=speech_locale_id,
            ),
            audio_bytes=audio_bytes,
            mime_type=clean_mime_type,
            temperature=0.0,
            timeout_seconds=45,
            response_mime_type="text/plain",
            max_output_tokens=160,
        )
        if result is None or not str(getattr(result, "text", "") or "").strip():
            raise PortfolioVoiceUnavailableError(
                "Portfolio voice command is unavailable right now."
            )

        transcript = _clean_text(str(getattr(result, "text", "") or ""))
        if not transcript:
            raise PortfolioVoiceInterpretationError(
                "Portfolio command could not be understood."
            )

        try:
            return await self.interpret_transcript(
                db,
                transcript=transcript,
                app_language_code=app_language_code,
                speech_locale_id=speech_locale_id,
            )
        except ValueError as exc:
            raise PortfolioVoiceInterpretationError(
                str(exc) or "Portfolio command could not be understood."
            ) from exc
        except PortfolioVoiceInterpretationError as exc:
            raise PortfolioVoiceInterpretationError(
                f"{str(exc) or 'Portfolio command could not be understood.'} "
                f"Transcript: {transcript[:160]}"
            ) from exc
        except PortfolioVoiceUnavailableError:
            local_operations = _parse_simple_transcript(transcript)
            if not local_operations:
                raise
            return PortfolioVoiceCommandResponse(
                transcript=transcript,
                message=_default_message(local_operations),
                applyMode="append",
                operations=local_operations,
            )


def _build_prompt(
    *,
    transcript: str,
    app_language_code: str,
    speech_locale_id: str | None,
) -> str:
    language = _coerce_text(app_language_code) or "en"
    locale_id = _coerce_text(speech_locale_id) or "unknown"
    now = datetime.now(timezone.utc).isoformat()
    return f"""
Convert a short spoken crypto portfolio buy command into strict JSON for a mobile app.

Current UTC time: {now}
App language code: {language}
Speech locale id: {locale_id}

User transcript:
{transcript}

Rules:
- Return JSON only. No markdown and no code fences.
- Use exactly this shape:
  {{
    "transcript": "clean transcript",
    "message": "short user-facing summary",
    "applyMode": "append",
    "operations": [
      {{
        "type": "add",
        "symbol": "ETH",
        "amount": 0.3,
        "buyPrice": 3200,
        "buyAt": "optional ISO-8601 datetime"
      }}
    ]
  }}
- type must be "add".
- symbol must be a crypto ticker in uppercase without spaces.
- Support any crypto asset, including new, niche, meme, and low-cap coins.
- If the asset is recognizable, use its standard market ticker.
- If the asset is uncommon but still understandable from the transcript, return the best uppercase symbol or asset token instead of failing.
- amount and buyPrice must be positive numbers.
- If the transcript is unclear, return an empty operations array and explain briefly in message.
- Parse number words when reasonably confident.
- Keep the message concise and in the same language as the transcript when possible.
- Do not invent assets, prices, dates, or quantities.
""".strip()


def _build_audio_prompt(
    *,
    app_language_code: str,
    speech_locale_id: str | None,
) -> str:
    language = _coerce_text(app_language_code) or "en"
    locale_id = _coerce_text(speech_locale_id) or "unknown"
    return f"""
Transcribe the attached audio into plain text only.

App language code: {language}
Speech locale id: {locale_id}

Rules:
- Return plain text only.
- Do not return JSON.
- Do not explain anything.
- Do not add markdown or quotes.
- Preserve asset names, numbers, and prices as spoken as closely as possible.
""".strip()


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
