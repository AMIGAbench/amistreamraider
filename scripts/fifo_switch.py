#!/usr/bin/env python3
"""Feed a FIFO with placeholder TS until live data arrives, then switch to live."""

from __future__ import annotations

import argparse
import fcntl
import os
import signal
import subprocess
import sys
import time
from typing import Optional

CHUNK_SIZE = 188 * 256  # 48 KiB aligned to TS packets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FIFO writer with placeholder→live switch")
    parser.add_argument("--fifo", required=True, help="Path to FIFO that will receive MPEG-TS data")
    parser.add_argument("--placeholder-cmd", required=True, help="Command producing placeholder TS on stdout")
    parser.add_argument("--live-cmd", required=True, help="Command producing live TS on stdout")
    parser.add_argument("--switch-grace", type=float, default=0.0, help="Optional seconds to wait before killing placeholder")
    return parser.parse_args()


def _launch(cmd: str) -> subprocess.Popen:
    return subprocess.Popen(
        ["bash", "-lc", cmd],
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        bufsize=0,
        preexec_fn=os.setsid,
    )


def _terminate(proc: subprocess.Popen, name: str) -> None:
    if not proc:
        return
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def main() -> None:
    args = parse_args()
    fifo_path = args.fifo
    placeholder_cmd = args.placeholder_cmd
    live_cmd = args.live_cmd

    print(f"[fifo-switch] starting placeholder: {placeholder_cmd}", file=sys.stderr, flush=True)
    placeholder_proc = _launch(placeholder_cmd)
    if not placeholder_proc.stdout:
        raise RuntimeError("placeholder process has no stdout")

    print(f"[fifo-switch] starting live chain: {live_cmd}", file=sys.stderr, flush=True)
    live_proc = _launch(live_cmd)
    if not live_proc.stdout:
        raise RuntimeError("live process has no stdout")

    live_fd = live_proc.stdout.fileno()
    original_flags = fcntl.fcntl(live_fd, fcntl.F_GETFL)
    fcntl.fcntl(live_fd, fcntl.F_SETFL, original_flags | os.O_NONBLOCK)

    fifo_fd: Optional[int] = None
    try:
        fifo_fd = os.open(fifo_path, os.O_WRONLY)
        live_started = False
        last_placeholder_activity = time.time()

        while not live_started:
            if live_proc.poll() is not None:
                raise RuntimeError("live process exited before producing data")
            try:
                live_chunk = live_proc.stdout.read(CHUNK_SIZE)
            except BlockingIOError:
                live_chunk = None
            if live_chunk:
                if args.switch_grace:
                    time.sleep(args.switch_grace)
                print("[fifo-switch] live data detected, switching over", file=sys.stderr, flush=True)
                live_started = True
                fcntl.fcntl(live_fd, fcntl.F_SETFL, original_flags)
                _terminate(placeholder_proc, "placeholder")
                placeholder_proc.stdout.close()
                os.write(fifo_fd, live_chunk)
                break
            placeholder_chunk = placeholder_proc.stdout.read(CHUNK_SIZE)
            if placeholder_chunk:
                os.write(fifo_fd, placeholder_chunk)
                last_placeholder_activity = time.time()
            else:
                if placeholder_proc.poll() is not None:
                    raise RuntimeError("placeholder process exited unexpectedly")
                if time.time() - last_placeholder_activity > 5:
                    print("[fifo-switch] waiting for placeholder data...", file=sys.stderr, flush=True)
                    last_placeholder_activity = time.time()

        while True:
            live_chunk = live_proc.stdout.read(CHUNK_SIZE)
            if live_chunk:
                os.write(fifo_fd, live_chunk)
                continue
            if live_proc.poll() is not None:
                break

    except BrokenPipeError:
        print("[fifo-switch] FIFO reader closed, stopping", file=sys.stderr, flush=True)
    finally:
        _terminate(placeholder_proc, "placeholder")
        _terminate(live_proc, "live")
        if fifo_fd is not None:
            os.close(fifo_fd)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
