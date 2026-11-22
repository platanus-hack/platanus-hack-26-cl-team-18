import { backendHttpUrl } from "@/lib/config";
import { useLive } from "@/lib/live-api";
import type { ConnectionState } from "@/lib/ws/typed-socket";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Card } from "@/shared/ui/card";
import { Mic, MicOff, Play, Square } from "lucide-react";
import { useEffect, useState } from "react";
import { InputHealthSchema, type InputHealth } from "./input-status";
import { CONNECTION } from "./labels";
import { stationView } from "./station";
import { STATIONS } from "./stations";

/** Poll `/health` (inputs and provider keys): devices change on their own. */
function useHealth(intervalMs = 2000): InputHealth | null {
    const [health, setHealth] = useState<InputHealth | null>(null);
    useEffect(() => {
        const controller = new AbortController();
        let timer: ReturnType<typeof setTimeout>;
        async function poll() {
            try {
                const response = await fetch(`${backendHttpUrl}/health`, {
                    signal: controller.signal,
                });
                if (!response.ok) throw new Error("health unavailable");
                const data: unknown = await response.json();
                if (!controller.signal.aborted) setHealth(InputHealthSchema.parse(data));
            } catch {
                if (!controller.signal.aborted) setHealth(null);
            } finally {
                if (!controller.signal.aborted)
                    timer = setTimeout(() => {
                        void poll();
                    }, intervalMs);
            }
        }
        void poll();
        return () => {
            controller.abort();
            clearTimeout(timer);
        };
    }, [intervalMs]);
    return health;
}

const STATE_TONE = { standby: "neutral", live: "success", degraded: "warning" } as const;

/**
 * The station's control bar: one state word, the one button that matters, and
 * one dot per device. Always shows the backend's answer, never a guess.
 */
export function StationBar({
    screen,
    screens,
    connection,
}: {
    screen: string;
    screens: readonly string[];
    connection: ConnectionState;
}) {
    const { status, unreachable, failed, pending, apply } = useLive(screen);
    const health = useHealth();
    const link = CONNECTION[connection.status];
    return (
        <Card
            className="flex flex-col gap-3 p-4"
            aria-label={`En vivo · ${screen}`}
            data-live={status?.live ? "on" : "off"}
        >
            <div className="flex flex-wrap items-center justify-between gap-4">
                <div className="flex flex-wrap items-center gap-3">
                    <label className="flex items-center gap-2 text-compact text-muted">
                        Estación
                        <select
                            className="rounded-control border border-control-edge bg-surface px-2 py-1 font-mono text-fg"
                            value={screen}
                            onChange={(event) => {
                                const url = new URL(window.location.href);
                                url.searchParams.set("screen", event.target.value);
                                window.location.assign(url.toString());
                            }}
                        >
                            {STATIONS.map((station) => (
                                <option
                                    key={station.id}
                                    value={station.id}
                                    disabled={!station.live && station.id !== screen}
                                >
                                    {station.live ? station.name : `${station.name} · sin señal`}
                                </option>
                            ))}
                            {[...new Set([screen, ...screens])]
                                .filter((id) => !STATIONS.some((station) => station.id === id))
                                .map((id) => (
                                    <option key={id} value={id}>
                                        {id}
                                    </option>
                                ))}
                        </select>
                    </label>
                    {status === null ? (
                        <Badge tone={unreachable ? "danger" : "neutral"} appearance="soft">
                            {unreachable ? "Sin conexión con el backend" : "Cargando…"}
                        </Badge>
                    ) : (
                        <StateWord view={stationView(status, health)} />
                    )}
                </div>
                <div className="flex items-center gap-2">
                    {connection.status !== "open" && !unreachable && (
                        <span
                            className="text-metadata text-warning"
                            title="Conexión de esta consola"
                        >
                            ● {link.label}
                        </span>
                    )}
                    {status && (
                        <Controls
                            status={status}
                            health={health}
                            pending={pending}
                            unreachable={unreachable}
                            apply={apply}
                        />
                    )}
                </div>
            </div>
            {status && <Dots view={stationView(status, health)} />}
            {((unreachable && status !== null) || failed) && (
                <p className="text-compact text-danger" role="status">
                    {unreachable
                        ? "Sin conexión: mostrando el último estado conocido."
                        : "No se aplicó el cambio."}
                </p>
            )}
        </Card>
    );
}

function StateWord({ view }: { view: ReturnType<typeof stationView> }) {
    return (
        <Badge tone={STATE_TONE[view.state]} appearance="soft">
            {view.headline}
        </Badge>
    );
}

function Controls({
    status,
    health,
    pending,
    unreachable,
    apply,
}: {
    status: NonNullable<ReturnType<typeof useLive>["status"]>;
    health: InputHealth | null;
    pending: boolean;
    unreachable: boolean;
    apply: ReturnType<typeof useLive>["apply"];
}) {
    const view = stationView(status, health);
    return (
        <>
            {view.mic && (
                <Button
                    variant="outline"
                    aria-pressed={view.mic.pressed}
                    disabled={unreachable}
                    loading={pending}
                    onClick={() => {
                        apply({ mic: !status.mic });
                    }}
                >
                    {status.mic ? <Mic aria-hidden /> : <MicOff aria-hidden />}
                    {view.mic.label}
                </Button>
            )}
            <Button
                size="lg"
                variant={status.live ? "destructive" : "primary"}
                aria-pressed={status.live}
                disabled={unreachable}
                loading={pending}
                onClick={() => {
                    apply({ live: view.toggleTo });
                }}
            >
                {status.live ? <Square aria-hidden /> : <Play aria-hidden />}
                {view.toggleLabel}
            </Button>
        </>
    );
}

function Dots({ view }: { view: ReturnType<typeof stationView> }) {
    return (
        <div className="flex flex-col gap-2">
            <ul className="flex flex-wrap gap-x-5 gap-y-1" aria-label="Estado de los equipos">
                {view.dots.map((dot) => (
                    <li key={dot.key}>
                        <Badge tone={dot.tone}>{dot.label}</Badge>
                    </li>
                ))}
            </ul>
            {view.warnings.map((warning) => (
                <p key={warning} className="text-compact text-warning" role="status">
                    {warning.split(/(\/screen\?id=\S+)/).map((part, index) =>
                        part.startsWith("/screen?") ? (
                            <a
                                key={index}
                                href={part}
                                target="_blank"
                                rel="noreferrer"
                                className="font-mono underline underline-offset-2"
                            >
                                {part}
                            </a>
                        ) : (
                            part
                        ),
                    )}
                </p>
            ))}
        </div>
    );
}
