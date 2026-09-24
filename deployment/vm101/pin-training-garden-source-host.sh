#!/bin/sh
set -eu

keyfile=$(mktemp)
trap 'rm -f "$keyfile"' EXIT
ssh-keyscan -T 10 -t ed25519 47.109.46.12 2>/dev/null > "$keyfile"
test -s "$keyfile"
ssh-keygen -lf "$keyfile" -E sha256 | \
    grep -Fq 'SHA256:JjgSGvZtLyzLaZg5JLYkwX3kZcq0L/r0SWanRTIrHoc'
install -m 0644 "$keyfile" /etc/training-garden/source_known_hosts
echo 'VPS SSH host key pinned'
