#!/usr/bin/env python3
"""TCP wrapper that starts the transcode pipeline on client connect."""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
from typing import Optional

BUFFER_SIZE = 64 * 1024
CURRENT_PIPELINE: Optional[subprocess.Popen] = None


def _terminate_current_pipeline() -> None:
    global CURRENT_PIPELINE
    proc = CURRENT_PIPELINE
    if not proc:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    CURRENT_PIPELINE = None


def _handle_signal(signum, frame) -> None:  # type: ignore[override]
    _terminate_current_pipeline()
    sys.exit(0)


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def run_pipeline_to_socket(cmd: str, conn: socket.socket) -> None:
    """Execute shell pipeline and forward stdout into the given TCP socket."""
    proc = subprocess.Popen(
        ["bash", "-lc", cmd],
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        bufsize=0,
        preexec_fn=os.setsid,
    )
    assert proc.stdout is not None  # for mypy/static hints
    global CURRENT_PIPELINE
    CURRENT_PIPELINE = proc
    try:
        while True:
            chunk = proc.stdout.read(BUFFER_SIZE)
            if not chunk:
                break
            try:
                conn.sendall(chunk)
            except (BrokenPipeError, ConnectionResetError):
                break
    finally:
        _terminate_current_pipeline()


def serve(host: str, port: int, cmd: str) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(1)
        print(f"[tcp-wrapper] listening on {host}:{port}", flush=True)
        while True:
            conn: Optional[socket.socket] = None
            addr = None
            try:
                conn, addr = srv.accept()
                print(f"[tcp-wrapper] client connected from {addr}", flush=True)
                run_pipeline_to_socket(cmd, conn)
            except KeyboardInterrupt:
                raise
            finally:
                if conn:
                    conn.close()
                    if addr:
                        print(f"[tcp-wrapper] client disconnected {addr}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start shell pipeline on incoming TCP connections.")
    parser.add_argument("--host", default="0.0.0.0", help="Listen host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, required=True, help="Listen port")
    parser.add_argument(
        "--cmd",
        required=True,
        help="Shell pipeline (bash -lc) that emits MPEG data on stdout",
    )
    args = parser.parse_args()
    try:
        serve(args.host, args.port, args.cmd)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
