from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Sequence

import asyncio
import os

import httpx

from app.services.ai_provider_config_service import GeminiConfig, list_gemini_configs


@dataclass(frozen=True)
class GeminiResult:
    text: str
    model: str
    config_id: int | None
    config_label: str


class GeminiClient:
    _semaphore = asyncio.Semaphore(int(os.getenv("XR_GEMINI_MAX_CONCURRENCY", "2")))
    _cooldown_seconds = max(30, int(os.getenv("XR_GEMINI_KEY_COOLDOWN_SECONDS", "300")))
    _cooldowns: dict[str, float] = {}

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
    def _mark_rate_limited(cls, config: GeminiConfig) -> None:
        cls._cooldowns[cls._cache_key(config)] = monotonic() + cls._cooldown_seconds

    @classmethod
    def _clear_cooldown(cls, config: GeminiConfig) -> None:
        cls._cooldowns.pop(cls._cache_key(config), None)

    def _ordered_configs(self) -> list[GeminiConfig]:
        now = monotonic()
        ready: list[GeminiConfig] = []
        cooling: list[tuple[float, GeminiConfig]] = []

        for config in self._configs:
            cooldown_until = GeminiClient._cooldowns.get(self._cache_key(config), 0.0)
            if cooldown_until > now:
                cooling.append((cooldown_until, config))
            else:
                ready.append(config)

        ready.sort(key=lambda item: (item.sort_order, item.label.lower(), item.id or 0))
        cooling.sort(key=lambda item: (item[0], item[1].sort_order, item[1].id or 0))
        return [*ready, *(config for _until, config in cooling)]

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

    async def _generate_once(
        self,
        config: GeminiConfig,
        *,
        prompt: str,
        temperature: float,
        timeout_seconds: float,
    ) -> tuple[GeminiResult | None, bool]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{config.model}:generateContent?key={config.api_key}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature},
        }
        timeout = httpx.Timeout(connect=10, read=timeout_seconds, write=20, pool=20)
        limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)

        async with GeminiClient._semaphore:
            try:
                async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
                    response = await client.post(url, json=payload)
            except httpx.ReadTimeout:
                return None, False
            except httpx.ConnectTimeout:
                return None, False
            except httpx.TransportError:
                return None, False

        if self._is_rate_limited_response(response):
            return None, True
        if response.status_code in (403, 404):
            return None, False
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            return None, False

        try:
            data = response.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            out = (text or "").strip()
            if not out:
                return None, False
            return (
                GeminiResult(
                    text=out,
                    model=config.model,
                    config_id=config.id,
                    config_label=config.label,
                ),
                False,
            )
        except Exception:
            return None, False

    async def generate_text(
        self,
        *,
        prompt: str,
        temperature: float = 0.2,
        timeout_seconds: float = 35,
    ) -> GeminiResult | None:
        for config in self._ordered_configs():
            result, is_rate_limited = await self._generate_once(
                config,
                prompt=prompt,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
            )
            if result is not None:
                GeminiClient._clear_cooldown(config)
                return result
            if is_rate_limited:
                GeminiClient._mark_rate_limited(config)
        return None


async def build_gemini_client(db) -> GeminiClient | None:
    configs = await list_gemini_configs(db)
    if not configs:
        return None
    return GeminiClient(configs)
