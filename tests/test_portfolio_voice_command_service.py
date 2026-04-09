from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from app.services.portfolio_voice_command_service import (
    PortfolioVoiceCommandService,
    PortfolioVoiceInterpretationError,
    PortfolioVoiceUnavailableError,
)


class PortfolioVoiceCommandServiceTests(IsolatedAsyncioTestCase):
    async def test_interpret_transcript_parses_simple_buy_command_locally(self) -> None:
        service = PortfolioVoiceCommandService()

        result = await service.interpret_transcript(
            SimpleNamespace(),
            transcript="0.3 eth 3200 dan oldim",
            app_language_code="uz",
            speech_locale_id="uz-UZ",
        )

        self.assertEqual(result.applyMode, "append")
        self.assertEqual(result.operations[0].symbol, "ETH")
        self.assertAlmostEqual(result.operations[0].amount, 0.3)
        self.assertAlmostEqual(result.operations[0].buyPrice, 3200.0)

    async def test_interpret_transcript_raises_for_unrecognized_command(self) -> None:
        service = PortfolioVoiceCommandService()

        with self.assertRaises(PortfolioVoiceInterpretationError):
            await service.interpret_transcript(
                SimpleNamespace(),
                transcript="ethdan uch ming ikki yuzdan ozroq oldim",
                app_language_code="uz",
                speech_locale_id="uz-UZ",
            )

    async def test_interpret_audio_is_disabled_without_gemini(self) -> None:
        service = PortfolioVoiceCommandService()

        with self.assertRaises(PortfolioVoiceUnavailableError):
            await service.interpret_audio(
                SimpleNamespace(),
                audio_bytes=b"123",
                mime_type="audio/mp4",
                filename="note.m4a",
                app_language_code="uz",
                speech_locale_id="uz-UZ",
            )
