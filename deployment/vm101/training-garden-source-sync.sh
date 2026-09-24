#!/bin/sh
set -eu
umask 027

root=/srv/training-garden/mirror
mkdir -p "$root"

fetch_retry() {
    attempt=0
    until timeout 20 git -C "$1" fetch --no-tags "$2" "$3"; do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge 3 ]; then
            return 1
        fi
        sleep 5
    done
}

for repo in Syclover-TrainingGarden-Backend Syclover-TrainingGarden-Frontend; do
    mirror="$root/$repo.git"
    if [ ! -d "$mirror" ]; then
        git init --bare "$mirror"
        git -C "$mirror" remote add fork "https://github.com/Converter258/$repo.git"
        git -C "$mirror" remote add upstream "https://github.com/K4per/$repo.git"
    fi
    fetch_retry "$mirror" fork \
        +refs/heads/deploy/vm101:refs/heads/deploy-vm101
    fetch_retry "$mirror" upstream \
        +refs/heads/main:refs/heads/upstream-main
done

echo 'Training Garden source mirrors updated'
