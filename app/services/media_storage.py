from __future__ import annotations

import asyncio
import mimetypes
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from app.core.config import Settings, get_settings


@dataclass(frozen=True)
class StoredMedia:
    url: str
    path: str
    key: str


class MediaStorageService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        public_base_url: str | None = None,
        media_root: Path | None = None,
        r2_client_factory=None,
    ) -> None:
        self._settings = settings or get_settings()
        self._local_public_base_url = (
            public_base_url
            if public_base_url is not None
            else self._settings.public_base_url
        )
        self._local_public_base_url = self._normalize_base_url(self._local_public_base_url)
        self._r2_public_base_url = self._normalize_base_url(self._settings.r2_public_base_url)
        self._r2_endpoint_url = self._resolve_r2_endpoint_url(self._settings)
        self._media_root = media_root or Path(__file__).resolve().parents[2] / "media"
        self._r2_client_factory = r2_client_factory
        self._r2_client = None

    async def save_bytes(
        self,
        raw: bytes,
        *,
        file_name: str,
        category: str,
        content_type: str | None = None,
    ) -> StoredMedia:
        if not raw:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty.",
            )

        normalized_category = self._normalize_category(category)
        normalized_name = self._normalize_file_name(file_name, default_name="upload.bin")
        suffix = Path(normalized_name).suffix.lower()
        stored_name = f"{uuid4().hex}{suffix[:16]}"

        if self._is_r2_configured:
            key = f"{normalized_category}/{stored_name}"
            await asyncio.to_thread(
                self._put_r2_object,
                key,
                raw,
                self._detect_content_type(normalized_name, content_type),
            )
            public_url = self._build_r2_public_url(key)
            return StoredMedia(url=public_url, path=public_url, key=key)

        target_dir = self._media_root / normalized_category
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / stored_name
        target_path.write_bytes(raw)

        relative_path = f"/media/{normalized_category}/{stored_name}"
        public_url = self._build_local_public_url(relative_path)
        return StoredMedia(url=public_url, path=relative_path, key=relative_path)

    async def save_upload_file(
        self,
        file: UploadFile,
        *,
        category: str,
        default_file_name: str,
        max_bytes: int | None = None,
        too_large_detail: str = "Uploaded file is too large.",
        empty_detail: str = "Uploaded file is empty.",
    ) -> StoredMedia:
        normalized_category = self._normalize_category(category)
        normalized_name = self._normalize_file_name(
            file.filename or default_file_name,
            default_name=default_file_name,
        )
        suffix = Path(normalized_name).suffix.lower()
        stored_name = f"{uuid4().hex}{suffix[:16]}"
        spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
        written = 0

        try:
            await file.seek(0)
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if max_bytes is not None and written > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=too_large_detail,
                    )
                spool.write(chunk)

            if written <= 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=empty_detail,
                )

            spool.seek(0)
            if self._is_r2_configured:
                key = f"{normalized_category}/{stored_name}"
                await asyncio.to_thread(
                    self._upload_r2_fileobj,
                    key,
                    spool,
                    self._detect_content_type(normalized_name, file.content_type),
                )
                public_url = self._build_r2_public_url(key)
                return StoredMedia(url=public_url, path=public_url, key=key)

            target_dir = self._media_root / normalized_category
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / stored_name
            try:
                with target_path.open("wb") as buffer:
                    spool.seek(0)
                    shutil.copyfileobj(spool, buffer, length=1024 * 1024)
            except Exception:
                target_path.unlink(missing_ok=True)
                raise

            relative_path = f"/media/{normalized_category}/{stored_name}"
            public_url = self._build_local_public_url(relative_path)
            return StoredMedia(url=public_url, path=relative_path, key=relative_path)
        finally:
            spool.close()
            await file.close()

    async def delete(self, raw_reference: str | None) -> None:
        local_relative = self._extract_local_relative_path(raw_reference)
        if local_relative is not None:
            target = (self._media_root / local_relative).resolve()
            try:
                target.relative_to(self._media_root.resolve())
            except ValueError:
                target = None
            if target is not None:
                target.unlink(missing_ok=True)

        r2_key = self._extract_r2_key(raw_reference)
        if r2_key is not None and self._is_r2_configured:
            await asyncio.to_thread(self._delete_r2_object, r2_key)

    @property
    def _is_r2_configured(self) -> bool:
        return all(
            [
                self._r2_endpoint_url,
                self._settings.r2_access_key_id.strip(),
                self._settings.r2_secret_access_key.strip(),
                self._settings.r2_bucket_name.strip(),
                self._r2_public_base_url,
            ]
        )

    def _create_r2_client(self):
        if self._r2_client_factory is not None:
            return self._r2_client_factory()
        try:
            import boto3
            from botocore.config import Config
        except ImportError as error:
            raise RuntimeError("boto3 is required for Cloudflare R2 media storage.") from error

        return boto3.client(
            "s3",
            endpoint_url=self._r2_endpoint_url,
            aws_access_key_id=self._settings.r2_access_key_id.strip(),
            aws_secret_access_key=self._settings.r2_secret_access_key.strip(),
            region_name=self._settings.r2_region.strip() or "auto",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            ),
        )

    def _get_r2_client(self):
        if self._r2_client is None:
            self._r2_client = self._create_r2_client()
        return self._r2_client

    def _put_r2_object(self, key: str, raw: bytes, content_type: str) -> None:
        self._get_r2_client().put_object(
            Bucket=self._settings.r2_bucket_name.strip(),
            Key=key,
            Body=raw,
            ContentType=content_type,
        )

    def _upload_r2_fileobj(self, key: str, fileobj, content_type: str) -> None:
        fileobj.seek(0)
        self._get_r2_client().upload_fileobj(
            Fileobj=fileobj,
            Bucket=self._settings.r2_bucket_name.strip(),
            Key=key,
            ExtraArgs={"ContentType": content_type},
        )

    def _delete_r2_object(self, key: str) -> None:
        self._get_r2_client().delete_object(
            Bucket=self._settings.r2_bucket_name.strip(),
            Key=key,
        )

    def _build_r2_public_url(self, key: str) -> str:
        return f"{self._r2_public_base_url}/{key.lstrip('/')}"

    def _build_local_public_url(self, relative_path: str) -> str:
        if not self._local_public_base_url:
            return relative_path
        return f"{self._local_public_base_url}{relative_path}"

    def _detect_content_type(self, file_name: str, declared: str | None = None) -> str:
        normalized_declared = (declared or "").strip().lower()
        if normalized_declared:
            return normalized_declared
        guessed, _ = mimetypes.guess_type(file_name)
        return guessed or "application/octet-stream"

    def _extract_local_relative_path(self, raw_reference: str | None) -> str | None:
        raw = (raw_reference or "").strip()
        if not raw:
            return None
        if raw.startswith("/media/"):
            return raw.removeprefix("/media/").strip("/") or None
        marker = raw.find("/media/")
        if marker >= 0:
            return raw[marker + len("/media/") :].strip("/") or None
        return None

    def _extract_r2_key(self, raw_reference: str | None) -> str | None:
        raw = (raw_reference or "").strip()
        if not raw or not self._r2_public_base_url:
            return None

        parsed_base = urlparse(self._r2_public_base_url)
        parsed_raw = urlparse(raw)
        if parsed_raw.scheme and parsed_raw.netloc:
            if parsed_raw.netloc.lower() != parsed_base.netloc.lower():
                return None
            base_path = parsed_base.path.strip("/")
            raw_path = parsed_raw.path.strip("/")
            if base_path:
                if raw_path == base_path:
                    return None
                prefix = f"{base_path}/"
                if not raw_path.startswith(prefix):
                    return None
                raw_path = raw_path[len(prefix) :]
            return raw_path or None

        normalized = raw.strip("/")
        return normalized or None

    def _normalize_category(self, category: str) -> str:
        normalized = "/".join(
            part.strip()
            for part in str(category or "").replace("\\", "/").split("/")
            if part.strip()
        )
        return normalized or "uploads"

    def _normalize_file_name(self, file_name: str, *, default_name: str) -> str:
        normalized = Path((file_name or "").strip() or default_name).name
        return normalized or default_name

    def _normalize_base_url(self, raw: str | None) -> str:
        value = (raw or "").strip().rstrip("/")
        return value

    def _resolve_r2_endpoint_url(self, settings: Settings) -> str:
        explicit = self._normalize_base_url(settings.r2_endpoint_url)
        if explicit:
            return explicit
        account_id = settings.r2_account_id.strip()
        if not account_id:
            return ""
        return f"https://{account_id}.r2.cloudflarestorage.com"
