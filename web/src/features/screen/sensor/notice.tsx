/**
 * Tells whoever set up the screen why it can't see or hear: a permission prompt
 * waiting, a refused permission, no device, a device another app holds. Without
 * it a blocked camera looked exactly like a working one (the house ad), and the
 * browser's prompt (Firefox asks on every load unless "Remember" is ticked) went
 * unnoticed. Nothing shows while the devices work or are off on purpose.
 */
import { useEffect, useState } from "react";
import type { DeviceStates } from "./link";
import type { DeviceState } from "./vision";

/** A prompt answered within this long never flashes a notice. */
export const ASK_NOTICE_DELAY_MS = 2_000;

type Device = keyof DeviceStates;

export function noticeText(device: Device, state: DeviceState): string | null {
    const camera = device === "camera";
    const name = camera ? "la cámara" : "el micrófono";
    const Name = camera ? "Cámara" : "Micrófono";
    switch (state) {
        case "asking":
            return `Permite ${name}: pulsa «Permitir» en el aviso del navegador y marca «Recordar esta decisión».`;
        case "blocked":
            return `${Name} ${camera ? "bloqueada" : "bloqueado"}: pulsa el ícono tachado junto a la dirección, permite el acceso y recarga.`;
        case "missing":
            return `No hay ${name} ${camera ? "conectada" : "conectado"}.`;
        case "busy":
            return `${Name} en uso por otra aplicación: ciérrala y la pantalla la retoma sola.`;
        default:
            return null;
    }
}

export function SensorNotice({ devices }: { devices: DeviceStates }) {
    const asking = devices.camera === "asking" || devices.mic === "asking";
    // The device report that has been asking for ASK_NOTICE_DELAY_MS (a new report
    // is a new object, so every fresh prompt waits again).
    const [ripe, setRipe] = useState<DeviceStates | null>(null);
    useEffect(() => {
        if (!asking) return;
        const timer = setTimeout(() => {
            setRipe(devices);
        }, ASK_NOTICE_DELAY_MS);
        return () => {
            clearTimeout(timer);
        };
    }, [asking, devices]);
    const askedLong = ripe === devices;
    const lines = (["camera", "mic"] as const)
        .filter((device) => devices[device] !== "asking" || askedLong)
        .map((device) => noticeText(device, devices[device]))
        .filter((text): text is string => text !== null);
    if (lines.length === 0) return null;
    return (
        <div
            role="status"
            className="absolute right-6 bottom-6 max-w-xl space-y-1 rounded-lg bg-black/80 px-5 py-4 text-lg text-white"
            style={{ zIndex: "var(--z-overlay)" }}
        >
            {lines.map((line) => (
                <p key={line}>{line}</p>
            ))}
        </div>
    );
}
