"""Twitch channel search service backed by the official Helix API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from server.api.twitch_client import TwitchClient


@dataclass(frozen=True)
class ChannelSearchResult:
    """Normalized representation of a Twitch channel search hit."""

    id: str
    login: str
    name: str
    title: str
    live: bool
    viewers: int
    language: str
    thumbnail_url: Optional[str]
    thumb_160_url: str
    started_at: Optional[str]
    game_id: Optional[str]
    game_name: Optional[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "type": "channel",
            "id": self.id,
            "login": self.login,
            "name": self.name,
            "title": self.title,
            "live": self.live,
            "viewers": self.viewers,
            "language": self.language,
            "thumbnail_url": self.thumbnail_url,
            "thumb_160_url": self.thumb_160_url,
            "started_at": self.started_at,
            "game_id": self.game_id,
            "game_name": self.game_name,
        }


class SearchService:
    """Search facade that queries Twitch and normalises the response."""

    def __init__(self, twitch_client: TwitchClient) -> None:
        self._twitch = twitch_client

    async def search_channels(
        self,
        *,
        query: str,
        limit: int,
        client_id: str,
        thumb_url_builder: Callable[[str], str],
    ) -> List[ChannelSearchResult]:
        payload = await self._twitch.search_channels(
            query=query,
            limit=limit,
            client_id=client_id,
        )
        data = payload.get("data", [])
        live_ids = [entry["id"] for entry in data if entry.get("is_live")]
        viewer_map = await self._fetch_viewers(
            live_ids,
            client_id=client_id,
        )
        results: List[ChannelSearchResult] = []
        for entry in data:
            login = entry.get("broadcaster_login") or entry.get("display_name", "").lower()
            thumb_template = entry.get("thumbnail_url")
            thumbnail_url = None
            if thumb_template:
                thumbnail_url = (
                    thumb_template.replace("{width}", "320").replace("{height}", "180")
                )
            result = ChannelSearchResult(
                id=str(entry.get("id", "")),
                login=login,
                name=entry.get("display_name", login),
                title=entry.get("title") or "",
                live=bool(entry.get("is_live")),
                viewers=viewer_map.get(entry.get("id"), 0),
                language=entry.get("broadcaster_language") or "",
                thumbnail_url=thumbnail_url,
                thumb_160_url=thumb_url_builder(login),
                started_at=entry.get("started_at"),
                game_id=entry.get("game_id"),
                game_name=entry.get("game_name"),
            )
            results.append(result)
        return results

    async def resolve_category_id(
        self,
        *,
        game_name: str,
        client_id: str,
    ) -> Optional[str]:
        """Resolve a human-readable category name to a Twitch game_id."""
        payload = await self._twitch.search_categories(
            query=game_name,
            first=20,
            client_id=client_id,
        )
        data = payload.get("data", [])
        if not data:
            return None
        target = game_name.strip().lower()
        for entry in data:
            name = (entry.get("name") or "").lower()
            if name == target:
                return entry.get("id")
        return data[0].get("id")

    async def _fetch_viewers(
        self,
        live_ids: List[str],
        *,
        client_id: str,
    ) -> Dict[str, int]:
        if not live_ids:
            return {}
        payload = await self._twitch.get_streams(
            user_ids=live_ids,
            client_id=client_id,
        )
        viewer_map: Dict[str, int] = {}
        for stream in payload.get("data", []):
            viewer_map[str(stream.get("user_id"))] = int(stream.get("viewer_count", 0))
        return viewer_map

    async def channel_detail(
        self,
        *,
        login: str,
        client_id: str,
        thumb_url_builder: Callable[[str], str],
    ) -> Optional[Dict[str, object]]:
        users_payload = await self._twitch.get_users(
            logins=[login],
            client_id=client_id,
        )
        data = users_payload.get("data", [])
        if not data:
            return None
        user = data[0]
        streams_payload = await self._twitch.get_streams(
            user_ids=[user["id"]],
            client_id=client_id,
        )
        stream = streams_payload.get("data", [])
        stream_info = stream[0] if stream else None
        return self._channel_from_user(user, stream_info, thumb_url_builder)

    async def top_channels(
        self,
        *,
        limit: int,
        client_id: str,
        thumb_url_builder: Callable[[str], str],
        game_id: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        streams_payload = await self._twitch.get_streams(
            user_ids=None,
            first=limit,
            game_id=game_id,
            client_id=client_id,
        )
        streams = streams_payload.get("data", [])
        if not streams:
            return []
        user_logins = [stream.get("user_login") for stream in streams if stream.get("user_login")]
        users_payload = await self._twitch.get_users(
            logins=user_logins,
            client_id=client_id,
        )
        user_map = {user["login"].lower(): user for user in users_payload.get("data", [])}
        results: List[Dict[str, object]] = []
        for stream in streams:
            login = stream.get("user_login", "").lower()
            user = user_map.get(login, {})
            results.append(
                self._channel_from_stream(stream, user, thumb_url_builder)
            )
        return results

    async def favorites(
        self,
        *,
        logins: List[str],
        client_id: str,
        thumb_url_builder: Callable[[str], str],
    ) -> List[Dict[str, object]]:
        if not logins:
            return []
        users_payload = await self._twitch.get_users(
            logins=logins,
            client_id=client_id,
        )
        users = users_payload.get("data", [])
        if not users:
            return []
        ids = [user["id"] for user in users]
        streams_payload = await self._twitch.get_streams(
            user_ids=ids,
            client_id=client_id,
        )
        stream_map = {
            stream.get("user_id"): stream
            for stream in streams_payload.get("data", [])
        }
        user_map = {user["login"].lower(): user for user in users}
        results: List[Dict[str, object]] = []
        for login in logins:
            user = user_map.get(login.lower())
            if not user:
                continue
            stream = stream_map.get(user["id"])
            results.append(self._channel_from_user(user, stream, thumb_url_builder))
        return results

    async def favorites_from_token(
        self,
        *,
        client_id: str,
        thumb_url_builder: Callable[[str], str],
        max_count: int = 100,
    ) -> List[Dict[str, object]]:
        """Fetch favorites based on the authenticated user's follows.

        This uses the token-bound user (GET /users) and lists their follows
        (GET /users/follows?from_id=...). It returns up to max_count items
        enriched with live status and metadata.
        """
        # Resolve current user from token
        me_payload = await self._twitch.get_users(client_id=client_id)
        me_list = me_payload.get("data", [])
        if not me_list:
            return []
        me = me_list[0]
        from_id = str(me.get("id"))
        if not from_id:
            return []

        follows_payload = await self._twitch.get_followed_channels(
            from_user_id=from_id,
            first=max_count,
            client_id=client_id,
        )
        follows = follows_payload.get("data", [])
        if not follows:
            return []

        # New Helix response for channels/followed uses broadcaster_* keys
        target_ids = []
        for f in follows:
            bid = f.get("broadcaster_id") or f.get("to_id")  # to_id for legacy
            if bid:
                target_ids.append(bid)
        if not target_ids:
            return []

        users_payload = await self._twitch.get_users(ids=target_ids, client_id=client_id)
        users = users_payload.get("data", [])
        if not users:
            return []
        streams_payload = await self._twitch.get_streams(user_ids=target_ids, client_id=client_id)
        stream_map = {s.get("user_id"): s for s in streams_payload.get("data", [])}
        # Map by login for consistent output ordering (preserve original follows order)
        user_by_id = {u.get("id"): u for u in users}
        results: List[Dict[str, object]] = []
        for f in follows:
            uid = f.get("broadcaster_id") or f.get("to_id")
            user = user_by_id.get(uid)
            if not user:
                continue
            stream = stream_map.get(uid)
            results.append(self._channel_from_user(user, stream, thumb_url_builder))
        return results

    def _channel_from_user(
        self,
        user: Dict[str, Any],
        stream: Optional[Dict[str, Any]],
        thumb_url_builder: Callable[[str], str],
    ) -> Dict[str, object]:
        login = user.get("login", "")
        live = bool(stream)
        viewers = int(stream.get("viewer_count", 0)) if stream else 0
        title = stream.get("title") if stream else ""
        game_id = stream.get("game_id") if stream else None
        game_name = stream.get("game_name") if stream else None
        started_at = stream.get("started_at") if stream else None
        return {
            "type": "channel",
            "id": user.get("id"),
            "login": login,
            "name": user.get("display_name", login),
            "title": title or user.get("description", ""),
            "live": live,
            "viewers": viewers,
            "language": user.get("broadcaster_language") or user.get("language", ""),
            "game_id": game_id,
            "game_name": game_name,
            "started_at": started_at,
            "thumb_160_url": thumb_url_builder(login),
        }

    def _channel_from_stream(
        self,
        stream: Dict[str, Any],
        user: Dict[str, Any],
        thumb_url_builder: Callable[[str], str],
    ) -> Dict[str, object]:
        login = stream.get("user_login", "")
        return {
            "type": "channel",
            "id": stream.get("user_id"),
            "login": login,
            "name": stream.get("user_name", login),
            "title": stream.get("title", ""),
            "live": True,
            "viewers": int(stream.get("viewer_count", 0)),
            "language": stream.get("language", ""),
            "game_id": stream.get("game_id"),
            "game_name": stream.get("game_name"),
            "started_at": stream.get("started_at"),
            "thumb_160_url": thumb_url_builder(login),
        }
