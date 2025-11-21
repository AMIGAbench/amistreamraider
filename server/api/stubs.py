"""Offline stub data used when Twitch Helix is not reachable."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class StubChannel:
    id: str
    login: str
    name: str
    title: str
    live: bool
    viewers: int
    language: str
    game_id: Optional[str]
    game_name: Optional[str]
    started_at: Optional[str]


STUB_CHANNELS: List[StubChannel] = [
    StubChannel(
        id="1001",
        login="amigadev",
        name="AmigaDev",
        title="Retro Coding: AREXX Automation",
        live=True,
        viewers=420,
        language="en",
        game_id="509670",
        game_name="Science & Technology",
        started_at="2025-01-01T19:45:00Z",
    ),
    StubChannel(
        id="1002",
        login="pixelqueen",
        name="PixelQueen",
        title="VOD Rewatch: Deluxe Paint Tricks",
        live=False,
        viewers=0,
        language="de",
        game_id="509660",
        game_name="Art",
        started_at=None,
    ),
    StubChannel(
        id="1003",
        login="nerdcastle",
        name="NerdCastle",
        title="Live: Building Amiga-Friendly APIs",
        live=True,
        viewers=815,
        language="en",
        game_id="1469308723",
        game_name="Software & Game Development",
        started_at="2025-01-01T18:05:00Z",
    ),
]

STUB_CATEGORIES: Dict[str, str] = {
    channel.game_id: channel.game_name or ""  # type: ignore[index]
    for channel in STUB_CHANNELS
    if channel.game_id
}


def get_stub_categories() -> List[Dict[str, str]]:
    return [
        {
            "id": game_id,
            "name": name,
            "box_art_url": None,
        }
        for game_id, name in STUB_CATEGORIES.items()
    ]


class OfflineTwitchService:
    """Return deterministic data for offline development."""

    def search_channels(
        self,
        *,
        query: str,
        limit: int,
        thumb_url_builder,
        **_: object,
    ) -> List[Dict[str, object]]:
        query_lower = query.lower()
        filtered = [
            channel
            for channel in STUB_CHANNELS
            if query_lower in channel.login or query_lower in channel.name.lower()
        ]
        if not filtered:
            filtered = STUB_CHANNELS
        return [self._to_dict(channel, thumb_url_builder) for channel in filtered[:limit]]

    def channel_detail(
        self,
        *,
        login: str,
        thumb_url_builder,
        **_: object,
    ) -> Optional[Dict[str, object]]:
        login_lower = login.lower()
        for channel in STUB_CHANNELS:
            if channel.login == login_lower:
                return self._to_dict(channel, thumb_url_builder)
        return None

    def top_channels(
        self,
        *,
        limit: int,
        thumb_url_builder,
        game_id: Optional[str] = None,
        game_name: Optional[str] = None,
        **_: object,
    ) -> List[Dict[str, object]]:
        candidates = STUB_CHANNELS
        if game_id:
            candidates = [channel for channel in candidates if channel.game_id == game_id]
        elif game_name:
            match = game_name.lower()
            candidates = [channel for channel in candidates if (channel.game_name or "").lower() == match]
        top_sorted = sorted(candidates, key=lambda c: c.viewers, reverse=True)
        return [self._to_dict(channel, thumb_url_builder) for channel in top_sorted[:limit]]

    def resolve_game_id(self, game_name: str) -> Optional[str]:
        match = game_name.strip().lower()
        for channel in STUB_CHANNELS:
            if (channel.game_name or "").lower() == match and channel.game_id:
                return channel.game_id
        return None

    def favorites(self, *, thumb_url_builder, **_: object) -> List[Dict[str, object]]:
        return [self._to_dict(channel, thumb_url_builder) for channel in STUB_CHANNELS[:2]]

    def _to_dict(self, channel: StubChannel, thumb_builder) -> Dict[str, object]:
        return {
            "type": "channel",
            "id": channel.id,
            "login": channel.login,
            "name": channel.name,
            "title": channel.title,
            "live": channel.live,
            "viewers": channel.viewers,
            "language": channel.language,
            "game_id": channel.game_id,
            "game_name": channel.game_name,
            "started_at": channel.started_at,
            "thumb_160_url": thumb_builder(channel.login),
        }
