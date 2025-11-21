#!/usr/bin/env python3
"""Minimal stdin→TCP sink for MPEG-TS output."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path
from typing import Optional, Set

CHUNK_SIZE = 64 * 1024


async def serve_stream(
    host: str,
    port: int,
    *,
    backlog: int,
    stats_file: Optional[Path],
    wait_for_client: bool,
    stop_on_disconnect: bool,
) -> None:
    loop = asyncio.get_running_loop()
    stdin_reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(stdin_reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    clients: Set[asyncio.StreamWriter] = set()
    total_bytes = 0
    client_ready = asyncio.Event()
    shutdown_event = asyncio.Event()

    async def write_stats() -> None:
        nonlocal total_bytes
        if not stats_file:
            return
        stats_file.parent.mkdir(parents=True, exist_ok=True)
        while True:
            await asyncio.sleep(1)
            try:
                stats_file.write_text(f"{total_bytes}\n", encoding="utf-8")
            except Exception:
                pass

    async def close_client(writer: asyncio.StreamWriter) -> None:
        clients.discard(writer)
        with contextlib.suppress(Exception):
            writer.close()
            await writer.wait_closed()

    stdin_task: Optional[asyncio.Task] = None

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal stdin_task
        peer = writer.get_extra_info("peername")
        print(f"[tcp-sink] client connected from {peer}", flush=True)
        clients.add(writer)
        client_ready.set()
        try:
            await reader.read()
        finally:
            await close_client(writer)
            print("[tcp-sink] client disconnected", flush=True)
            if stop_on_disconnect and not clients:
                shutdown_event.set()
                if stdin_task and not stdin_task.done():
                    stdin_task.cancel()

    server = await asyncio.start_server(handle_client, host, port, backlog=backlog)

    async def broadcast(chunk: bytes) -> None:
        dead: Set[asyncio.StreamWriter] = set()
        for writer in list(clients):
            try:
                writer.write(chunk)
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                dead.add(writer)
        for writer in dead:
            await close_client(writer)

    async def stdin_consumer() -> None:
        nonlocal total_bytes
        print("[tcp-sink] stdin reader active", flush=True)
        if wait_for_client:
            print("[tcp-sink] waiting for first client before streaming", flush=True)
            await client_ready.wait()
        while not shutdown_event.is_set():
            data = await stdin_reader.read(CHUNK_SIZE)
            if not data:
                break
            total_bytes += len(data)
            await broadcast(data)
        print("[tcp-sink] stdin closed or shutdown requested, stopping broadcast", flush=True)

    stats_task = asyncio.create_task(write_stats()) if stats_file else None
    stdin_task = asyncio.create_task(stdin_consumer())
    async with server:
        try:
            await stdin_task
        except asyncio.CancelledError:
            pass
        finally:
            if stats_task:
                stats_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stats_task
            close_tasks = [close_client(writer) for writer in list(clients)]
            if close_tasks:
                await asyncio.gather(*close_tasks, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Forward stdin MPEG-TS to TCP clients.")
    parser.add_argument("--host", default="0.0.0.0", help="Listen host")
    parser.add_argument("--port", type=int, required=True, help="Listen port")
    parser.add_argument("--backlog", type=int, default=8, help="TCP backlog (default: 8)")
    parser.add_argument("--stats-file", type=Path, help="Optional file to write cumulative bytes")
    parser.add_argument("--wait-for-client", action="store_true", help="Do not read stdin until at least one client connects.")
    parser.add_argument(
        "--stop-on-disconnect",
        action="store_true",
        help="Stop streaming and exit when the last client disconnects.",
    )
    args = parser.parse_args()

    asyncio.run(
        serve_stream(
            args.host,
            args.port,
            backlog=args.backlog,
            stats_file=args.stats_file,
            wait_for_client=args.wait_for_client,
            stop_on_disconnect=args.stop_on_disconnect,
        )
    )


if __name__ == "__main__":
    main()
