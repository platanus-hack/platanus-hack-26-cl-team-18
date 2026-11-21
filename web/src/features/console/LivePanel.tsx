import { useLive } from "@/lib/live-api";
import { Badge } from "@/shared/ui/badge";
import { Button } from "@/shared/ui/button";
import { Card } from "@/shared/ui/card";
import { Mic, MicOff, Radio } from "lucide-react";
import { livePanelView } from "./live-panel";

/**
 * The station's "En vivo" switch: one button wakes the camera, the mic (on an
 * opted-in screen) and ad generation; "Detener" returns the screen to standby.
 * The panel always shows the backend's answer, never an optimistic guess.
 */
export function LivePanel({ screen }: { screen: string }) {
    const { status, unreachable, failed, pending, apply } = useLive(screen);
    if (status === null) {
        return (
            <Card className="flex items-center gap-3 p-4" aria-label={`En vivo · ${screen}`}>
                <Badge tone={unreachable ? "danger" : "neutral"} appearance="soft">
                    {unreachable ? "Sin conexión con el backend" : "Cargando estado en vivo…"}
                </Badge>
            </Card>
        );
    }
    const view = livePanelView(status);
    return (
        <Card
            className="flex flex-col gap-4 p-4"
            aria-label={`En vivo · ${screen}`}
            data-live={status.live ? "on" : "off"}
        >
            <div className="flex flex-wrap items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                    <Radio className="size-5 text-muted" aria-hidden />
                    <div>
                        <p className="text-metadata text-muted">Estación · {screen}</p>
                        <p className="flex items-center gap-2 text-section font-medium">
                            {view.headline}
                            <Badge tone={view.tone} appearance="soft">
                                {status.live ? "Activo" : "Pausado"}
                            </Badge>
                        </p>
                        <p className="text-compact text-muted">{view.detail}</p>
                    </div>
                </div>
                <div className="flex items-center gap-2">
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
                        {view.toggleLabel}
                    </Button>
                </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
                {view.chips.map((chip) => (
                    <Badge key={chip.key} tone={chip.tone} appearance="soft">
                        {chip.label}
                    </Badge>
                ))}
                {unreachable && (
                    <Badge tone="danger" appearance="soft">
                        Sin conexión: mostrando el último estado conocido
                    </Badge>
                )}
                {failed && (
                    <Badge tone="danger" appearance="soft">
                        No se aplicó el cambio
                    </Badge>
                )}
            </div>
            {view.warnings.map((warning) => (
                <p key={warning} className="text-compact text-warning" role="status">
                    {warning}
                </p>
            ))}
        </Card>
    );
}
