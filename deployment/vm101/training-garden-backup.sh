#!/bin/sh
set -eu
umask 077

backup_root=/var/backups/training-garden
stamp=$(date +%Y%m%d-%H%M%S)
database_mount=$(docker volume inspect training-garden_backend_data --format '{{.Mountpoint}}')
storage_mount=$(docker volume inspect training-garden_backend_storage --format '{{.Mountpoint}}')

install -d -m 0700 "$backup_root/database" "$backup_root/storage" "$backup_root/config"

python3 - "$database_mount/training_garden.db" "$backup_root/database/$stamp.db" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
    result = target.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"SQLite backup integrity check failed: {result}")
finally:
    target.close()
    source.close()
PY

tar -C "$storage_mount" -czf "$backup_root/storage/$stamp.tar.gz" .
cp /etc/training-garden/platform.env "$backup_root/config/$stamp.env"
manifest=RELEASE_MANIFEST.txt
if [ ! -f "/opt/training-garden/current/$manifest" ]; then
    manifest=RELEASE_MANIFEST
fi
tar -C /opt/training-garden/current -czf "$backup_root/config/$stamp-deploy.tar.gz" \
    compose.yaml compose.vm101.yaml "$manifest"

printf 'Backup completed: %s\n' "$stamp"
