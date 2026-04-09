from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from app.services.portfolio_voice_command_service import (
    PortfolioVoiceCommandService,
    PortfolioVoiceInterpretationError,
)


class _FakeGeminiClient:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict[str, object]] = []

    async def generate_text(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text=self._text, model="gemini-test")


class PortfolioVoiceCommandServiceTests(IsolatedAsyncioTestCase):
    async def test_uses_portfolio_scope_and_requests_json_output(self) -> None:
        calls: list[dict[str, object]] = []
        gemini = _FakeGeminiClient(
            """
            {"message":"ETH qo'shildi","operations":[{"type":"buy","symbol":"eth","amount":"0.3","buyPrice":"3200"}]}
            """
        )

        async def fake_builder(db, **kwargs):
            del db
            calls.append(kwargs)
            return gemini

        service = PortfolioVoiceCommandService(gemini_client_builder=fake_builder)

        result = await service.interpret_transcript(
            SimpleNamespace(),
            transcript="0.3 eth 3200 dan oldim",
            app_language_code="uz",
            speech_locale_id="uz-UZ",
        )

        self.assertEqual(
            calls,
            [
                {
                    "usage_scope": "portfolio",
                    "fallback_scopes": ("default",),
                }
            ],
        )
        self.assertEqual(len(gemini.calls), 1)
        self.assertEqual(gemini.calls[0]["temperature"], 0.0)
        self.assertEqual(gemini.calls[0]["timeout_seconds"], 18)
        self.assertEqual(gemini.calls[0]["response_mime_type"], "application/json")
        self.assertEqual(gemini.calls[0]["max_output_tokens"], 256)
        self.assertEqual(result.applyMode, "append")
        self.assertEqual(result.operations[0].symbol, "ETH")
        self.assertAlmostEqual(result.operations[0].amount, 0.3)
        self.assertAlmostEqual(result.operations[0].buyPrice, 3200.0)
        self.assertEqual(result.message, "ETH qo'shildi")

    async def test_uses_portfolio_scope_when_gemini_fallback_is_needed(self) -> None:
        calls: list[dict[str, object]] = []

        async def fake_builder(db, **kwargs):
            del db
            calls.append(kwargs)
            return _FakeGeminiClient(
                """
                ```json
                {"message":"ETH qo'shildi","operations":[{"type":"buy","symbol":"ethereum","amount":"0.3","buyPrice":"3200"}]}
                ```
                """
            )

        service = PortfolioVoiceCommandService(gemini_client_builder=fake_builder)

        result = await service.interpret_transcript(
            SimpleNamespace(),
            transcript="ethdan uch ming ikki yuzdan ozroq oldim",
            app_language_code="uz",
            speech_locale_id="uz-UZ",
        )

        self.assertEqual(
            calls,
            [
                {
                    "usage_scope": "portfolio",
                    "fallback_scopes": ("default",),
                }
            ],
        )
        self.assertEqual(result.applyMode, "append")
        self.assertEqual(result.operations[0].symbol, "ETH")
        self.assertAlmostEqual(result.operations[0].amount, 0.3)
        self.assertAlmostEqual(result.operations[0].buyPrice, 3200.0)
        self.assertEqual(result.message, "ETH qo'shildi")

    async def test_raises_when_gemini_returns_empty_operations(self) -> None:
        async def fake_builder(db, **kwargs):
            del db, kwargs
            return _FakeGeminiClient('{"message":"Tushunmadim","operations":[]}')

        service = PortfolioVoiceCommandService(gemini_client_builder=fake_builder)

        with self.assertRaises(PortfolioVoiceInterpretationError):
            await service.interpret_transcript(
                SimpleNamespace(),
                transcript="nimadir",
                app_language_code="uz",
                speech_locale_id=None,
            )
