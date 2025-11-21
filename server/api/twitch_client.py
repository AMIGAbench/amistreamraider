"""Asynchronous Twitch API client with user access tokens."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, Optional, Sequence, Tuple, Union

import httpx

from server.api.auth import get_access_token, refresh_access_token


class TwitchAPIError(Exception):
    """Base error for Twitch API interactions."""


class TwitchAuthError(TwitchAPIError):
    """Raised when acquiring an OAuth token fails."""


class TwitchRequestError(TwitchAPIError):
    """Raised when an HTTP request to Twitch fails."""


class TwitchClient:
    """Thin wrapper around the Twitch Helix API using user access tokens."""

    def __init__(
        self,
        *,
        base_url: str = "https://api.twitch.tv/helix",
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout)

    async def search_channels(
        self,
        *,
        query: str,
        limit: int,
        client_id: str,
    ) -> Dict[str, Any]:
        """Call Twitch's search/channels endpoint."""
        params = {"query": query, "first": str(limit)}
        return await self._request(
            "GET",
            "/search/channels",
            params=params,
            client_id=client_id,
        )

    async def get_streams(
        self,
        *,
        user_ids: Sequence[str] | None = None,
        first: Optional[int] = None,
        game_id: Optional[str] = None,
        client_id: str,
    ) -> Dict[str, Any]:
        """Fetch live stream details for the provided user IDs."""
        params: list[tuple[str, str]] = []
        if user_ids:
            params.extend([("user_id", user_id) for user_id in user_ids])
        if first:
            params.append(("first", str(first)))
        if game_id:
            params.append(("game_id", game_id))
        return await self._request(
            "GET",
            "/streams",
            params=params,
            client_id=client_id,
        )

    async def get_users(
        self,
        *,
        logins: Optional[Sequence[str]] = None,
        ids: Optional[Sequence[str]] = None,
        client_id: str,
    ) -> Dict[str, Any]:
        """Fetch user profiles by login or ID."""
        if not logins and not ids:
            # No filters → return current authenticated user
            return await self._request(
                "GET",
                "/users",
                params=None,
                client_id=client_id,
            )
        params: list[tuple[str, str]] = []
        if logins:
            params.extend([("login", login) for login in logins])
        if ids:
            params.extend([("id", user_id) for user_id in ids])
        return await self._request(
            "GET",
            "/users",
            params=params,
            client_id=client_id,
        )

    async def get_followed_channels(
        self,
        *,
        from_user_id: str,
        first: Optional[int] = None,
        after: Optional[str] = None,
        client_id: str,
    ) -> Dict[str, Any]:
        """Get channels followed by the specified user (Helix: channels/followed)."""
        # Helix has migrated from /users/follows to /channels/followed
        # Requires scope: user:read:follows
        params: list[Tuple[str, str]] = [("user_id", from_user_id)]
        if first:
            params.append(("first", str(first)))
        if after:
            params.append(("after", after))
        return await self._request(
            "GET",
            "/channels/followed",
            params=params,
            client_id=client_id,
        )

    async def search_categories(
        self,
        *,
        query: str,
        first: Optional[int] = None,
        client_id: str,
    ) -> Dict[str, Any]:
        """Search Twitch categories by a free-text query."""
        params: list[tuple[str, str]] = [("query", query)]
        if first:
            params.append(("first", str(first)))
        return await self._request(
            "GET",
            "/search/categories",
            params=params,
            client_id=client_id,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        client_id: str,
        params: Optional[Union[Dict[str, Any], Sequence[Tuple[str, Any]]]] = None,
    ) -> Dict[str, Any]:
        token = get_access_token()
        if not token:
            raise TwitchAuthError("No user access token available")
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {token}",
        }
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                )
        except httpx.RequestError as exc:
            raise TwitchRequestError(f"Twitch request failed: {exc}") from exc

        if response.status_code == 401:
            # Try refresh
            if await refresh_access_token(client_id):
                token = get_access_token()
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                    try:
                        async with httpx.AsyncClient(timeout=self.timeout) as client:
                            response = await client.request(
                                method,
                                url,
                                headers=headers,
                                params=params,
                            )
                    except httpx.RequestError as exc:  # pragma: no cover - network failure
                        raise TwitchRequestError(f"Twitch request failed: {exc}") from exc

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TwitchRequestError(
                f"Twitch API returned HTTP {exc.response.status_code}: {exc.response.text}"
            ) from exc

        return response.json()
