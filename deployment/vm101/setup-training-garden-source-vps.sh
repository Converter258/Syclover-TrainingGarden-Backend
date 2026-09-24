#!/bin/sh
set -eu

bundle=/root/training-garden-automation
id -u tg-source >/dev/null 2>&1 || useradd --system --create-home --shell /usr/bin/git-shell tg-source
install -d -m 0750 -o tg-source -g tg-source /srv/training-garden /srv/training-garden/mirror
chown -R tg-source:tg-source /srv/training-garden/mirror
install -m 0755 "$bundle/training-garden-source-sync.py" /usr/local/sbin/training-garden-source-sync
install -m 0644 "$bundle/training-garden-source-sync.service" /etc/systemd/system/training-garden-source-sync.service
install -m 0644 "$bundle/training-garden-source-sync.timer" /etc/systemd/system/training-garden-source-sync.timer
systemctl daemon-reload
systemctl start training-garden-source-sync.service
systemctl enable --now training-garden-source-sync.timer
systemctl is-active training-garden-source-sync.timer
