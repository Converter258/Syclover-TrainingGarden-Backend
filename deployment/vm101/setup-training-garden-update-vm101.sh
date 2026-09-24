#!/bin/sh
set -eu

bundle=/home/usyc/training-garden-automation
key=/etc/training-garden/source_key
hosts=/etc/training-garden/source_known_hosts
export GIT_SSH_COMMAND="ssh -i $key -o UserKnownHostsFile=$hosts -o BatchMode=yes -o StrictHostKeyChecking=yes"
release=$(readlink -f /opt/training-garden/current)
manifest=$release/RELEASE_MANIFEST.txt
test -f "$manifest"

install -m 0755 "$bundle/training-garden-update.py" /usr/local/sbin/training-garden-update
install -m 0755 "$bundle/training-garden-backup.sh" /usr/local/sbin/training-garden-backup
install -m 0644 "$bundle/training-garden-update.service" /etc/systemd/system/training-garden-update.service
install -m 0644 "$bundle/training-garden-update.timer" /etc/systemd/system/training-garden-update.timer
python3 -m py_compile /usr/local/sbin/training-garden-update
sh -n /usr/local/sbin/training-garden-backup

source_checkout=$(mktemp -d /tmp/tg-source-setup.XXXXXX)
trap 'rm -rf "$source_checkout"' EXIT
git clone -q tg-source@47.109.46.12:/srv/training-garden/delivery.git "$source_checkout/delivery"
python3 - "$source_checkout/delivery/manifest.json" "$manifest" <<'PY'
import json
import sys
from pathlib import Path

sources = json.loads(Path(sys.argv[1]).read_text())
manifest = Path(sys.argv[2])
existing = manifest.read_text()
with manifest.open("a") as output:
    for repo, versions in sources.items():
        upstream = versions["upstream-main"]
        if f"{repo}: {upstream}" not in existing:
            print(f"Current release differs from {repo} upstream; first check will deploy it")
            continue
        for branch in ("upstream-main", "deploy-vm101"):
            line = f"{repo}/{branch}: {versions[branch]}"
            if line not in existing:
                output.write(line + "\n")
PY

/usr/local/sbin/training-garden-backup
systemctl daemon-reload
systemctl enable --now training-garden-update.timer
systemctl start training-garden-update.service
systemctl is-active training-garden-update.timer
cat /var/lib/training-garden/updater/status.json
