#!/usr/bin/env bash
# Display a private, short-lived QR from the CLI container; never submits checkin.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data
chmod 700 data
container="uestc-wechat-authorize-$$"
qr="$PWD/data/wechat-authorization.png"
cleanup() {
  rm -f "$qr"
  docker stop --time 3 "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM
docker compose run --rm --name "$container" -e TASK_TIMEOUT_SECONDS=360 cli wechat-authorize &
runner=$!
shown=false
while kill -0 "$runner" 2>/dev/null; do
  if [ "$shown" = false ] && docker cp "$container:/app/data/wechat-authorization.png" "$qr" 2>/dev/null; then
    chmod 600 "$qr"
    printf '请用已绑定学校账号的微信扫码确认：%s\n' "$qr"
    if command -v open >/dev/null; then open "$qr"; fi
    shown=true
  fi
  sleep 1
done
wait "$runner"
