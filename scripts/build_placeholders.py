#!/usr/bin/env python3
"""
Generate per-profile placeholder MPEG clips from a static image so early
transcoder stages can feed clients before the real Twitch stream is ready.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Dict, List

import json

ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = ROOT / "server" / "profiles"
ASSETS_DIR = ROOT / "server" / "assets"
PLACEHOLDER_DIR = ASSETS_DIR / "placeholders"
PLACEHOLDER_IMAGE = ASSETS_DIR / "videoinsert.jpg"


def load_profiles() -> Dict[str, Dict]:
    profiles: Dict[str, Dict] = {}
    for path in PROFILES_DIR.glob("*.json"):
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        name = data.get("name") or path.stem
        profiles[name] = data
    return profiles


def list_to_opts(args: List[str]) -> Dict[str, str]:
    opts: Dict[str, str] = {}
    i = 0
    length = len(args)
    while i < length:
        key = args[i]
        if not key.startswith("-"):
            i += 1
            continue
        if i + 1 < length and not args[i + 1].startswith("-"):
            opts[key] = args[i + 1]
            i += 2
        else:
            opts[key] = ""
            i += 1
    return opts


def build_placeholder(
    profile_name: str,
    profile: Dict,
    *,
    duration: float,
    dry_run: bool = False,
) -> None:
    output_args: List[str] = profile.get("output_args") or []
    opts = list_to_opts(output_args)

    video_codec = opts.get("-vcodec", "mpeg1video")
    video_bitrate = opts.get("-b:v", "800k")
    frame_rate = opts.get("-r", "24")
    video_filter = opts.get("-vf")
    bframes = opts.get("-bf")

    audio_codec = opts.get("-acodec", "mp2")
    audio_rate = opts.get("-ar", "22050")
    audio_channels = opts.get("-ac", "2")
    audio_filter = opts.get("-af")
    audio_bitrate = opts.get("-b:a", "128k")

    channel_layout = "mono" if audio_channels == "1" else "stereo"
    output_path = PLACEHOLDER_DIR / f"{profile_name}.mpg"

    cmd: List[str] = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(PLACEHOLDER_IMAGE),
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=channel_layout={channel_layout}:sample_rate={audio_rate}",
        "-shortest",
        "-t",
        str(duration),
        "-r",
        frame_rate,
        "-c:v",
        video_codec,
        "-b:v",
        video_bitrate,
    ]
    if bframes:
        cmd.extend(["-bf", bframes])
    if video_filter:
        cmd.extend(["-vf", video_filter])

    cmd.extend(
        [
            "-c:a",
            audio_codec,
            "-ar",
            audio_rate,
            "-ac",
            audio_channels,
        ]
    )
    if audio_filter:
        cmd.extend(["-af", audio_filter])
    if audio_bitrate:
        cmd.extend(["-b:a", audio_bitrate])

    cmd.extend(["-f", "mpeg", str(output_path)])

    if dry_run:
        print(" ".join(cmd))
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[placeholder] {profile_name}: writing {output_path}")
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build placeholder videos for all profiles.")
    parser.add_argument(
        "--duration",
        type=float,
        default=10.0,
        help="Placeholder clip length in seconds (default: 10)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print ffmpeg commands without executing them",
    )
    args = parser.parse_args()

    if not PLACEHOLDER_IMAGE.exists():
        raise SystemExit(f"Placeholder image {PLACEHOLDER_IMAGE} missing.")

    profiles = load_profiles()
    if not profiles:
        raise SystemExit(f"No profiles found in {PROFILES_DIR}")

    for name, profile in profiles.items():
        build_placeholder(name, profile, duration=args.duration, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
