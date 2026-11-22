#!/bin/sh
# Start the GPU service as the unprivileged `gpu` user.
#
# /models is a volume: a fresh one, or one created by an older image that ran
# as root, may be root-owned. While still root, hand a root-owned /models to `gpu`
# once (it holds a few weight files and an optional TensorRT .engine), then drop
# privileges for good. A /models already owned by someone else (gpu after the
# first start, or a bind-mounted host directory) is never chowned.
# Started with `docker run --user ...` instead, we are already unprivileged.
set -eu

if [ "$(id -u)" = "0" ]; then
    if [ "$(stat -c %u /models)" = "0" ]; then
        chown -R gpu:gpu /models
    fi
    exec setpriv --reuid=gpu --regid=gpu --init-groups --inh-caps=-all \
        env HOME=/home/gpu "$0" "$@"
fi

# Seed the volume with the baked weights without overwriting what it has
# (a TensorRT .engine exported on an earlier start stays next to them).
if [ -w /models ]; then
    cp --update=none /opt/weights/*.pt /models/
else
    for weight in /opt/weights/*.pt; do
        if [ ! -e "/models/$(basename "$weight")" ]; then
            echo "docker-entrypoint: /models is not writable by uid $(id -u) and has no" \
                "$(basename "$weight"). Start without --user (the entrypoint fixes a" \
                "root-owned volume), or chown the volume to that uid." >&2
            exit 1
        fi
    done
    echo "docker-entrypoint: /models is read-only for uid $(id -u); using its weights" \
        "as they are (a TensorRT export will not persist)." >&2
fi
exec "$@"
