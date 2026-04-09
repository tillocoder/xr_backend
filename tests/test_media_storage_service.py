from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from app.services.media_storage import MediaStorageService


class _FakeR2Client:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []

    def put_object(self, **kwargs) -> None:
        self.put_calls.append(dict(kwargs))

    def upload_fileobj(self, **kwargs) -> None:
        fileobj = kwargs.get("Fileobj")
        if fileobj is not None:
            kwargs = dict(kwargs)
            kwargs["BodyLength"] = len(fileobj.read())
            fileobj.seek(0)
        self.put_calls.append(dict(kwargs))

    def delete_object(self, **kwargs) -> None:
        self.delete_calls.append(dict(kwargs))


class MediaStorageServiceTests(IsolatedAsyncioTestCase):
    async def test_save_bytes_uses_local_media_fallback_when_r2_is_not_configured(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            service = MediaStorageService(
                settings=_build_settings(),
                public_base_url="https://api.example.com",
                media_root=Path(tmp_dir),
            )

            stored = await service.save_bytes(
                b"voice-data",
                file_name="voice.m4a",
                category="chat_voice",
            )

            self.assertTrue(stored.path.startswith("/media/chat_voice/"))
            self.assertEqual(stored.url, f"https://api.example.com{stored.path}")
            target = Path(tmp_dir) / stored.path.removeprefix("/media/")
            self.assertTrue(target.exists())

            await service.delete(stored.path)
            self.assertFalse(target.exists())

    async def test_save_bytes_uses_r2_public_url_when_r2_is_configured(self) -> None:
        fake_client = _FakeR2Client()
        service = MediaStorageService(
            settings=_build_settings(
                r2_account_id="acc123",
                r2_access_key_id="key",
                r2_secret_access_key="secret",
                r2_bucket_name="xr-media",
                r2_public_base_url="https://assets.example.com",
            ),
            media_root=Path.cwd(),
            r2_client_factory=lambda: fake_client,
        )

        stored = await service.save_bytes(
            b"image-data",
            file_name="photo.png",
            category="community_posts",
        )

        self.assertEqual(stored.url, stored.path)
        self.assertTrue(stored.url.startswith("https://assets.example.com/community_posts/"))
        self.assertEqual(len(fake_client.put_calls), 1)
        self.assertEqual(fake_client.put_calls[0]["Bucket"], "xr-media")
        self.assertEqual(fake_client.put_calls[0]["Key"], stored.key)

    async def test_delete_extracts_r2_key_from_public_base_path_prefix(self) -> None:
        fake_client = _FakeR2Client()
        service = MediaStorageService(
            settings=_build_settings(
                r2_endpoint_url="https://acc123.r2.cloudflarestorage.com",
                r2_access_key_id="key",
                r2_secret_access_key="secret",
                r2_bucket_name="xr-media",
                r2_public_base_url="https://cdn.example.com/media",
            ),
            media_root=Path.cwd(),
            r2_client_factory=lambda: fake_client,
        )

        await service.delete("https://cdn.example.com/media/chat_voice/abc123.m4a")

        self.assertEqual(len(fake_client.delete_calls), 1)
        self.assertEqual(fake_client.delete_calls[0]["Bucket"], "xr-media")
        self.assertEqual(fake_client.delete_calls[0]["Key"], "chat_voice/abc123.m4a")


def _build_settings(**overrides):
    defaults = {
        "public_base_url": "",
        "r2_endpoint_url": "",
        "r2_account_id": "",
        "r2_access_key_id": "",
        "r2_secret_access_key": "",
        "r2_bucket_name": "",
        "r2_public_base_url": "",
        "r2_region": "auto",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)
