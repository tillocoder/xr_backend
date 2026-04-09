from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Sequence

import asyncio
import base64
import logging
import os
import re

import httpx

from app.services.ai_provider_config_service import GeminiConfig, list_gemini_configs


@dataclass(frozen=True)
class GeminiResult:
    text: str
    model: str
    config_id: int | None
    config_label: str


LOGGER = logging.getLogger(__name__)


class GeminiClient:
    _semaphore = asyncio.Semaphore(int(os.getenv("XR_GEMINI_MAX_CONCURRENCY", "2")))
    _cooldown_seconds = max(30, int(os.getenv("XR_GEMINI_KEY_COOLDOWN_SECONDS", "300")))
    _cooldowns: dict[str, float] = {}
    _next_cooldown_warning_at = 0.0
    _cooldown_warning_interval_seconds = max(30, min(_cooldown_seconds, 300))

    def __init__(self, configs: GeminiConfig | Sequence[GeminiConfig]):
        if isinstance(configs, GeminiConfig):
            config_list = [configs]
        else:
            config_list = list(configs)
        self._configs = [cfg for cfg in config_list if str(cfg.api_key or "").strip()]

    @property
    def model(self) -> str:
        if not self._configs:
            return ""
        return self._configs[0].model

    @classmethod
    def _cache_key(cls, config: GeminiConfig) -> str:
        return f"{config.id or 0}:{config.api_key[-12:]}"

    @classmethod
    def _mark_rate_limited(cls, config: GeminiConfig, *, cooldown_seconds: float | None = None) -> None:
        cooldown_for = max(1.0, float(cooldown_seconds or cls._cooldown_seconds))
        cls._cooldowns[cls._cache_key(config)] = monotonic() + cooldown_for

    @classmethod
    def _clear_cooldown(cls, config: GeminiConfig) -> None:
        cls._cooldowns.pop(cls._cache_key(config), None)

    def _ready_configs(self) -> list[GeminiConfig]:
        now = monotonic()
        ready: list[GeminiConfig] = []

        for config in self._configs:
            cooldown_until = GeminiClient._cooldowns.get(self._cache_key(config), 0.0)
            if cooldown_until <= now:
                ready.append(config)

        ready.sort(key=lambda item: (item.sort_order, item.label.lower(), item.id or 0))
        return ready

    def _next_ready_after_seconds(self) -> float | None:
        now = monotonic()
        waits = [
            max(0.0, cooldown_until - now)
            for config in self._configs
            if (cooldown_until := GeminiClient._cooldowns.get(self._cache_key(config), 0.0)) > now
        ]
        if not waits:
            return None
        return min(waits)

    @classmethod
    def _warn_all_configs_cooling_down(cls, *, retry_after_seconds: float) -> None:
        now = monotonic()
        if now < cls._next_cooldown_warning_at:
            return
        cls._next_cooldown_warning_at = now + cls._cooldown_warning_interval_seconds
        LOGGER.warning(
            "gemini_all_configs_cooling_down retry_after_seconds=%s",
            round(retry_after_seconds, 2),
        )

    def _is_rate_limited_response(self, response: httpx.Response) -> bool:
        if response.status_code == 429:
            return True
        if response.status_code != 403:
            return False

        body = (response.text or "").lower()
        return any(
            marker in body
            for marker in (
                "quota",
                "rate limit",
                "resource_exhausted",
                "resource has been exhausted",
                "too many requests",
            )
        )

    def _cooldown_seconds_from_response(self, response: httpx.Response) -> float:
        retry_after = str(response.headers.get("retry-after") or "").strip()
        if retry_after:
            try:
                return max(1.0, float(retry_after))
            except ValueError:
                pass

        body = response.text or ""
        for pattern in (
            r"retry after\s+(\d+)",
            r"in\s+(\d+)\s+seconds",
        ):
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                try:
                    return max(1.0, float(match.group(1)))
                except ValueError:
                    continue
        return float(self._cooldown_seconds)

    async def _generate_once(
        self,
        config: GeminiConfig,
        *,
        parts: list[dict[str, object]],
        temperature: float,
        timeout_seconds: float,
        response_mime_type: str | None,
        max_output_tokens: int | None,
    ) -> tuple[GeminiResult | None, bool, float | None]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{config.model}:generateContent?key={config.api_key}"
        )
        generation_config: dict[str, object] = {"temperature": temperature}
        if response_mime_type:
            generation_config["responseMimeType"] = response_mime_type
        if max_output_tokens is not None:
            generation_config["maxOutputTokens"] = max_output_tokens
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": generation_config,
        }
        timeout = httpx.Timeout(connect=10, read=timeout_seconds, write=20, pool=20)
        limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)

        async with GeminiClient._semaphore:
            try:
                async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
                    response = await client.post(url, json=payload)
            except httpx.ReadTimeout:
                LOGGER.warning(
                    "gemini_request_timeout config_label=%s model=%s timeout_seconds=%s",
                    config.label,
                    config.model,
                    timeout_seconds,
                )
                return None, False, None
            except httpx.ConnectTimeout:
                LOGGER.warning(
                    "gemini_connect_timeout config_label=%s model=%s",
                    config.label,
                    config.model,
                )
                return None, False, None
            except httpx.TransportError as exc:
                LOGGER.warning(
                    "gemini_transport_error config_label=%s model=%s detail=%s",
                    config.label,
                    config.model,
                    str(exc),
                )
                return None, False, None

        if self._is_rate_limited_response(response):
            cooldown_seconds = self._cooldown_seconds_from_response(response)
            LOGGER.warning(
                "gemini_rate_limited config_label=%s model=%s status=%s cooldown_seconds=%s body=%s",
                config.label,
                config.model,
                response.status_code,
                round(cooldown_seconds, 2),
                (response.text or "").replace("\n", " ")[:400],
            )
            return None, True, cooldown_seconds
        if response.status_code in (403, 404):
            LOGGER.warning(
                "gemini_request_rejected config_label=%s model=%s status=%s body=%s",
                config.label,
                config.model,
                response.status_code,
                (response.text or "").replace("\n", " ")[:400],
            )
            return None, False, None
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            LOGGER.warning(
                "gemini_request_failed config_label=%s model=%s status=%s body=%s",
                config.label,
                config.model,
                response.status_code,
                (response.text or "").replace("\n", " ")[:400],
            )
            return None, False, None

        try:
            data = response.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            out = (text or "").strip()
            if not out:
                return None, False, None
            return (
                GeminiResult(
                    text=out,
                    model=config.model,
                    config_id=config.id,
                    config_label=config.label,
                ),
                False,
                None,
            )
        except Exception as exc:
            LOGGER.warning(
                "gemini_response_parse_failed config_label=%s model=%s detail=%s body=%s",
                config.label,
                config.model,
                str(exc),
                (response.text or "").replace("\n", " ")[:400],
            )
            return None, False, None

    async def generate_text(
        self,
        *,
        prompt: str,
        temperature: float = 0.2,
        timeout_seconds: float = 35,
        response_mime_type: str | None = None,
        max_output_tokens: int | None = None,
    ) -> GeminiResult | None:
        return await self.generate_parts(
            parts=[{"text": prompt}],
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            response_mime_type=response_mime_type,
            max_output_tokens=max_output_tokens,
        )

    async def generate_parts(
        self,
        *,
        parts: list[dict[str, object]],
        temperature: float = 0.2,
        timeout_seconds: float = 35,
        response_mime_type: str | None = None,
        max_output_tokens: int | None = None,
    ) -> GeminiResult | None:
        ready_configs = self._ready_configs()
        if not ready_configs:
            retry_after_seconds = self._next_ready_after_seconds()
            if retry_after_seconds is not None:
                self._warn_all_configs_cooling_down(retry_after_seconds=retry_after_seconds)
            return None

        for config in ready_configs:
            result, is_rate_limited, cooldown_seconds = await self._generate_once(
                config,
                parts=parts,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
                response_mime_type=response_mime_type,
                max_output_tokens=max_output_tokens,
            )
            if result is not None:
                GeminiClient._clear_cooldown(config)
                return result
            if is_rate_limited:
                GeminiClient._mark_rate_limited(config, cooldown_seconds=cooldown_seconds)
        return None

    async def generate_audio_text(
        self,
        *,
        prompt: str,
        audio_bytes: bytes,
        mime_type: str,
        temperature: float = 0.0,
        timeout_seconds: float = 45,
        response_mime_type: str | None = None,
        max_output_tokens: int | None = None,
    ) -> GeminiResult | None:
        encoded_audio = base64.b64encode(audio_bytes).decode("ascii")
        return await self.generate_parts(
            parts=[
                {"text": prompt},
                {"inlineData": {"mimeType": mime_type, "data": encoded_audio}},
            ],
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            response_mime_type=response_mime_type,
            max_output_tokens=max_output_tokens,
        )


async def build_gemini_client(
    db,
    *,
    usage_scope: str = "default",
    fallback_scopes: tuple[str, ...] = (),
) -> GeminiClient | None:
    configs = await list_gemini_configs(
        db,
        usage_scope=usage_scope,
        fallback_scopes=fallback_scopes,
    )
    if not configs:
        return None
    return GeminiClient(configs)
