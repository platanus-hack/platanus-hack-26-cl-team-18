#!/bin/sh
# Start the GPU service as the unprivileged `gpu` user.
#
# /models is a volume: a fresh one, or one created by an older image that ran
# as root, may be root-owned. While still root, hand it to `gpu` (it holds a few
# weight files and an optional TensorRT .engine), then drop privileges for good.
# Started with `docker run --user ...` instead, we are already unprivileged.
set -eu

if [ "$(id -u)" = "0" ]; then
    chown -R gpu:gpu /models
    exec setpriv --reuid=gpu --regid=gpu --init-groups --inh-caps=-all \
        env HOME=/home/gpu "$0" "$@"
fi

# Seed the volume with the baked weights without overwriting what it has
# (a TensorRT .engine exported on an earlier start stays next to them).
cp --update=none /opt/weights/*.pt /models/
exec "$@"
