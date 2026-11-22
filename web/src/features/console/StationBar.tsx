import { cn } from "@/lib/cn";
import { backendHttpUrl } from "@/lib/config";
import { useLive } from "@/lib/live-api";
import type { ConnectionState } from "@/lib/ws/typed-socket";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Check, ChevronDown, Mic, MicOff, Play } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
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
        <div
            className="flex flex-col gap-3"
            aria-label={`En vivo · ${screen}`}
            data-live={status?.live ? "on" : "off"}
        >
            <div className="flex flex-wrap items-center justify-between gap-4">
                <div className="flex flex-wrap items-center gap-3">
                    <ScreenPicker screen={screen} />
                    {status === null && !unreachable && (
                        <Badge tone="neutral" appearance="soft">
                            Cargando…
                        </Badge>
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
        </div>
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
 * The screen selector: a dropdown over the station's screens. MUT_01 is the real,
 * live-capable screen and stays selected; MUT_02… are listed but not selectable,
 * so only one screen can ever be live.
 */
function ScreenPicker({ screen }: { screen: string }) {
    const screens = stationScreens(screen);
    const current = screens.find((entry) => entry.id !== null) ?? {
        label: screen,
        id: screen,
    };
    const available = screens.filter((entry) => entry.id !== null).length;
    const [open, setOpen] = useState(false);
    const [focused, setFocused] = useState(0);
    const root = useRef<HTMLDivElement>(null);
    const trigger = useRef<HTMLButtonElement>(null);
    const items = useRef<(HTMLLIElement | null)[]>([]);
    const listId = useId();

    useEffect(() => {
        if (!open) return;
        function onPointer(event: PointerEvent) {
            if (!root.current?.contains(event.target as Node)) setOpen(false);
        }
        document.addEventListener("pointerdown", onPointer);
        return () => {
            document.removeEventListener("pointerdown", onPointer);
        };
    }, [open]);

    useEffect(() => {
        if (open) items.current[focused]?.focus();
    }, [open, focused]);

    function close() {
        setOpen(false);
        trigger.current?.focus();
    }

    function onListKey(event: React.KeyboardEvent) {
        const last = screens.length - 1;
        if (event.key === "ArrowDown") setFocused((i) => (i >= last ? 0 : i + 1));
        else if (event.key === "ArrowUp") setFocused((i) => (i <= 0 ? last : i - 1));
        else if (event.key === "Home") setFocused(0);
        else if (event.key === "End") setFocused(last);
        else if (event.key === "Escape" || event.key === "Tab") close();
        else if (event.key === "Enter" || event.key === " ") {
            if (screens[focused]?.id !== null) close();
        } else return;
        event.preventDefault();
    }

    return (
        <div ref={root} className="relative">
            <button
                ref={trigger}
                type="button"
                aria-haspopup="listbox"
                aria-expanded={open}
                aria-controls={listId}
                aria-label={`Pantalla ${current.label}`}
                onClick={() => {
                    setFocused(screens.indexOf(current));
                    setOpen((value) => !value);
                }}
                onKeyDown={(event) => {
                    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                        event.preventDefault();
                        setFocused(screens.indexOf(current));
                        setOpen(true);
                    }
                }}
                className={cn(
                    "group -ml-2 inline-flex h-8 items-center gap-2 rounded-control px-2",
                    "text-compact font-medium tracking-tight text-fg",
                    "transition-colors duration-(--duration-fast) hover:bg-surface-hover",
                    "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                    open && "bg-surface-hover",
                )}
            >
                <span aria-hidden className="size-1.5 rounded-full bg-success" />
                <span>{current.label}</span>
                <ChevronDown
                    aria-hidden
                    className={cn(
                        "size-3.5 text-subtle transition-transform duration-(--duration-base)",
                        open && "rotate-180",
                    )}
                />
            </button>
            {open && (
                <div
                    className="absolute top-full left-0 z-50 mt-1.5 w-60 origin-top-left rounded-container border border-border bg-surface-raised p-1 shadow-overlay"
                    style={{ animation: "menu-in var(--duration-base) ease-out" }}
                >
                    <div className="flex items-center justify-between px-2.5 pt-1.5 pb-2">
                        <span className="eyebrow">Pantallas</span>
                        <span className="font-mono text-metadata text-subtle">
                            {available} de {screens.length} activas
                        </span>
                    </div>
                    <ul
                        id={listId}
                        role="listbox"
                        aria-label="Pantallas"
                        aria-activedescendant={`${listId}-${focused}`}
                        onKeyDown={onListKey}
                        className="flex flex-col gap-0.5"
                    >
                        {screens.map((entry, index) => {
                            const selectable = entry.id !== null;
                            const selected = entry === current;
                            return (
                                <li
                                    key={entry.label}
                                    id={`${listId}-${index}`}
                                    ref={(node) => {
                                        items.current[index] = node;
                                    }}
                                    role="option"
                                    tabIndex={index === focused ? 0 : -1}
                                    aria-selected={selected}
                                    aria-disabled={!selectable}
                                    onPointerMove={() => {
                                        setFocused(index);
                                    }}
                                    onClick={() => {
                                        if (selectable) close();
                                    }}
                                    className={cn(
                                        "flex h-9 items-center gap-2.5 rounded-control px-2.5 font-mono text-compact outline-none",
                                        "transition-colors duration-(--duration-fast)",
                                        selectable
                                            ? "cursor-pointer text-fg focus:bg-surface-hover"
                                            : "cursor-not-allowed text-subtle focus:bg-surface-hover/50",
                                        selected && "bg-accent-soft/40",
                                    )}
                                >
                                    <span
                                        aria-hidden
                                        className={cn(
                                            "size-1.5 shrink-0 rounded-full",
                                            selectable
                                                ? "bg-success shadow-[0_0_0_3px_var(--color-success-soft)]"
                                                : "bg-border-strong",
                                        )}
                                    />
                                    <span className="flex-1">{entry.label}</span>
                                    {selectable && entry.id !== entry.label && (
                                        <span className="text-metadata text-subtle">
                                            {entry.id}
                                        </span>
                                    )}
                                    <Check
                                        aria-hidden
                                        className={cn(
                                            "size-4 text-primary",
                                            !selected && "invisible",
                                        )}
                                    />
                                </li>
                            );
                        })}
                    </ul>
                </div>
            )}
        </div>
    );
}
