#!/bin/sh
set -eu

install -d -m 0700 -o root -g root /etc/training-garden
if [ ! -f /etc/training-garden/source_key ]; then
    ssh-keygen -q -t ed25519 -N '' -C vm101-training-garden-source \
        -f /etc/training-garden/source_key
fi
chmod 0600 /etc/training-garden/source_key
chmod 0644 /etc/training-garden/source_key.pub
cat /etc/training-garden/source_key.pub
