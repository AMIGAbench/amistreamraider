"""Stub transcoding manager for offline development and tests."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Dict, List, Optional

from server.transcode.manager import PipelineMetrics, PipelineStatus, TranscodeProfile


class StubTranscodeManager:
    """Return deterministic pipeline statuses without launching processes."""

    def __init__(self) -> None:
        profile = TranscodeProfile(
            name="stub",
            output_args=[],
            description="Stub profile (no real transcoding)",
            listen_port=5001,
        )
        self._profiles = {profile.name: profile}
        self._pipelines: Dict[str, PipelineStatus] = {}
        self._metrics: Dict[str, PipelineMetrics] = {}
        self._metric_tasks: Dict[str, asyncio.Task] = {}

    def available_profiles(self) -> List[TranscodeProfile]:
        return list(self._profiles.values())

    def get_status(self, op_id: str) -> Optional[PipelineStatus]:
        return self._pipelines.get(op_id)

    def get_metrics(self, op_id: str) -> Optional[PipelineMetrics]:
        status = self._pipelines.get(op_id)
        metrics = self._metrics.get(op_id)
        if not status or not metrics:
            return None
        self._update_metrics(op_id)
        return metrics

    async def start_pipeline(
        self,
        *,
        op_id: str,
        channel: str,
        profile_name: str,
    ) -> PipelineStatus:
        profile = self._profiles.get(profile_name) or next(iter(self._profiles.values()))
        status = PipelineStatus(
            op_id=op_id,
            channel=channel,
            profile=profile.name,
            started_at=time.time(),
            state="running",
            listen_host="127.0.0.1",
            listen_port=profile.listen_port or 5001,
            outfile=Path(f"/tmp/{op_id}.mpg"),
            log_file=Path(f"/tmp/{op_id}.log"),
            pgid=None,
        )
        self._pipelines[op_id] = status
        metrics = PipelineMetrics(op_id=op_id)
        self._metrics[op_id] = metrics
        self._metric_tasks[op_id] = asyncio.create_task(self._simulate_metrics(op_id))
        return status

    async def stop_pipeline(self, op_id: str) -> bool:
        status = self._pipelines.get(op_id)
        if not status:
            return False
        status.state = "stopped"
        status.ended_at = time.time()
        self._update_metrics(op_id)
        task = self._metric_tasks.pop(op_id, None)
        if task:
            task.cancel()
        return True

    def remove_pipeline(self, op_id: str) -> None:
        self._pipelines.pop(op_id, None)
        metrics_task = self._metric_tasks.pop(op_id, None)
        if metrics_task:
            metrics_task.cancel()
        self._metrics.pop(op_id, None)

    async def _simulate_metrics(self, op_id: str) -> None:
        try:
            while True:
                status = self._pipelines.get(op_id)
                if not status or status.state != "running":
                    self._update_metrics(op_id)
                    return
                self._update_metrics(op_id)
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return

    def _update_metrics(self, op_id: str) -> None:
        status = self._pipelines.get(op_id)
        metrics = self._metrics.get(op_id)
        if not status or not metrics:
            return
        now = time.time()
        elapsed = max(0.0, now - status.started_at)
        metrics.out_time_ms = int(elapsed * 1000)
        if elapsed == 0:
            metrics.bytes_total = 0
            metrics.bitrate_kbps = 0
        else:
            # Simulate roughly 1.5 Mbit/s output stream
            simulated_bps = 1_500_000 / 8  # bytes per second
            metrics.bytes_total = int(simulated_bps * elapsed)
            metrics.bitrate_kbps = 1500
        metrics.updated_at = now
