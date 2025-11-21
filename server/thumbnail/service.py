"""Thumbnail cache and placeholder generation service."""

from __future__ import annotations

import base64
import logging
import os
import re
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional

import httpx

try:  # Pillow is optional but recommended for PNG→JPEG conversion
    from PIL import Image  # type: ignore
except ImportError:  # pragma: no cover - conversion unavailable in minimal env
    Image = None


PLACEHOLDER_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAKAAAABaCAIAAACwpMoFAAAA7UlEQVR4nO3RQQkA"
    "IQAAwfNS+DSK/VOZQoRlJsHCjrn2R9f/OoC7DI4zOM7gOIPjDI4zOM7gOIPjDI4z"
    "OM7gOIPjDI4zOM7gOIPjDI4zOM7gOIPjDI4zOM7gOIPjDI4zOM7gOIPjDI4zOM7g"
    "OIPjDI4zOM7gOIPjDI4zOM7gOIPjDI4zOM7gOIPjDI4zOM7gOIPjDI4zOM7gOIPj"
    "DI4zOM7gOIPjDI4zOM7gOIPjDI4zOM7gOIPjDI4zOM7gOIPjDI4zOM7gOIPjDI4z"
    "OM7gOIPjDI4zOM7gOIPjDI4zOM7gOIPjDmd7ASyc3BfmAAAAAElFTkSuQmCC"
)

PLACEHOLDER_JPEG_BASE64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8L"
    "CwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUF"
    "BQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4e"
    "Hh4eHh4eHh4eHh4eHh4eHh7/wAARCABaAKADASIAAhEBAxEB/8QAHwAAAQUBAQEB"
    "AQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQR"
    "BRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3"
    "ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWW"
    "l5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo"
    "6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QA"
    "tREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMz"
    "UvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVm"
    "Z2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6"
    "wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEA"
    "PwD5nooor2zkCiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiii"
    "gAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiii"
    "gAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiii"
    "gAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiii"
    "gAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiii"
    "gAooooA//9k="
)

SUPPORTED_FORMATS = {"png", "jpg", "jpeg"}
SAFE_LOGIN_PATTERN = re.compile(r"[^a-z0-9_]+")


class ThumbnailError(Exception):
    """Base error type for thumbnail generation issues."""


class UnsupportedFormatError(ThumbnailError):
    """Raised when a requested thumbnail format is not supported."""


@dataclass(frozen=True)
class ThumbnailResult:
    """Information about a cached thumbnail asset."""

    path: Path
    regenerated: bool
    source: str  # cache, remote_preview, placeholder, profile


class ThumbnailService:
    """Handle thumbnail caching and placeholder generation."""

    REMOTE_PREVIEW_TEMPLATE = "live_user_{login}-160x90.jpg"

    def __init__(
        self,
        cache_dir: Path,
        ttl_seconds: int = 600,
        *,
        enable_remote_fetch: bool = True,
        remote_base_url: str = "https://static-cdn.jtvnw.net/previews-ttv",
        http_timeout: float = 2.0,
        target_width: int = 160,
        target_height: int = 90,
    ) -> None:
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._placeholder_assets = {
            "png": base64.b64decode(PLACEHOLDER_PNG_BASE64),
            "jpg": base64.b64decode(PLACEHOLDER_JPEG_BASE64),
        }
        self._enable_remote_fetch = enable_remote_fetch
        self._remote_base_url = remote_base_url.rstrip("/")
        self._http_timeout = http_timeout
        self._logger = logging.getLogger(__name__)
        self._target_width = target_width
        self._target_height = target_height

    def get_thumbnail(self, login: str, fmt: str = "png", force: bool = False) -> ThumbnailResult:
        """Return a cached thumbnail path, generating it when necessary."""
        sanitized_login = self._sanitize_login(login)
        fmt = fmt.lower()
        if fmt == "jpeg":
            fmt = "jpg"
        if fmt not in SUPPORTED_FORMATS:
            raise UnsupportedFormatError(f"Format '{fmt}' wird noch nicht unterstützt.")

        target_path = self.cache_dir / f"{sanitized_login}.{fmt}"
        fresh = target_path.exists() and self._is_fresh(target_path)

        if not force and fresh:
            return ThumbnailResult(path=target_path, regenerated=False, source="cache")

        if fmt == "jpg":
            remote_path = self._write_remote_thumbnail(target_path, sanitized_login)
            if remote_path:
                return ThumbnailResult(path=remote_path, regenerated=True, source="remote_preview")

        placeholder_path = self._write_placeholder(target_path, fmt)
        return ThumbnailResult(path=placeholder_path, regenerated=True, source="placeholder")

    def generate_placeholder(self, login: str, fmt: str) -> ThumbnailResult:
        """Force regeneration of the placeholder image."""
        sanitized_login = self._sanitize_login(login)
        fmt = fmt.lower()
        target_path = self.cache_dir / f"{sanitized_login}.{fmt}"
        placeholder_path = self._write_placeholder(target_path, fmt)
        return ThumbnailResult(path=placeholder_path, regenerated=True, source="placeholder")

    def _sanitize_login(self, login: str) -> str:
        if not login:
            raise ThumbnailError("Login darf nicht leer sein.")
        login_lower = login.lower()
        sanitized = SAFE_LOGIN_PATTERN.sub("-", login_lower).strip("-")
        if not sanitized:
            raise ThumbnailError("Login enthält keine gültigen Zeichen.")
        return sanitized

    def _is_fresh(self, path: Path) -> bool:
        if self.ttl_seconds <= 0:
            return True
        age = time.time() - path.stat().st_mtime
        return age <= self.ttl_seconds

    def store_external_thumbnail(
        self,
        login: str,
        fmt: str,
        data: bytes,
        *,
        content_type: Optional[str] = None,
        source: str,
    ) -> Optional[ThumbnailResult]:
        """Persist externally provided thumbnail bytes."""
        sanitized_login = self._sanitize_login(login)
        fmt = fmt.lower()
        processed = self._prepare_external_bytes(data, fmt, content_type)
        if processed is None:
            return None
        target_path = self.cache_dir / f"{sanitized_login}.{fmt}"
        try:
            target_path.write_bytes(processed)
            os.utime(target_path, None)
        except OSError as exc:  # pragma: no cover - filesystem error
            self._logger.warning("Konnte externes Vorschaubild nicht speichern: %s", exc)
            raise
        return ThumbnailResult(path=target_path, regenerated=True, source=source)

    def _write_placeholder(self, path: Path, fmt: str) -> Path:
        data = self._placeholder_assets.get(fmt)
        if not data:
            raise UnsupportedFormatError(f"Kein Platzhalter für Format '{fmt}'.")
        path.write_bytes(data)
        os.utime(path, None)
        return path

    def _write_remote_thumbnail(self, path: Path, login: str) -> Optional[Path]:
        """Attempt to download the live preview from Twitch's CDN."""
        if not self._enable_remote_fetch:
            return None

        preview_bytes = self._download_remote_preview(login)
        if not preview_bytes:
            return None

        try:
            path.write_bytes(preview_bytes)
            os.utime(path, None)
        except OSError as exc:  # pragma: no cover - filesystem error
            self._logger.warning("Konnte Vorschaubild nicht speichern: %s", exc)
            return None
        return path

    def _download_remote_preview(self, login: str) -> Optional[bytes]:
        url = f"{self._remote_base_url}/{self.REMOTE_PREVIEW_TEMPLATE.format(login=login)}"
        try:
            response = httpx.get(url, timeout=self._http_timeout)
        except httpx.RequestError as exc:
            self._logger.debug("Twitch Vorschaubild Download fehlgeschlagen (%s): %s", login, exc)
            return None

        if response.status_code != 200:
            self._logger.debug(
                "Twitch Vorschaubild nicht verfügbar (%s): HTTP %s", login, response.status_code
            )
            return None

        content_type = response.headers.get("content-type", "")
        if "image" not in content_type:
            self._logger.debug(
                "Twitch Vorschaubild ungültiger Typ (%s): %s", login, content_type
            )
            return None

        if not response.content:
            self._logger.debug("Twitch Vorschaubild leer (%s).", login)
            return None

        return response.content

    def _prepare_external_bytes(
        self,
        data: bytes,
        fmt: str,
        content_type: Optional[str],
    ) -> Optional[bytes]:
        if Image is None:
            self._logger.debug("Pillow nicht verfügbar – keine Skalierung möglich.")
            return data

        try:
            with Image.open(BytesIO(data)) as img:
                image = img.convert("RGB")
        except Exception as exc:  # pragma: no cover - decoding error
            self._logger.warning("Vorschaubild konnte nicht geöffnet werden: %s", exc)
            return None

        processed = self._letterbox_image(image, fmt)
        return processed

    def _letterbox_image(self, image: "Image.Image", fmt: str) -> Optional[bytes]:
        if self._target_width <= 0 or self._target_height <= 0:
            self._logger.debug("Ungültige Zielgröße für Vorschaubilder.")
            return None

        src_w, src_h = image.size
        if src_w <= 0 or src_h <= 0:
            self._logger.debug("Vorschaubild hat ungültige Dimensionen (%s x %s).", src_w, src_h)
            return None

        scale = min(self._target_width / src_w, self._target_height / src_h)
        if scale <= 0:
            return None

        new_w = max(1, int(round(src_w * scale)))
        new_h = max(1, int(round(src_h * scale)))

        resized = image.resize((new_w, new_h), Image.LANCZOS)
        canvas = Image.new("RGB", (self._target_width, self._target_height), color=(0, 0, 0))
        offset = ((self._target_width - new_w) // 2, (self._target_height - new_h) // 2)
        canvas.paste(resized, offset)

        output = BytesIO()
        if fmt == "png":
            canvas.save(output, format="PNG")
        else:
            canvas.save(output, format="JPEG", quality=85)
        return output.getvalue()
