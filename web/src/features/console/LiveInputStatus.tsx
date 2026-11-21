import { backendHttpUrl } from "@/lib/config";
import { Badge } from "@/shared/ui/badge";
import { useEffect, useState } from "react";
import { InputHealthSchema, inputLabels } from "./input-status";

export function LiveInputStatus({ screen }: { screen: string }) {
    const [health, setHealth] = useState<ReturnType<typeof InputHealthSchema.parse> | null>(null);
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
                    }, 2000);
            }
        }
        void poll();
        return () => {
            controller.abort();
            clearTimeout(timer);
        };
    }, []);
    if (health === null)
        return (
            <Badge tone="warning" appearance="soft">
                Estado de entradas no disponible
            </Badge>
        );
    const labels = inputLabels(health, screen);
    const input = health.inputs[screen];
    return (
        <div className="flex flex-wrap items-center gap-2" aria-label={`Entradas · ${screen}`}>
            <span className="text-metadata text-muted">Entradas · {screen}</span>
            <Badge tone={input?.camera_recent ? "success" : "warning"} appearance="soft">
                {labels.camera}
            </Badge>
            <Badge tone={input?.speech_enabled ? "info" : "neutral"} appearance="soft">
                {labels.speech}
            </Badge>
            <Badge tone={input?.player_connected ? "success" : "warning"} appearance="soft">
                {labels.player}
            </Badge>
        </div>
    );
}
