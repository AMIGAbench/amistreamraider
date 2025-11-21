"""Authentication utilities for user tokens."""

import json
import os
import time
from pathlib import Path
from typing import Dict, Optional

ROOT_DIR = Path(__file__).resolve().parents[2]
AUTH_STORE = Path(os.getenv("AUTH_STORE", ROOT_DIR / "data" / "auth.json"))


def load_auth_data() -> Optional[Dict[str, object]]:
    if not AUTH_STORE.exists():
        return None
    try:
        with AUTH_STORE.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def save_auth_data(data: Dict[str, object]) -> None:
    AUTH_STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = AUTH_STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(AUTH_STORE)


def get_access_token() -> Optional[str]:
    data = load_auth_data()
    if not data:
        return None
    obtained_at = data.get("obtained_at", 0)
    expires_in = data.get("expires_in", 0)
    if time.time() > obtained_at + expires_in - 60:
        return None
    return data.get("access_token")


def get_refresh_token() -> Optional[str]:
    data = load_auth_data()
    return data.get("refresh_token") if data else None


async def refresh_access_token(client_id: str) -> bool:
    """Refresh the access token using refresh token."""
    import httpx

    refresh_token = get_refresh_token()
    if not refresh_token:
        return False

    payload = {
        "client_id": client_id,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post("https://id.twitch.tv/oauth2/token", data=payload)
            response.raise_for_status()
            result = response.json()
    except Exception:
        return False

    auth_data = load_auth_data() or {}
    auth_data.update(
        {
            "access_token": result["access_token"],
            "refresh_token": result.get("refresh_token", refresh_token),
            "obtained_at": time.time(),
            "expires_in": result["expires_in"],
        }
    )
    save_auth_data(auth_data)
    return True
