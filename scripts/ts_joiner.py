#!/usr/bin/env python3
"""Join placeholder MPEG-TS with live MPEG-TS using a global PTS offset."""

from __future__ import annotations

import argparse
import os
import selectors
import sys
from typing import Optional

TS_PACKET_SIZE = 188
PTS_MASK = (1 << 33) - 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TS joiner (placeholder → live)")
    parser.add_argument("--live-fd", type=int, required=True, help="File descriptor for live TS input")
    return parser.parse_args()


def decode_pts(data: bytes) -> int:
    return (
        ((data[0] >> 1) & 0x07) << 30
        | (data[1] << 22)
        | ((data[2] >> 1) & 0x7F) << 15
        | (data[3] << 7)
        | ((data[4] >> 1) & 0x7F)
    )


def encode_pts(value: int) -> bytes:
    pts = value & ((1 << 33) - 1)
    b0 = (((0x2 << 4) | ((pts >> 30) & 0x07)) << 1) | 0x01
    b1 = (pts >> 22) & 0xFF
    b2 = ((((pts >> 15) & 0x7F) << 1) | 0x01) & 0xFF
    b3 = (pts >> 7) & 0xFF
    b4 = (((pts & 0x7F) << 1) | 0x01) & 0xFF
    return bytes([b0, b1, b2, b3, b4])


def stream_is_media(stream_id: int) -> bool:
    return 0xC0 <= stream_id <= 0xDF or 0xE0 <= stream_id <= 0xEF


def extract_pts(packet: bytearray) -> Optional[tuple[int, int, Optional[int], Optional[int]]]:
    if len(packet) != TS_PACKET_SIZE or packet[0] != 0x47:
        return None
    payload_unit_start = bool(packet[1] & 0x40)
    adaptation_field_control = (packet[3] >> 4) & 0x03
    idx = 4
    if adaptation_field_control in (2, 3):
        if idx >= len(packet):
            return None
        adap_len = packet[idx]
        idx += 1 + adap_len
    if adaptation_field_control == 2 or idx >= len(packet):
        return None
    if not payload_unit_start:
        return None
    if idx + 6 > len(packet) or packet[idx : idx + 3] != b"\x00\x00\x01":
        return None
    stream_id = packet[idx + 3]
    if not stream_is_media(stream_id):
        return None
    flags = packet[idx + 7]
    header_data_length = packet[idx + 8]
    if not (flags & 0x80):
        return None
    pts_pos = idx + 9
    if pts_pos + 5 > len(packet) or header_data_length < 5:
        return None
    pts = decode_pts(packet[pts_pos : pts_pos + 5])
    dts_pos: Optional[int] = None
    dts: Optional[int] = None
    if flags & 0x40:
        dts_pos = pts_pos + 5
        if dts_pos + 5 > len(packet) or header_data_length < 10:
            return None
        dts = decode_pts(packet[dts_pos : dts_pos + 5])
    return pts, pts_pos, dts, dts_pos


def main() -> None:
    args = parse_args()
    live_fd = os.fdopen(args.live_fd, "rb", buffering=0)
    placeholder = sys.stdin.buffer
    out = sys.stdout.buffer

    selector = selectors.DefaultSelector()
    selector.register(live_fd, selectors.EVENT_READ)

    use_live = False
    placeholder_last_pts: Optional[int] = None
    first_live_pts: Optional[int] = None
    pts_offset: Optional[int] = None

    while True:
        if not use_live:
            events = selector.select(timeout=0)
            if events:
                use_live = True
                print("[ts-joiner] switching to live stream", file=sys.stderr, flush=True)
                try:
                    placeholder.close()
                except Exception:
                    pass
        source = live_fd if use_live else placeholder
        packet = source.read(TS_PACKET_SIZE)
        if not packet:
            if not use_live:
                continue
            break

        if use_live:
            mutable_packet = bytearray(packet)
            parsed = extract_pts(mutable_packet)
            if parsed:
                pts, pts_pos, dts, dts_pos = parsed
                if first_live_pts is None:
                    first_live_pts = pts
                    if placeholder_last_pts is None:
                        placeholder_last_pts = pts
                    pts_offset = (placeholder_last_pts - first_live_pts) & PTS_MASK
                if pts_offset is not None:
                    new_pts = (pts + pts_offset) & PTS_MASK
                    mutable_packet[pts_pos : pts_pos + 5] = encode_pts(new_pts)
                    if dts is not None and dts_pos is not None:
                        new_dts = (dts + pts_offset) & PTS_MASK
                        mutable_packet[dts_pos : dts_pos + 5] = encode_pts(new_dts)
            out.write(mutable_packet)
        else:
            mutable_packet = bytearray(packet)
            parsed = extract_pts(mutable_packet)
            if parsed:
                pts, _, _, _ = parsed
                if (placeholder_last_pts is None) or (pts > placeholder_last_pts):
                    placeholder_last_pts = pts
            out.write(packet)
        out.flush()


if __name__ == "__main__":
    main()
