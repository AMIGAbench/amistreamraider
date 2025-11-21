"""Utilities for loading transcoding profiles from disk."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from server.transcode.manager import TranscodeProfile


def load_profiles(profiles_dir: Path) -> Dict[str, TranscodeProfile]:
    profiles: Dict[str, TranscodeProfile] = {}
    if not profiles_dir.exists():
        return profiles
    for path in profiles_dir.glob("*.json"):
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        name = data.get("name") or path.stem
        output_args = data.get("output_args") or []
        if not isinstance(output_args, list):
            raise ValueError(f"Profile {path} has invalid output_args")
        profile = TranscodeProfile(
            name=name,
            output_args=[str(arg) for arg in output_args],
            description=data.get("description"),
            listen_port=data.get("listen_port"),
        )
        profiles[profile.name] = profile
    return profiles

