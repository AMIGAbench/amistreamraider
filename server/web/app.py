"""Primary FastAPI application for the Amiga Twitch GUI backend.

The HTTP layer exposes endpoints that the Amiga client consumes. Each
endpoint can emit JSON (default) or the Amiga key/value flatfile format via
`?format=kv`. Response formatting helpers live in ``server.http.utils``.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

import httpx
from fastapi import Body, Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from server.api.auth import (
    get_access_token,
    get_refresh_token,
    refresh_access_token,
    save_auth_data,
)
from server.api.categories import CategoryService
from server.api.search import SearchService
from server.api.stubs import OfflineTwitchService, get_stub_categories
from server.api.twitch_client import TwitchAuthError, TwitchClient, TwitchRequestError
from server.web.utils import format_response
from server.thumbnail.service import (
    ThumbnailError,
    ThumbnailService,
    UnsupportedFormatError,
)
from server.transcode.manager import PipelineMetrics, PipelineStatus, TranscodeManager
from server.transcode.profiles import load_profiles
from server.transcode.stub import StubTranscodeManager


APP_TITLE = "Amiga Twitch GUI Backend"
APP_DESCRIPTION = (
    "Backend HTTP API providing Twitch data, thumbnail services, and "
    "transcoding control for the Amiga client."
)

ROOT_DIR = Path(__file__).resolve().parents[2]
PROFILES_DIR = ROOT_DIR / "server" / "profiles"
THUMB_CACHE_DIR = Path(
    os.getenv("THUMB_CACHE_DIR", ROOT_DIR / "server" / "thumbnail" / "cache")
)
THUMB_CACHE_TTL = int(os.getenv("THUMB_CACHE_TTL", "600"))
USE_OFFLINE_STUBS = os.getenv("TWITCH_USE_STUBS", "0") != "0"
THUMB_REMOTE_BASE_URL = os.getenv(
    "THUMBNAIL_REMOTE_BASE_URL", "https://static-cdn.jtvnw.net/previews-ttv"
)
THUMB_HTTP_TIMEOUT = float(os.getenv("THUMBNAIL_HTTP_TIMEOUT", "2.0"))
DEFAULT_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID") or "jyyezrdar235ncprwpchuh49y2vb4w"
PUBLIC_CLIENT = os.getenv("PUBLIC_CLIENT", "1") == "1"
AUTH_STORE = Path(os.getenv("AUTH_STORE", ROOT_DIR / "data" / "auth.json"))
AUTH_SCOPES = os.getenv("AUTH_SCOPES", "").split() if os.getenv("AUTH_SCOPES") else []
DEFAULT_FAVORITES = [
    login.strip()
    for login in os.getenv("TWITCH_FAVORITES", "").split(",")
    if login.strip()
]
TRANSCODE_USE_STUBS = os.getenv("TRANSCODE_USE_STUBS", "0") != "0"
TRANSCODE_WORK_DIR = Path(os.getenv("TRANSCODE_WORK_DIR", ROOT_DIR / "server" / "transcode" / "runs"))
STREAMLINK_PATH = os.getenv("STREAMLINK_PATH", "streamlink")
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
MBUFFER_PATH = os.getenv("MBUFFER_PATH", "mbuffer")
CLIENT_ID_REQUIRED_MSG = "Twitch-Client-Id erforderlich (Header X-Twitch-Client-Id oder TWITCH_CLIENT_ID)."

logger = logging.getLogger(__name__)

app = FastAPI(
    title=APP_TITLE,
    description=APP_DESCRIPTION,
    version="0.1.0",
)

twitch_client = TwitchClient()
search_service = SearchService(twitch_client)
offline_service = OfflineTwitchService()
category_service = CategoryService(twitch_client)
thumbnail_service = ThumbnailService(
    THUMB_CACHE_DIR,
    ttl_seconds=THUMB_CACHE_TTL,
    enable_remote_fetch=not USE_OFFLINE_STUBS,
    remote_base_url=THUMB_REMOTE_BASE_URL,
    http_timeout=THUMB_HTTP_TIMEOUT,
)

_loaded_profiles = load_profiles(PROFILES_DIR)
if TRANSCODE_USE_STUBS:
    transcode_manager: StubTranscodeManager | TranscodeManager = StubTranscodeManager()
else:
    if not _loaded_profiles:
        raise RuntimeError("No transcoding profiles available in server/profiles")
    transcode_manager = TranscodeManager(
        work_dir=TRANSCODE_WORK_DIR,
        profiles=_loaded_profiles,
        streamlink_path=STREAMLINK_PATH,
        ffmpeg_path=FFMPEG_PATH,
        mbuffer_path=MBUFFER_PATH,
    )


@app.on_event("startup")
async def _auto_refresh_token_on_startup() -> None:
    """Ensure stored device-flow token is refreshed when the server boots."""
    if not PUBLIC_CLIENT:
        return
    if not DEFAULT_CLIENT_ID:
        logger.debug("Kein Default Client ID gesetzt – überspringe Auto-Refresh.")
        return
    if get_access_token():
        return  # Token noch gültig
    refresh_token = get_refresh_token()
    if not refresh_token:
        logger.info("Kein Refresh Token vorhanden – Auto-Refresh wird übersprungen.")
        return
    success = await refresh_access_token(DEFAULT_CLIENT_ID)
    if success:
        logger.info("Twitch Access Token beim Start automatisch erneuert.")
    else:
        logger.warning("Automatischer Token-Refresh beim Start fehlgeschlagen.")


async def _download_image(url: str, timeout: float) -> Optional[tuple[bytes, str]]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
    except httpx.RequestError as exc:
        logger.debug("Download fehlgeschlagen (%s): %s", url, exc)
        return None

    if response.status_code != 200:
        logger.debug("Download HTTP %s (%s)", response.status_code, url)
        return None

    content_type = response.headers.get("content-type", "")
    if "image" not in content_type:
        logger.debug("Download kein Bild (%s): %s", url, content_type)
        return None

    if not response.content:
        logger.debug("Download leer (%s)", url)
        return None

    return response.content, content_type


async def _resolve_user_state(
    login: str,
    client_id: str,
) -> tuple[Optional[dict], Optional[bool]]:
    try:
        users_payload = await twitch_client.get_users(
            logins=[login],
            client_id=client_id,
        )
    except (TwitchAuthError, TwitchRequestError) as exc:
        logger.debug("Twitch get_users fehlgeschlagen (%s): %s", login, exc)
        return None, None

    data = users_payload.get("data", [])
    if not data:
        return None, None

    user = data[0]
    user_id = user.get("id")
    is_live: Optional[bool] = None
    if user_id:
        try:
            streams_payload = await twitch_client.get_streams(
                user_ids=[user_id],
                client_id=client_id,
            )
            is_live = bool(streams_payload.get("data"))
        except (TwitchAuthError, TwitchRequestError) as exc:
            logger.debug("Twitch get_streams fehlgeschlagen (%s): %s", login, exc)
    return user, is_live


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def get_search_service() -> SearchService | OfflineTwitchService:
    return offline_service if USE_OFFLINE_STUBS else search_service


def get_category_service() -> CategoryService:
    return category_service


def get_client_id(
    twitch_client_id: str | None = Header(None, alias="X-Twitch-Client-Id"),
) -> str:
    if PUBLIC_CLIENT:
        return DEFAULT_CLIENT_ID
    return twitch_client_id or DEFAULT_CLIENT_ID


def _append_auth_messages(messages: list[str], include_auth: bool) -> None:
    """Append standard authentication status messages when required."""
    if include_auth:
        messages.append("Authentifiziere bei Twitch…")


def _append_auth_success(messages: list[str], include_auth: bool) -> None:
    if include_auth:
        messages.append("Authentifizierung erfolgreich.")


class PlayStartRequest(BaseModel):
    channel: str
    profile: str = "aga_low"


class PlayStopRequest(BaseModel):
    op_id: str


class StrategyRequest(BaseModel):
    strategy: str


def _pipeline_payload(status: PipelineStatus) -> Dict[str, object]:
    return {
        "op_id": status.op_id,
        "channel": status.channel,
        "profile": status.profile,
        "state": status.state,
        "started_at": status.started_at,
        "ended_at": status.ended_at,
        "listen_host": status.listen_host,
        "listen_port": status.listen_port,
        "outfile": str(status.outfile),
        "log_file": str(status.log_file),
        "restart_count": status.restart_count,
        "last_error": status.last_error,
    }


def _metrics_payload(metrics: PipelineMetrics) -> Dict[str, object]:
    return {
        "op_id": metrics.op_id,
        "bytes_total": metrics.bytes_total,
        "out_time_ms": metrics.out_time_ms,
        "bitrate_kbps": metrics.bitrate_kbps,
        "updated_at": metrics.updated_at,
    }


@app.get("/health")
def health(format: str = Query("json", pattern="^(json|kv)$")):
    """Simple readiness probe used by orchestrators and CLI tooling."""
    payload = {"status": "ok", "message": "Backend bereit."}
    return format_response(payload, format)

@app.get("/auth/me")
async def auth_me(
    format: str = Query("json", pattern="^(json|kv)$"),
    client_id: str = Depends(get_client_id),
) -> object:
    """Return information about the currently authenticated user (via device auth).

    Requires PUBLIC_CLIENT=1 and a valid user access token. Returns a compact
    subset of the Helix /users response for convenience.
    """
    if PUBLIC_CLIENT:
        if not get_access_token():
            # Return 200 with a machine-friendly status so clients don't treat it as a hard error
            payload = {"status": "auth_required", "message": "User authentication required"}
            return format_response(payload, format)
    else:
        if not client_id:
            payload = {"status": "error", "message": CLIENT_ID_REQUIRED_MSG}
            return format_response(payload, format, status_code=401)

    try:
        me_payload = await twitch_client.get_users(client_id=client_id)  # type: ignore[arg-type]
    except (TwitchAuthError, TwitchRequestError) as exc:
        payload = {"status": "error", "message": str(exc)}
        return format_response(payload, format, status_code=502)

    data = me_payload.get("data", [])
    if not data:
        payload = {"status": "error", "message": "No user bound to token"}
        return format_response(payload, format, status_code=404)

    u = data[0]
    # Compact mapping (top-level mirrors for easy KV parsing)
    user_info = {
        "id": u.get("id"),
        "login": u.get("login"),
        "display_name": u.get("display_name"),
        "type": u.get("type"),
        "broadcaster_type": u.get("broadcaster_type"),
        "description": u.get("description", ""),
        "profile_image_url": u.get("profile_image_url"),
        "created_at": u.get("created_at"),
    }
    payload = {
        "status": "ok",
        "user": user_info,
        "id": user_info["id"],
        "login": user_info["login"],
        "display_name": user_info["display_name"],
    }
    return format_response(payload, format)

@app.post("/auth/reset")
def auth_reset(format: str = Query("json", pattern="^(json|kv)$")):
    """Reset stored device-auth tokens (for PUBLIC_CLIENT flows).

    Always returns 200 with a simple status so clients can call it safely
    before starting the auth flow to avoid stale credentials.
    """
    try:
        if AUTH_STORE.exists():
            AUTH_STORE.unlink(missing_ok=True)  # type: ignore[call-arg]
        # Some Python versions don't support missing_ok; fallback below
    except TypeError:
        try:
            if AUTH_STORE.exists():
                AUTH_STORE.unlink()
        except FileNotFoundError:
            pass
    except Exception as exc:  # pragma: no cover - non-critical
        payload = {"status": "error", "message": str(exc)}
        return format_response(payload, format, status_code=200)
    payload = {"status": "ok", "message": "Authentication state reset"}
    return format_response(payload, format)


@app.post("/auth/device/start")
async def auth_device_start(
    format: str = Query("json", pattern="^(json|kv)$"),
):
    """Start Device Code Flow for user authentication."""
    if not PUBLIC_CLIENT:
        payload = {"status": "error", "message": "Public client not enabled"}
        return format_response(payload, format, status_code=400)
    if not DEFAULT_CLIENT_ID:
        payload = {"status": "error", "message": "Client ID not configured"}
        return format_response(payload, format, status_code=400)

    scopes = AUTH_SCOPES or []
    data = {
        "client_id": DEFAULT_CLIENT_ID,
        "scope": " ".join(scopes),
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post("https://id.twitch.tv/oauth2/device", data=data)
            response.raise_for_status()
            result = response.json()
    except Exception as exc:
        payload = {"status": "error", "message": str(exc)}
        return format_response(payload, format, status_code=400)

    payload = {
        "status": "ok",
        "message": "OK: Device flow started",
        "user_code": result["user_code"],
        "device_code": result["device_code"],
        "verification_uri": result["verification_uri"],
        "expires_in": result["expires_in"],
        "interval": result.get("interval", 5),
    }
    return format_response(payload, format)


@app.post("/auth/device/poll")
async def auth_device_poll(
    device_code: str,
    format: str = Query("json", pattern="^(json|kv)$"),
):
    """Poll for device code confirmation and store tokens."""
    if not PUBLIC_CLIENT:
        payload = {"status": "error", "message": "Public client not enabled"}
        return format_response(payload, format, status_code=400)
    if not DEFAULT_CLIENT_ID:
        payload = {"status": "error", "message": "Client ID not configured"}
        return format_response(payload, format, status_code=400)
    
    data = {
        "client_id": DEFAULT_CLIENT_ID,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code"
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post("https://id.twitch.tv/oauth2/token", data=data)
            if response.status_code == 400:
                error = response.json().get("error")
                if error == "authorization_pending":
                    return format_response({"status": "pending", "message": "authorization pending"}, format)
                elif error == "slow_down":
                    return format_response({"status": "slow_down", "message": "slow down"}, format)
                else:
                    return format_response({"status": "error", "message": error}, format, status_code=400)
            response.raise_for_status()
            result = response.json()
            # Store tokens
            auth_data = {
                "client_id": DEFAULT_CLIENT_ID,
                "access_token": result["access_token"],
                "refresh_token": result["refresh_token"],
                "scope": result.get("scope", []),
                "obtained_at": time.time(),
                "expires_in": result["expires_in"],
            }
            save_auth_data(auth_data)
            return format_response({"status": "success", "message": "Authentication successful"}, format)
    except Exception as e:
        payload = {"status": "error", "message": str(e)}
        return format_response(payload, format, status_code=400)


@app.post("/auth/refresh")
async def auth_refresh():
    """Refresh access token using refresh token."""
    if not PUBLIC_CLIENT:
        return {"error": "Public client not enabled"}
    
    success = await refresh_access_token(DEFAULT_CLIENT_ID)
    if success:
        return {"status": "success"}
    else:
        return {"error": "Refresh failed"}


@app.get("/status/ping")
def status_ping(format: str = Query("json", pattern="^(json|kv)$")):
    """Return a deterministic status message to test client polling flows."""
    payload = {"status": "info", "message": "Dienst antwortet.", "op_id": "noop"}
    return format_response(payload, format)


@app.get("/search")
async def search_channels(
    request: Request,
    q: str = Query(..., min_length=1, description="Suchbegriff für Twitch-Kanäle."),
    limit: int = Query(20, ge=1, le=25),
    format: str = Query("json", pattern="^(json|kv)$"),
    live_only: bool = Query(False, description="Nur Live-Kanäle zurückgeben."),
    service: SearchService = Depends(get_search_service),
    client_id: str = Depends(get_client_id),
):
    """Call Twitch's search API and normalise the response."""
    thumb_url_builder = lambda login: str(request.url_for("get_thumbnail", login=login))
    messages: list[str] = ["Suche gestartet…"]
    include_auth = not USE_OFFLINE_STUBS
    _append_auth_messages(messages, include_auth)
    if USE_OFFLINE_STUBS:
        items_result = service.search_channels(  # type: ignore[attr-defined]
            query=q,
            limit=limit,
            thumb_url_builder=thumb_url_builder,
        )
        items_resolved = await _maybe_await(items_result)
        items = [item.to_dict() if hasattr(item, "to_dict") else item for item in items_resolved]
        if live_only:
            items = [item for item in items if item.get("live")]
        messages.append("Suche erfolgreich beendet.")
    else:
        if PUBLIC_CLIENT:
            if not get_access_token():
                payload = {
                    "error": "auth_required",
                    "message": "User authentication required",
                }
                return format_response(payload, format, status_code=401)
        try:
            items_result = service.search_channels(
                query=q,
                limit=limit,
                client_id=client_id,
                thumb_url_builder=thumb_url_builder,
            )
            items_resolved = await _maybe_await(items_result)
            _append_auth_success(messages, include_auth)
            messages.append("Suche erfolgreich beendet.")
        except TwitchAuthError as exc:
            error_message = "Fehler: Authentifizierung bei Twitch fehlgeschlagen."
            payload = {
                "status": "error",
                "message": "Authentifizierung bei Twitch fehlgeschlagen.",
                "detail": str(exc),
                "messages": messages + [error_message],
            }
            return format_response(payload, format, status_code=401)
        except TwitchRequestError as exc:
            error_message = "Fehler: Twitch-Anfrage fehlgeschlagen."
            payload = {
                "status": "error",
                "message": "Twitch-Anfrage fehlgeschlagen.",
                "detail": str(exc),
                "messages": messages + [error_message],
            }
            return format_response(payload, format, status_code=502)

        items = [item.to_dict() if hasattr(item, "to_dict") else item for item in items_resolved]
        if live_only:
            items = [item for item in items if item.get("live")]

    payload = {
        "status": "ok",
        "message": "Suche erfolgreich beendet.",
        "query": q,
        "count": len(items),
        "items": items,
        "live_only": live_only,
        "messages": messages,
    }
    return format_response(payload, format)


@app.get("/profiles")
def list_profiles(format: str = Query("json", pattern="^(json|kv)$")):
    """Enumerate available transcoding profiles from the profiles directory."""
    profiles = [
        {"name": path.stem, "path": str(path)}
        for path in sorted(PROFILES_DIR.glob("*.json"))
    ]
    payload = {
        "status": "Profile geladen.",
        "profiles": profiles,
        "profile_count": len(profiles),
    }
    return format_response(payload, format)


@app.get("/thumbnail/channel/{login}")
async def get_thumbnail(
    login: str,
    fmt: str = Query("jpg", pattern="^(jpg|png)$", description="Bildformat (jpg oder png)."),
    force: bool = Query(False, description="Cache erneuern, auch wenn Eintrag gültig ist."),
    format: str = Query("image", pattern="^(image|json|kv)$"),
    client_id: str = Depends(get_client_id),
):
    """Serve cached thumbnails or a generated placeholder when not available."""
    try:
        result = thumbnail_service.get_thumbnail(login, fmt=fmt, force=force)
    except UnsupportedFormatError as exc:
        fallback_format = "json" if format == "image" else format
        payload = {
            "status": "Fehler",
            "login": login,
            "message": str(exc),
        }
        return format_response(payload, fallback_format, status_code=400)
    except ThumbnailError as exc:
        fallback_format = "json" if format == "image" else format
        payload = {
            "status": "Fehler",
            "login": login,
            "message": str(exc),
        }
        return format_response(payload, fallback_format, status_code=400)

    fmt_lower = fmt.lower()
    can_use_profile = (
        not USE_OFFLINE_STUBS
        and fmt_lower == "jpg"
        and client_id
        and (force or result.source != "cache")
    )

    if can_use_profile:
        user, is_live = await _resolve_user_state(login, client_id)
        if user and is_live is False:
            profile_url = user.get("profile_image_url")
            if profile_url:
                downloaded = await _download_image(profile_url, THUMB_HTTP_TIMEOUT)
                if downloaded:
                    content, content_type = downloaded
                    try:
                        stored = thumbnail_service.store_external_thumbnail(
                            login,
                            fmt_lower,
                            content,
                            content_type=content_type,
                            source="profile",
                        )
                    except OSError:
                        logger.debug("Speichern des Profilbilds fehlgeschlagen (%s)", login)
                        stored = None
                    if stored:
                        result = stored
                    else:
                        result = thumbnail_service.generate_placeholder(login, fmt_lower)

    cache_status = "miss" if result.regenerated else "hit"
    headers = {
        "X-Status": "Vorschaubild bereit.",
        "X-Thumbnail-Login": login,
        "X-Cache": cache_status,
        "X-Thumbnail-Source": result.source,
    }

    if format == "image":
        media_type = "image/jpeg" if fmt_lower == "jpg" else f"image/{fmt_lower}"
        return FileResponse(
            result.path,
            media_type=media_type,
            filename=result.path.name,
            headers=headers,
        )

    payload = {
        "status": "Vorschaubild bereit.",
        "login": login,
        "format": fmt_lower,
        "cache": cache_status,
        "path": str(result.path),
        "source": result.source,
    }
    return format_response(payload, format)


@app.get("/channel/{login}")
async def channel_info(
    request: Request,
    login: str,
    format: str = Query("json", pattern="^(json|kv)$"),
    service: SearchService = Depends(get_search_service),
    client_id: str = Depends(get_client_id),
) -> object:
    thumb_url_builder = lambda l: str(request.url_for("get_thumbnail", login=l))
    messages: list[str] = ["Kanalinfo abrufen…"]
    include_auth = not USE_OFFLINE_STUBS
    _append_auth_messages(messages, include_auth)
    if USE_OFFLINE_STUBS:
        item_result = service.channel_detail(  # type: ignore[attr-defined]
            login=login,
            thumb_url_builder=thumb_url_builder,
        )
        item = await _maybe_await(item_result)
        if item is None:
            error_message = f"Fehler: Kanal '{login}' nicht gefunden."
            payload = {
                "status": "error",
                "message": f"Kanal '{login}' nicht gefunden.",
                "messages": messages + [error_message],
            }
            return format_response(payload, format, status_code=404)
        if hasattr(item, "to_dict"):
            item = item.to_dict()
        messages.append("Kanalinfo erfolgreich abgerufen.")
        payload = {
            "status": "Kanalinfo erfolgreich abgerufen.",
            "item": item,
            "messages": messages,
        }
        return format_response(payload, format)
    if PUBLIC_CLIENT:
        if not get_access_token():
            payload = {
                "error": "auth_required",
                "message": "User authentication required",
            }
            return format_response(payload, format, status_code=401)
    else:
        if not client_id:
            payload = {
                "status": "error",
                "message": CLIENT_ID_REQUIRED_MSG,
                "messages": messages + [CLIENT_ID_REQUIRED_MSG],
            }
            return format_response(payload, format, status_code=401)
    try:
        item_result = service.channel_detail(
            login=login,
            client_id=client_id,
            thumb_url_builder=thumb_url_builder,
        )
        item = await _maybe_await(item_result)
        _append_auth_success(messages, include_auth)
    except (TwitchAuthError, TwitchRequestError) as exc:
        error_message = "Fehler: Twitch-Anfrage fehlgeschlagen."
        payload = {
            "status": "error",
            "message": "Twitch-Anfrage fehlgeschlagen.",
            "detail": str(exc),
            "messages": messages + [error_message],
        }
        return format_response(payload, format, status_code=502)
    if item is None:
        error_message = f"Fehler: Kanal '{login}' nicht gefunden."
        payload = {
            "status": "error",
            "message": f"Kanal '{login}' nicht gefunden.",
            "messages": messages + [error_message],
        }
        return format_response(payload, format, status_code=404)
    if hasattr(item, "to_dict"):
        item = item.to_dict()
    messages.append("Kanalinfo erfolgreich abgerufen.")
    payload = {
        "status": "Kanalinfo erfolgreich abgerufen.",
        "item": item,
        "messages": messages,
    }
    return format_response(payload, format)


@app.get("/top")
async def top_channels(
    request: Request,
    limit: int = Query(5, ge=1, le=25),
    game_id: str | None = Query(None, description="Optionaler Filter auf Game-ID"),
    game_name: str | None = Query(None, description="Optionaler Filter auf Kategorienamen"),
    format: str = Query("json", pattern="^(json|kv)$"),
    service: SearchService = Depends(get_search_service),
    client_id: str = Depends(get_client_id),
) -> object:
    thumb_url_builder = lambda l: str(request.url_for("get_thumbnail", login=l))
    messages: list[str] = ["Top-Liste abrufen…"]
    include_auth = not USE_OFFLINE_STUBS
    _append_auth_messages(messages, include_auth)
    resolved_game_id = game_id
    resolved_game_name = game_name
    if USE_OFFLINE_STUBS:
        if game_name and not resolved_game_id:
            resolved_game_id = service.resolve_game_id(game_name)  # type: ignore[attr-defined]
            if not resolved_game_id:
                error_message = f"Kategorie '{game_name}' nicht gefunden."
                payload = {
                    "status": "error",
                    "message": error_message,
                    "messages": messages + [error_message],
                }
                return format_response(payload, format, status_code=404)
        if resolved_game_id and not resolved_game_name:
            resolved_game_name = game_name
        items_result = service.top_channels(  # type: ignore[attr-defined]
            limit=limit,
            thumb_url_builder=thumb_url_builder,
            game_id=resolved_game_id,
        )
        items_resolved = await _maybe_await(items_result)
        items = [item.to_dict() if hasattr(item, "to_dict") else item for item in items_resolved]
        messages.append("Top-Liste bereit.")
        game_meta = {}
        if resolved_game_id:
            game_meta["id"] = resolved_game_id
        if resolved_game_name:
            game_meta["name"] = resolved_game_name
        payload = {
            "status": "Top-Liste bereit.",
            "count": len(items),
            "items": items,
            "game": game_meta,
            "messages": messages,
        }
        return format_response(payload, format)
    if PUBLIC_CLIENT:
        if not get_access_token():
            error_message = "Auth required for category search."
            payload = {
                "status": "error",
                "message": "User authentication required",
                "messages": messages + [error_message],
            }
            return format_response(payload, format, status_code=401)
    if game_name and not resolved_game_id:
        category = await category_service.resolve_exact(
            name=game_name,
            client_id=client_id,
        )
        if not category:
            error_message = f"Kategorie '{game_name}' nicht gefunden."
            payload = {
                "status": "error",
                "message": error_message,
                "messages": messages + [error_message],
            }
            return format_response(payload, format, status_code=404)
        resolved_game_id = category.id
        resolved_game_name = category.name
    elif resolved_game_id and not resolved_game_name:
        resolved_game_name = game_name
    try:
        items_result = service.top_channels(  # type: ignore[call-arg]
            limit=limit,
            client_id=client_id,  # type: ignore[arg-type]
            thumb_url_builder=thumb_url_builder,
            game_id=resolved_game_id,
        )
        items_resolved = await _maybe_await(items_result)
        _append_auth_success(messages, include_auth)
    except (TwitchAuthError, TwitchRequestError) as exc:
        error_message = "Fehler: Twitch-Anfrage fehlgeschlagen."
        payload = {
            "status": "error",
            "message": "Twitch-Anfrage fehlgeschlagen.",
            "detail": str(exc),
            "messages": messages + [error_message],
        }
        return format_response(payload, format, status_code=502)
    items = [item.to_dict() if hasattr(item, "to_dict") else item for item in items_resolved]
    messages.append("Top-Liste bereit.")
    game_meta = {}
    if resolved_game_id:
        game_meta["id"] = resolved_game_id
    if resolved_game_name:
        game_meta["name"] = resolved_game_name
    payload = {
        "status": "Top-Liste bereit.",
        "count": len(items),
        "items": items,
        "game": game_meta,
        "messages": messages,
    }
    return format_response(payload, format)


@app.get("/favorites")
async def favorites(
    request: Request,
    format: str = Query("json", pattern="^(json|kv)$"),
    service: SearchService = Depends(get_search_service),
    client_id: str = Depends(get_client_id),
) -> object:
    thumb_url_builder = lambda l: str(request.url_for("get_thumbnail", login=l))
    messages: list[str] = ["Favoritenliste abrufen…"]
    include_auth = not USE_OFFLINE_STUBS
    _append_auth_messages(messages, include_auth)
    if USE_OFFLINE_STUBS:
        items_result = service.favorites(thumb_url_builder=thumb_url_builder)  # type: ignore[attr-defined]
        items_resolved = await _maybe_await(items_result)
        items = [item.to_dict() if hasattr(item, "to_dict") else item for item in items_resolved]
        messages.append("Favoritenliste geladen.")
        payload = {
            "status": "Favoriten geladen.",
            "count": len(items),
            "items": items,
            "messages": messages,
        }
        return format_response(payload, format)
    # For PUBLIC_CLIENT we use user access token (device auth)
    favorites_param = request.query_params.get("logins")
    logins = (
        [login.strip() for login in favorites_param.split(",") if login.strip()]
        if favorites_param
        else DEFAULT_FAVORITES
    )
    try:
        if logins:
            items_result = service.favorites(  # type: ignore[call-arg]
                logins=logins,
                client_id=client_id,  # type: ignore[arg-type]
                thumb_url_builder=thumb_url_builder,
            )
        else:
            # No explicit list: pull follows from Twitch using the authenticated user
            items_result = service.favorites_from_token(  # type: ignore[call-arg]
                client_id=client_id,  # type: ignore[arg-type]
                thumb_url_builder=thumb_url_builder,
                max_count=100,
            )
        items_resolved = await _maybe_await(items_result)
        _append_auth_success(messages, include_auth)
    except (TwitchAuthError, TwitchRequestError) as exc:
        error_message = "Fehler: Twitch-Anfrage fehlgeschlagen."
        payload = {
            "status": "error",
            "message": "Twitch-Anfrage fehlgeschlagen.",
            "detail": str(exc),
            "messages": messages + [error_message],
        }
        return format_response(payload, format, status_code=502)
    items = [item.to_dict() if hasattr(item, "to_dict") else item for item in items_resolved]
    messages.append("Favoritenliste geladen.")
    payload = {
        "status": "Favoriten geladen.",
        "count": len(items),
        "items": items,
        "messages": messages,
    }
    return format_response(payload, format)


@app.get("/categories")
async def list_categories(
    query: str = Query(..., min_length=1, description="Suchbegriff für Kategorienamen."),
    limit: int = Query(20, ge=1, le=100),
    format: str = Query("json", pattern="^(json|kv)$"),
    client_id: str = Depends(get_client_id),
) -> object:
    messages: list[str] = ["Kategorien abfragen…"]
    include_auth = not USE_OFFLINE_STUBS
    _append_auth_messages(messages, include_auth)
    if USE_OFFLINE_STUBS:
        categories = get_stub_categories()
        filtered = [cat for cat in categories if query.lower() in cat["name"].lower()]
        categories = filtered[:limit]
        messages.append("Kategorien bereit.")
        payload = {
            "status": "Kategorien bereit.",
            "count": len(categories),
            "items": categories,
            "messages": messages,
        }
        return format_response(payload, format)

    if PUBLIC_CLIENT and not get_access_token():
        error_message = "Auth required for category search."
        payload = {
            "status": "error",
            "message": "User authentication required",
            "messages": messages + [error_message],
        }
        return format_response(payload, format, status_code=401)

    try:
        categories = await category_service.search(
            query=query,
            limit=limit,
            client_id=client_id,
        )
        _append_auth_success(messages, include_auth)
    except (TwitchAuthError, TwitchRequestError) as exc:
        error_message = "Fehler: Twitch-Anfrage fehlgeschlagen."
        payload = {
            "status": "error",
            "message": "Twitch-Anfrage fehlgeschlagen.",
            "detail": str(exc),
            "messages": messages + [error_message],
        }
        return format_response(payload, format, status_code=502)

    items = [category.to_dict() for category in categories]
    messages.append("Kategorien bereit.")
    payload = {
        "status": "Kategorien bereit.",
        "count": len(items),
        "items": items,
        "messages": messages,
    }
    return format_response(payload, format)


@app.post("/strategy")
async def set_strategy(
    body: StrategyRequest | None = Body(None),
    value: str | None = Query(None, description="Optional strategy value"),
    format: str = Query("json", pattern="^(json|kv)$"),
):
    strategy = value
    if strategy is None and body is not None:
        strategy = body.strategy
    if strategy is None:
        payload = {
            "status": "error",
            "message": "strategy value missing",
        }
        return format_response(payload, format, status_code=400)
    try:
        await transcode_manager.set_strategy(strategy)
    except ValueError as exc:
        payload = {
            "status": "error",
            "message": str(exc),
        }
        return format_response(payload, format, status_code=400)
    payload = {
        "status": "ok",
        "strategy": transcode_manager.strategy,
    }
    return format_response(payload, format)


@app.get("/strategy")
async def get_strategy(
    value: str | None = Query(None, description="Optional strategy value"),
    format: str = Query("json", pattern="^(json|kv)$"),
):
    if value is not None:
        try:
            await transcode_manager.set_strategy(value)
        except ValueError as exc:
            payload = {
                "status": "error",
                "message": str(exc),
            }
            return format_response(payload, format, status_code=400)
    payload = {
        "status": "ok",
        "strategy": transcode_manager.strategy,
    }
    return format_response(payload, format)


@app.get("/play/profiles")
def list_transcode_profiles(format: str = Query("json", pattern="^(json|kv)$")):
    profiles = transcode_manager.available_profiles()
    payload = {
        "status": "Profile geladen.",
        "profiles": [
            {
                "name": profile.name,
                "description": profile.description,
                "listen_port": profile.listen_port,
            }
            for profile in profiles
        ],
        "count": len(profiles),
    }
    return format_response(payload, format)


@app.post("/play/start")
async def play_start(
    body: PlayStartRequest,
    format: str = Query("json", pattern="^(json|kv)$"),
):
    op_id = uuid.uuid4().hex
    try:
        status = await transcode_manager.start_pipeline(
            op_id=op_id,
            channel=body.channel,
            profile_name=body.profile,
        )
    except ValueError as exc:
        payload = {
            "status": "error",
            "message": str(exc),
        }
        return format_response(payload, format, status_code=400)
    messages = [
        "Transcoding gestartet…",
        "ffmpeg erfolgreich gestartet…",
        f"Streaming-Server aktiv (Port {status.listen_port}).",
    ]
    payload = {
        "status": "ffmpeg erfolgreich gestartet…",
        "op_id": op_id,
        "channel": body.channel,
        "profile": status.profile,
        "details": _pipeline_payload(status),
        "messages": messages,
    }
    return format_response(payload, format)


@app.post("/play/stop")
async def play_stop(
    body: PlayStopRequest,
    format: str = Query("json", pattern="^(json|kv)$"),
):
    success = await transcode_manager.stop_pipeline(body.op_id)
    if not success:
        payload = {
            "status": "error",
            "message": "op_id unbekannt",
        }
        return format_response(payload, format, status_code=404)
    transcode_manager.remove_pipeline(body.op_id)
    messages = [
        "Stop-Befehl ausgeführt.",
        "Transcoding gestoppt.",
    ]
    payload = {
        "status": "gestoppt",
        "op_id": body.op_id,
        "messages": messages,
    }
    return format_response(payload, format)


@app.get("/play/status/{op_id}")
def play_status(op_id: str, format: str = Query("json", pattern="^(json|kv)$")):
    status = transcode_manager.get_status(op_id)
    if not status:
        payload = {
            "status": "error",
            "message": "op_id unbekannt",
        }
        return format_response(payload, format, status_code=404)
    messages = ["Statusabfrage laufender Prozesse…"]
    if status.state == "running":
        messages.append(f"Streaming-Server aktiv (Port {status.listen_port}).")
    elif status.state == "restarting":
        messages.append("Transcoding wird neu gestartet…")
    elif status.state == "stopping":
        messages.append("Transcoding wird gestoppt…")
    elif status.state == "stopped":
        messages.append("Transcoding gestoppt.")
    elif status.state == "completed":
        messages.append("Transcoding abgeschlossen.")
    elif status.state == "error":
        messages.append("Transcoding-Fehler – bitte Profil prüfen.")
    if status.last_error:
        messages.append(status.last_error)
    payload = {
        "status": "ok",
        "details": _pipeline_payload(status),
        "messages": messages,
    }
    return format_response(payload, format)


@app.get("/op/{op_id}/metrics")
def play_metrics(op_id: str, format: str = Query("json", pattern="^(json|kv)$")):
    metrics = transcode_manager.get_metrics(op_id)
    if not metrics:
        payload = {
            "status": "error",
            "message": "op_id unbekannt",
        }
        return format_response(payload, format, status_code=404)
    status = transcode_manager.get_status(op_id)
    messages = ["Statusabfrage laufender Prozesse…", "Metrics bereit."]
    if status and status.last_error:
        messages.append(status.last_error)
    payload = {
        "status": "ok",
        "metrics": _metrics_payload(metrics),
        "messages": messages,
    }
    if status:
        payload["restart_count"] = status.restart_count
        payload["state"] = status.state
    return format_response(payload, format)
