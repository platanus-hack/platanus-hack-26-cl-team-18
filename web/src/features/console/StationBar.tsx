import { backendHttpUrl } from "@/lib/config";
import { useLive } from "@/lib/live-api";
import type { ConnectionState } from "@/lib/ws/typed-socket";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Card } from "@/shared/ui/card";
import { Mic, MicOff, Play } from "lucide-react";
import { useEffect, useState } from "react";
import { InputHealthSchema, type InputHealth } from "./input-status";
import { CONNECTION } from "./labels";
import { stationScreens, stationView } from "./station";

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
    connection,
}: {
    screen: string;
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
                    <ScreenPicker screen={screen} />
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
            {!status.live && (
                <Button
                    size="lg"
                    variant="primary"
                    disabled={unreachable}
                    loading={pending}
                    onClick={() => {
                        apply({ live: true });
                    }}
                >
                    <Play aria-hidden />
                    {view.toggleLabel}
                </Button>
            )}
        </>
    );
}
/**
 * The screen selector: MUT_01 is the real screen and stays selected; MUT_02… are
 * display-only (disabled, "En espera") so only one screen can ever be live.
 */
function ScreenPicker({ screen }: { screen: string }) {
    return (
        <div role="group" aria-label="Pantallas" className="flex flex-wrap items-center gap-1">
            {stationScreens(screen).map((entry) =>
                entry.id !== null ? (
                    <Button
                        key={entry.label}
                        size="sm"
                        variant="primary"
                        aria-pressed
                        title={`Pantalla ${entry.label}`}
                    >
                        <span className="font-mono">{entry.label}</span>
                    </Button>
                ) : (
                    <Button
                        key={entry.label}
                        size="sm"
                        variant="outline"
                        aria-pressed={false}
                        disabled
                        title={`${entry.label} · En espera`}
                    >
                        <span className="font-mono">{entry.label}</span>
                        <span className="text-metadata text-muted">En espera</span>
                    </Button>
                ),
            )}
        </div>
    );
}
