#!/bin/sh
set -eu
umask 077
Xvfb :99 -screen 0 1280x900x24 -nolisten tcp &
xvfb_pid=$!
x11vnc_pid=''
web_pid=''
task_pid=''
cleanup() {
    if [ -n "$task_pid" ]; then
        kill -TERM "$task_pid" 2>/dev/null || true
        wait "$task_pid" 2>/dev/null || true
    fi
    [ -z "$web_pid" ] || kill "$web_pid" 2>/dev/null || true
    [ -z "$x11vnc_pid" ] || kill "$x11vnc_pid" 2>/dev/null || true
    kill "$xvfb_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
sleep 1
x11vnc -display :99 -localhost -rfbport 5900 -nopw -forever -shared -quiet &
x11vnc_pid=$!
websockify --web=/usr/share/novnc/ 6080 localhost:5900 &
web_pid=$!
python -m app.main manual-login &
task_pid=$!
wait "$task_pid"
