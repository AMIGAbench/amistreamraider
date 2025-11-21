"""Category lookup helpers for Twitch Helix."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from server.api.twitch_client import TwitchClient


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    box_art_url: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "box_art_url": self.box_art_url,
        }


class CategoryService:
    def __init__(self, twitch_client: TwitchClient) -> None:
        self._twitch = twitch_client

    async def search(
        self,
        *,
        query: str,
        limit: int,
        client_id: str,
    ) -> List[Category]:
        payload = await self._twitch.search_categories(
            query=query,
            first=limit,
            client_id=client_id,
        )
        categories: List[Category] = []
        for entry in payload.get("data", []):
            categories.append(
                Category(
                    id=str(entry.get("id", "")),
                    name=entry.get("name", ""),
                    box_art_url=entry.get("box_art_url"),
                )
            )
        return categories

    async def resolve_exact(
        self,
        *,
        name: str,
        client_id: str,
    ) -> Optional[Category]:
        name_lower = name.strip().lower()
        payload = await self._twitch.search_categories(
            query=name,
            first=20,
            client_id=client_id,
        )
        candidates = payload.get("data", [])
        if not candidates:
            return None
        for entry in candidates:
            if (entry.get("name") or "").strip().lower() == name_lower:
                return Category(
                    id=str(entry.get("id", "")),
                    name=entry.get("name", ""),
                    box_art_url=entry.get("box_art_url"),
                )
        entry = candidates[0]
        return Category(
            id=str(entry.get("id", "")),
            name=entry.get("name", ""),
            box_art_url=entry.get("box_art_url"),
        )
