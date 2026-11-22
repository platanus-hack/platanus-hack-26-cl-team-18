import { cn } from "@/lib/cn";
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
                        !unreachable && (
                            <Badge tone="neutral" appearance="soft">
                                Cargando…
                            </Badge>
                        )
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
        <div
            role="group"
            aria-label="Pantallas"
            className="inline-flex max-w-full items-center gap-0.5 overflow-x-auto rounded-lg border border-border-subtle bg-surface-sunken p-0.5"
        >
            {stationScreens(screen).map((entry) => {
                const active = entry.id !== null;
                return (
                    <button
                        key={entry.label}
                        type="button"
                        aria-pressed={active}
                        disabled={!active}
                        title={active ? `Pantalla ${entry.label}` : `${entry.label} · En espera`}
                        className={cn(
                            "inline-flex h-7 shrink-0 items-center gap-2 rounded-md px-2.5 font-mono text-compact tracking-tight transition-colors",
                            "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                            active
                                ? "bg-surface-raised text-fg shadow-sm ring-1 ring-border"
                                : "cursor-not-allowed text-subtle",
                        )}
                    >
                        <span
                            aria-hidden
                            className={cn(
                                "size-1.5 rounded-full",
                                active
                                    ? "bg-success shadow-[0_0_0_3px_var(--color-success-soft)]"
                                    : "bg-border-strong",
                            )}
                        />
                        {entry.label}
                        {!active && <span className="sr-only">En espera</span>}
                    </button>
                );
            })}
        </div>
    );
}
