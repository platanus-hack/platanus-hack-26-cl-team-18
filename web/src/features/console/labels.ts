import type {
    DegradationFlag,
    ExclusionReason,
    MoneyDTO,
    SegmentMatchKind,
    SignalSource,
} from "@/contract";
import { moneyCl } from "@/lib/format";
import type { ConnectionState } from "@/lib/ws/typed-socket";
import type { Tone } from "@/shared/ui/badge";

export const CONNECTION: Record<ConnectionState["status"], { tone: Tone; label: string }> = {
    open: { tone: "success", label: "En vivo" },
    connecting: { tone: "info", label: "Conectando" },
    reconnecting: { tone: "warning", label: "Reconectando" },
    closed: { tone: "danger", label: "Sin conexión" },
};

export const EXCLUSION: Record<ExclusionReason, string> = {
    competitor: "Competidor",
    safety: "Protección de menores",
    budget: "Sin presupuesto",
    below_reserve: "Bajo el mínimo",
    no_layout: "Sin formato",
    not_partner: "No es socia",
    out_of_stock: "Sin stock",
    inactive: "Inactiva",
    no_basket_offer: "Sin oferta conjunta",
    outbid: "Superada",
    invalid_bid: "Puja inválida",
};

export const MATCH: Record<SegmentMatchKind, string> = {
    exact: "exacto",
    broader: "más amplio",
    narrower: "más específico",
    related: "relacionado",
};

export const SOURCE: Record<SignalSource, string> = {
    vision: "cámara",
    vlm: "imagen",
    speech: "voz",
    sound: "sonido",
};

export const DEGRADATION: Record<DegradationFlag, string> = {
    agent_offline: "Cámara desconectada",
    vlm_unavailable: "Etiquetas de imagen no disponibles",
    copy_fallback: "Texto de respaldo",
    voice_fallback: "Sin voz",
    image_fallback: "Imagen de respaldo",
    house_ad: "Aviso de la casa",
};

/** Latency trace steps, named for an operator instead of by field. */
const STEP: Record<string, string> = {
    candidate: "Candidato",
    measured: "Medido",
    detected: "Detectado",
    published: "Publicado",
    received: "Recibido",
    moment_changed: "Momento",
    cleared: "Subasta",
    ready: "Creatividad lista",
    displayed: "En pantalla",
};

export const stepLabel = (step: string) => STEP[step] ?? step;

export const money = (m: MoneyDTO) => moneyCl(m.amount);

export const ms = (value: number | null) =>
    value === null ? "—" : value === 0 ? "<1 ms" : `${Math.round(value)} ms`;

/** "+7 %", "−21 %", "0 %": the sign is part of the fact. */
export const signedPct = (ratio: number) => {
    const n = Math.round(ratio * 100);
    return `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n)} %`;
};

/** "1 persona", "2 personas". */
export const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
