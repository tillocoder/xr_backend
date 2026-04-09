from types import MethodType
from unittest import IsolatedAsyncioTestCase

from app.services.ai_provider_config_service import GeminiConfig
from app.services.gemini_service import GeminiClient, GeminiResult


class GeminiClientTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        GeminiClient._cooldowns.clear()
        GeminiClient._next_cooldown_warning_at = 0.0

    async def test_generate_parts_skips_configs_still_in_cooldown(self) -> None:
        primary = GeminiConfig(
            id=1,
            api_key="primary-key-123456789",
            model="gemini-test",
            label="Primary",
            sort_order=1,
            usage_scope="default",
        )
        secondary = GeminiConfig(
            id=2,
            api_key="secondary-key-987654321",
            model="gemini-test",
            label="Secondary",
            sort_order=2,
            usage_scope="default",
        )
        client = GeminiClient([primary, secondary])
        GeminiClient._mark_rate_limited(primary, cooldown_seconds=120)

        calls: list[str] = []

        async def fake_generate_once(self, config, **kwargs):
            del self, kwargs
            calls.append(config.label)
            return (
                GeminiResult(
                    text="ok",
                    model=config.model,
                    config_id=config.id,
                    config_label=config.label,
                ),
                False,
                None,
            )

        client._generate_once = MethodType(fake_generate_once, client)

        result = await client.generate_parts(parts=[{"text": "hello"}])

        self.assertIsNotNone(result)
        self.assertEqual(calls, ["Secondary"])

    async def test_generate_parts_does_not_call_provider_when_all_configs_are_cooling(self) -> None:
        config = GeminiConfig(
            id=1,
            api_key="primary-key-123456789",
            model="gemini-test",
            label="Primary",
            sort_order=1,
            usage_scope="default",
        )
        client = GeminiClient([config])
        GeminiClient._mark_rate_limited(config, cooldown_seconds=120)

        async def fake_generate_once(self, config, **kwargs):
            raise AssertionError(f"_generate_once should not be called for {config.label}")

        client._generate_once = MethodType(fake_generate_once, client)

        result = await client.generate_parts(parts=[{"text": "hello"}])

        self.assertIsNone(result)
