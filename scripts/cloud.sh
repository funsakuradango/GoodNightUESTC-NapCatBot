#!/bin/sh
# Keep the existing NapCat network attached for every production command.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
exec docker compose -f compose.yaml -f compose.napcat.yaml -f compose.cloud.yaml "$@"
