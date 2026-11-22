/**
 * The backend repeats `live.status` every 5 s to `?live=1` sockets (the TV and the
 * station console; app/bootstrap/container.py). Three missed heartbeats mean the
 * link died without a reset: TypedSocket's `idleTimeoutMs` reconnects it.
 */
export const HEARTBEAT_IDLE_MS = 15_000;
