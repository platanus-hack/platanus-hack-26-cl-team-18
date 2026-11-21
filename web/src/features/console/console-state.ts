import type {
    ConsoleAuction,
    ConsoleBook,
    ConsoleMoment,
    ConsoleNext,
    ConsoleOutbound,
    ConsoleReceipt,
    ConsoleRedemption,
    CreativeReady,
    CreativeUpgraded,
    DegradationFlag,
    DevPerception,
    LatencyTrace,
} from "@/contract";
import type { ConnectionState } from "@/lib/ws/typed-socket";
import { glassToGlassMs } from "./metrics";

/** Everything the console shows, per screen, reduced from the console socket. */

export const LATENCY_WINDOW = 200;

export interface ScreenView {
    readonly moment: ConsoleMoment | null;
    readonly auction: ConsoleAuction | null;
    readonly lastTrace: LatencyTrace | null;
    readonly glassToGlassMs: readonly number[];
    readonly degradation: readonly DegradationFlag[];
    readonly lastCreativeId: string | null;
    readonly book: ConsoleBook | null;
    /** Newest first; the moment receipts this console has seen. */
    readonly receipts: readonly ConsoleReceipt[];
    /** Newest first; QR/promo redemptions credited to every brand in the basket. */
    readonly redemptions: readonly ConsoleRedemption[];
    /** What the screen airs now (a station console gets it; ?live=1). */
    readonly onAir: CreativeReady | CreativeUpgraded | null;
    /** Newest first: the loop narrated, one line per fact. */
    readonly events: readonly StationEvent[];
    /** The creative waiting out the on-air one's minimum time (a held swap). */
    readonly next: ConsoleNext | null;
    /** The camera's latest detections (from /ws/dev, demo key only) and when they came. */
    readonly perception: { readonly message: DevPerception; readonly at: number } | null;
}

export interface StationEvent {
    readonly at: number;
    readonly text: string;
    readonly tone: "neutral" | "success" | "info" | "warning";
}

export const EVENT_WINDOW = 40;

export const RECEIPT_WINDOW = 20;
export const REDEMPTION_WINDOW = 10;

export interface ConsoleState {
    readonly connection: ConnectionState;
    readonly screens: Readonly<Record<string, ScreenView>>;
    readonly lastError: string | null;
}

export type ConsoleEvent =
    | { readonly type: "message"; readonly message: ConsoleOutbound; readonly at?: number }
    | { readonly type: "perception"; readonly message: DevPerception; readonly at: number }
    | { readonly type: "connection"; readonly state: ConnectionState };

export const emptyScreen: ScreenView = {
    moment: null,
    auction: null,
    lastTrace: null,
    glassToGlassMs: [],
    degradation: [],
    lastCreativeId: null,
    book: null,
    receipts: [],
    redemptions: [],
    onAir: null,
    events: [],
    next: null,
    perception: null,
};

export const initialConsoleState: ConsoleState = {
    connection: { status: "connecting", attempt: 0 },
    screens: {},
    lastError: null,
};

function withTrace(view: ScreenView, trace: LatencyTrace): ScreenView {
    const sample = glassToGlassMs(trace);
    return {
        ...view,
        lastTrace: trace,
        glassToGlassMs:
            sample === null
                ? view.glassToGlassMs
                : [...view.glassToGlassMs, sample].slice(-LATENCY_WINDOW),
    };
}

function update(
    state: ConsoleState,
    screenId: string,
    fn: (view: ScreenView) => ScreenView,
): ConsoleState {
    return {
        ...state,
        screens: { ...state.screens, [screenId]: fn(state.screens[screenId] ?? emptyScreen) },
    };
}

function brandsOf(creative: CreativeReady | CreativeUpgraded | ConsoleNext): string {
    return creative.spec.slots.map((slot) => slot.brand_name).join(" + ");
}

/** The timeline line for a message, or null when it adds nothing to read. */
export function describe(m: ConsoleOutbound): Omit<StationEvent, "at"> | null {
    switch (m.type) {
        case "console.moment":
            return {
                text: `Momento: ${m.people} ${m.people === 1 ? "persona" : "personas"}, ${m.looking} mirando`,
                tone: "neutral",
            };
        case "console.auction":
            return m.winners.length === 0
                ? { text: "Subasta: sin ganador", tone: "neutral" }
                : {
                      text: `Subasta: gana ${m.winners.map((w) => w.brand_name).join(" + ")}${m.swap === "held" ? " (espera el mínimo en pantalla)" : ""}`,
                      tone: "info",
                  };
        case "creative.ready":
            return m.auction_id === null
                ? { text: "Al aire: aviso de la casa", tone: "neutral" }
                : { text: `Al aire: ${brandsOf(m)}`, tone: "success" };
        case "creative.upgraded":
            return { text: `Mejora en vivo (${m.upgrade_kind}): ${brandsOf(m)}`, tone: "info" };
        case "console.receipt":
            return m.total.amount === "0"
                ? { text: "Recibo: $0, nadie miró", tone: "neutral" }
                : { text: `Recibo: $${m.total.amount} cobrado`, tone: "success" };
        case "console.redemption":
            return { text: `Canje ${m.code}: ${m.brands.join(" + ")}`, tone: "success" };
        case "console.next":
            return {
                text: `Siguiente: ${m.auction_id === null ? "aviso de la casa" : brandsOf(m)} (espera el mínimo en pantalla)`,
                tone: "info",
            };
        case "console.degradation":
            return m.flags.length
                ? { text: `Degradado: ${m.flags.join(", ")}`, tone: "warning" }
                : null;
        default:
            return null;
    }
}

function withEvent(view: ScreenView, m: ConsoleOutbound, at: number): ScreenView {
    const line = describe(m);
    return line
        ? { ...view, events: [{ ...line, at }, ...view.events].slice(0, EVENT_WINDOW) }
        : view;
}

export function consoleReducer(state: ConsoleState, event: ConsoleEvent): ConsoleState {
    if (event.type === "connection") return { ...state, connection: event.state };
    if (event.type === "perception") {
        const perception = { message: event.message, at: event.at };
        return update(state, event.message.screen_id, (v) => ({ ...v, perception }));
    }
    const at = event.at ?? 0;
    const m = event.message;
    if (describe(m) !== null && "screen_id" in m) {
        state = update(state, m.screen_id, (v) => withEvent(v, m, at));
    }
    switch (m.type) {
        case "console.moment":
            return update(state, m.screen_id, (v) => ({ ...v, moment: m }));
        case "console.auction":
            return update(state, m.screen_id, (v) => ({ ...v, auction: m }));
        case "console.latency":
            return update(state, m.screen_id, (v) =>
                withTrace({ ...v, lastCreativeId: m.creative_id }, m.trace),
            );
        case "console.degradation":
            return update(state, m.screen_id, (v) => ({ ...v, degradation: m.flags }));
        case "creative.ready":
            return update(state, m.screen_id, (v) => ({
                ...v,
                lastCreativeId: m.creative_id,
                onAir: m,
                // The preview aired (or a newer decision replaced it): nothing waits now.
                next: null,
            }));
        case "console.next":
            return update(state, m.screen_id, (v) => ({ ...v, next: m }));
        case "creative.upgraded":
            // An upgrade only replaces the ad it belongs to (never revives a retired one).
            return update(state, m.screen_id, (v) => ({
                ...v,
                lastCreativeId: m.creative_id,
                onAir: v.onAir?.creative_id === m.creative_id ? m : v.onAir,
            }));
        case "console.book":
            return update(state, m.screen_id, (v) => ({ ...v, book: m }));
        case "console.receipt":
            return update(state, m.screen_id, (v) => ({
                ...v,
                receipts: [m, ...v.receipts].slice(0, RECEIPT_WINDOW),
            }));
        case "console.redemption":
            return update(state, m.screen_id, (v) => ({
                ...v,
                redemptions: [m, ...v.redemptions].slice(0, REDEMPTION_WINDOW),
            }));
        case "system.error":
            return { ...state, lastError: `${m.code}: ${m.message}` };
        case "live.status":
            return state; // the live switch is shown by LiveInputStatus, which polls /health
    }
}
