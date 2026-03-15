from __future__ import annotations

from dataclasses import dataclass

import asyncio
import os

import httpx

from app.services.ai_provider_config_service import GeminiConfig


@dataclass(frozen=True)
class GeminiResult:
    text: str
    model: str


class GeminiClient:
    _semaphore = asyncio.Semaphore(int(os.getenv("XR_GEMINI_MAX_CONCURRENCY", "2")))

    def __init__(self, config: GeminiConfig):
        self._api_key = config.api_key
        self._model = config.model

    @property
    def model(self) -> str:
        return self._model

    async def generate_text(
        self,
        *,
        prompt: str,
        temperature: float = 0.2,
        timeout_seconds: float = 35,
    ) -> GeminiResult | None:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:generateContent?key={self._api_key}"
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
                    r = await client.post(url, json=payload)
            except httpx.ReadTimeout:
                return None
            except httpx.ConnectTimeout:
                return None
            except httpx.TransportError:
                return None

        if r.status_code == 429:
            return None
        if r.status_code in (403, 404):
            return None
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError:
            return None

        try:
            j = r.json()
            text = j["candidates"][0]["content"]["parts"][0]["text"]
            out = (text or "").strip()
            if not out:
                return None
            return GeminiResult(text=out, model=self._model)
        except Exception:
            return None
