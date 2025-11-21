#!/usr/bin/env bash
set -euo pipefail

API_PORT="${API_PORT:-18081}"
uvicorn_pid=""
xvfb_pid=""
pulseaudio_pid=""

OFFSCREEN_DISPLAY="${OFFSCREEN_DISPLAY:-:99}"
OFFSCREEN_RESOLUTION="${OFFSCREEN_RESOLUTION:-640x360x24}"
ASR_PULSE_SINK="${ASR_PULSE_SINK:-asr_null}"
DEFAULT_CHROMIUM_HEADLESS="/usr/local/bin/chromium-headless"
if [[ -z "${STREAMLINK_WEBBROWSER_EXECUTABLE:-}" ]] && [[ -x "${DEFAULT_CHROMIUM_HEADLESS}" ]]; then
  export STREAMLINK_WEBBROWSER_EXECUTABLE="${DEFAULT_CHROMIUM_HEADLESS}"
fi

log() {
  echo "[entrypoint] $*"
}

start_xvfb() {
  export DISPLAY="${OFFSCREEN_DISPLAY}"
  if pgrep -x Xvfb >/dev/null 2>&1; then
    log "Xvfb already running on ${OFFSCREEN_DISPLAY}"
    return
  fi
  log "Starting Xvfb on ${OFFSCREEN_DISPLAY} (${OFFSCREEN_RESOLUTION})"
  Xvfb "${OFFSCREEN_DISPLAY}" -screen 0 "${OFFSCREEN_RESOLUTION}" -nolisten tcp -ac &
  xvfb_pid=$!
}

detect_pulse_monitor() {
  if [[ -n "${ASR_PULSE_MONITOR:-}" ]]; then
    echo "${ASR_PULSE_MONITOR}"
    return
  fi
  local direct_name="${ASR_PULSE_SINK}.monitor"
  local legacy_name="alsa_output.${ASR_PULSE_SINK}.monitor"
  local first_source=""
  while read -r _ name _; do
    if [[ -z "${first_source}" ]]; then
      first_source="${name}"
    fi
    if [[ "${name}" == "${direct_name}" ]] || [[ "${name}" == "${legacy_name}" ]]; then
      echo "${name}"
      return
    fi
  done < <(pactl list short sources 2>/dev/null || true)
  if [[ -n "${first_source}" ]]; then
    echo "${first_source}"
  else
    echo "${direct_name}"
  fi
}

start_pulseaudio() {
  if ! pgrep -x pulseaudio >/dev/null 2>&1; then
    log "Starting PulseAudio daemon"
    pulseaudio -D --exit-idle-time=-1 --log-level=info >/dev/null 2>&1 || true
    pulseaudio_pid=$(pgrep -x pulseaudio || true)
    sleep 1
  fi
  if ! pactl list short sinks | grep -q "${ASR_PULSE_SINK}"; then
    log "Creating PulseAudio null sink ${ASR_PULSE_SINK}"
    pactl load-module module-null-sink \
      "sink_name=${ASR_PULSE_SINK}" \
      "latency_msec=30" \
      "sink_properties=device.description=ASRNullSink" >/dev/null 2>&1 || true
  fi
  local actual_sink="${ASR_PULSE_SINK}"
  if ! pactl list short sinks | awk '{print $2}' | grep -qx "${ASR_PULSE_SINK}"; then
    actual_sink="$(pactl list short sinks | awk 'NR==1 {print $2}')"
    if [[ -z "${actual_sink}" ]]; then
      actual_sink="${ASR_PULSE_SINK}"
    fi
  fi
  export ASR_PULSE_SINK="${actual_sink}"
  if [[ -n "${actual_sink}" ]]; then
    pactl set-default-sink "${actual_sink}" >/dev/null 2>&1 || true
  fi
  export PULSE_SINK="${actual_sink}"
  local monitor_name
  monitor_name="$(detect_pulse_monitor)"
  export ASR_PULSE_MONITOR="${monitor_name}"
  export PULSE_SOURCE="${monitor_name}"
  if pactl list short sources | grep -q "${monitor_name}"; then
    pactl set-default-source "${monitor_name}" >/dev/null 2>&1 || true
  fi
  pulseaudio_pid=$(pgrep -x pulseaudio || true)
}

cleanup() {
  local signal="${1:-TERM}"
  if [[ -n "${xvfb_pid}" ]] && kill -0 "${xvfb_pid}" 2>/dev/null; then
    kill -"${signal}" "${xvfb_pid}" 2>/dev/null || true
  fi
  if [[ -n "${pulseaudio_pid}" ]] && kill -0 "${pulseaudio_pid}" 2>/dev/null; then
    kill -"${signal}" "${pulseaudio_pid}" 2>/dev/null || true
  fi
  if [[ -n "${uvicorn_pid}" ]] && kill -0 "${uvicorn_pid}" 2>/dev/null; then
    kill -"${signal}" "${uvicorn_pid}" 2>/dev/null || true
  fi
}

trap 'cleanup TERM' INT TERM

if [[ -n "${AUTH_STORE:-}" ]]; then
  mkdir -p "$(dirname "${AUTH_STORE}")"
fi

start_xvfb
start_pulseaudio

set +e
python3 -m uvicorn server.web.app:app --host 0.0.0.0 --port "${API_PORT}" &
uvicorn_pid=$!
wait "${uvicorn_pid}"
status=$?
set -e

cleanup TERM
exit "${status}"
