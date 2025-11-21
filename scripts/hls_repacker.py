#!/usr/bin/env python3
"""
Repair/normalize a local HLS directory by monitoring an incoming manifest
and re-emitting segments with corrected continuity. Handles gaps by
inserting optional placeholder segments.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, List, Optional

SEGMENT_SUFFIX = ".ts"


@dataclass
class SegmentEntry:
    uri: str
    duration: float
    discontinuity: bool = False


def parse_manifest(path: Path) -> List[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    entries: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(line)
    return entries


async def wait_for_segment(path: Path, poll_interval: float, timeout: float) -> bool:
    deadline = time.time() + timeout
    last_size = -1
    stable = 0
    while time.time() < deadline:
        if path.exists():
            size = path.stat().st_size
            if size > 0:
                if size == last_size:
                    stable += 1
                    if stable >= 2:
                        return True
                else:
                    stable = 0
                    last_size = size
        await asyncio.sleep(poll_interval)
    return False


async def copy_segment(src: Path, dst: Path) -> None:
    await asyncio.to_thread(shutil.copyfile, src, dst)


def build_manifest(segments: Deque[SegmentEntry]) -> str:
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:6",
        "#EXT-X-MEDIA-SEQUENCE:0",
    ]
    for seg in segments:
        if seg.discontinuity:
            lines.append("#EXT-X-DISCONTINUITY")
        lines.append(f"#EXTINF:{seg.duration:.3f},")
        lines.append(seg.uri)
    return "\n".join(lines) + "\n"


async def repack(args) -> None:
    incoming_manifest = args.source_manifest
    outgoing_dir = args.output_dir
    outgoing_manifest = outgoing_dir / "index.m3u8"
    outgoing_dir.mkdir(parents=True, exist_ok=True)

    recent_segments: Deque[SegmentEntry] = deque(maxlen=args.keep_segments)
    processed = set()
    sequence = 0
    last_output_path: Optional[Path] = None
    last_source_time = time.time()

    while True:
        segments = parse_manifest(incoming_manifest)
        if not segments:
            await asyncio.sleep(args.poll_interval)
            continue

        for uri in segments:
            if uri in processed:
                continue
            src = incoming_manifest.parent / uri
            if not await wait_for_segment(src, args.poll_interval, args.segment_timeout):
                processed.add(uri)
                continue

            dst_name = f"segment_{sequence:05d}{SEGMENT_SUFFIX}"
            dst_path = outgoing_dir / dst_name
            await copy_segment(src, dst_path)

            recent_segments.append(SegmentEntry(uri=dst_name, duration=args.segment_duration, discontinuity=False))
            processed.add(uri)
            sequence += 1
            last_output_path = dst_path
            last_source_time = time.time()
            outgoing_manifest.write_text(build_manifest(recent_segments), encoding="utf-8")

        if (
            args.gap_timeout > 0
            and last_output_path
            and recent_segments
            and time.time() - last_source_time >= args.gap_timeout
        ):
            dst_name = f"segment_{sequence:05d}{SEGMENT_SUFFIX}"
            dst_path = outgoing_dir / dst_name
            await copy_segment(last_output_path, dst_path)
            recent_segments.append(
                SegmentEntry(uri=dst_name, duration=args.segment_duration, discontinuity=True)
            )
            sequence += 1
            last_source_time = time.time()
            outgoing_manifest.write_text(build_manifest(recent_segments), encoding="utf-8")

        await asyncio.sleep(args.poll_interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="HLS repacker")
    parser.add_argument("--source-manifest", type=Path, required=True, help="Input manifest path")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory for repaired HLS")
    parser.add_argument("--poll-interval", type=float, default=0.5, help="Polling interval for new segments")
    parser.add_argument("--segment-timeout", type=float, default=15.0, help="Seconds to wait for a segment")
    parser.add_argument("--segment-duration", type=float, default=4.0, help="Declared duration per segment")
    parser.add_argument("--keep-segments", type=int, default=8, help="How many segments to keep in the manifest")
    parser.add_argument(
        "--gap-timeout",
        type=float,
        default=20.0,
        help="Duplicate the last segment after this many seconds without new input (0 disables)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(repack(args))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
