#!/bin/sh
set -eu
umask 027

root=/srv/training-garden/mirror
mkdir -p "$root"

for repo in Syclover-TrainingGarden-Backend Syclover-TrainingGarden-Frontend; do
    mirror="$root/$repo.git"
    if [ ! -d "$mirror" ]; then
        git init --bare "$mirror"
        git -C "$mirror" remote add fork "https://github.com/Converter258/$repo.git"
        git -C "$mirror" remote add upstream "https://github.com/K4per/$repo.git"
    fi
    git -C "$mirror" fetch --no-tags fork \
        +refs/heads/deploy/vm101:refs/heads/deploy-vm101
    git -C "$mirror" fetch --no-tags upstream \
        +refs/heads/main:refs/heads/upstream-main
done

echo 'Training Garden source mirrors updated'
