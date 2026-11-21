#!/bin/sh
set -eu
lock_hash=$(sha256sum package-lock.json | cut -d' ' -f1)
installed_hash=""
if [ -f node_modules/.package-lock.sha256 ]; then
  installed_hash=$(cat node_modules/.package-lock.sha256)
fi
if [ ! -x node_modules/.bin/vite ] || [ "$installed_hash" != "$lock_hash" ]; then
  npm ci
  printf '%s\n' "$lock_hash" > node_modules/.package-lock.sha256
fi
exec "$@"
