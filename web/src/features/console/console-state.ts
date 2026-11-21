import type {
    ConsoleAuction,
    ConsoleBook,
    ConsoleMoment,
    ConsoleOutbound,
    ConsoleReceipt,
    ConsoleRedemption,
    DegradationFlag,
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
}

export const RECEIPT_WINDOW = 20;
export const REDEMPTION_WINDOW = 10;

export interface ConsoleState {
    readonly connection: ConnectionState;
    readonly screens: Readonly<Record<string, ScreenView>>;
    readonly lastError: string | null;
}

export type ConsoleEvent =
    | { readonly type: "message"; readonly message: ConsoleOutbound }
    | { readonly type: "connection"; readonly state: ConnectionState };

const emptyScreen: ScreenView = {
    moment: null,
    auction: null,
    lastTrace: null,
    glassToGlassMs: [],
    degradation: [],
    lastCreativeId: null,
    book: null,
    receipts: [],
    redemptions: [],
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

export function consoleReducer(state: ConsoleState, event: ConsoleEvent): ConsoleState {
    if (event.type === "connection") return { ...state, connection: event.state };
    const m = event.message;
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
        case "creative.upgraded":
            return update(state, m.screen_id, (v) => ({ ...v, lastCreativeId: m.creative_id }));
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
