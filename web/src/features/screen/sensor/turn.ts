/**
 * Turns Scribe's raw transcript stream into the utterances the backend acts on,
 * with the defences the edge agent measured in a noisy venue (docs/evals.md,
 * `edge/agent/speech/pipeline.py`). Pure: no sockets, no timers; the caller
 * feeds Scribe messages and calls `tick()` on every mic chunk (~128 ms).
 *
 * - **Low-confidence finals.** With `include_timestamps`, Scribe follows each
 *   commit ~20 ms later with a twin carrying per-word logprobs. Babble
 *   inventions ("Un café", "Creo") measured -1.48..-2.92 per word; real speech
 *   stays >= -0.95. A final below MIN_CONFIDENCE becomes an empty final, which
 *   retracts what its partials asked for. No twin within TWIN_WAIT_MS: the
 *   plain commit counts as before.
 * - **Settled-turn commit.** In a crowd the server VAD never hears silence, so
 *   a request would only finalize at Scribe's 90 s cap. Babble doesn't add
 *   words: once the longest partial stops growing for SETTLE_MS, has >= 2 words
 *   and doesn't end on a dangling word, `tick()` asks the caller to commit.
 * - **Dangling commits wait.** A VAD split after "no", "sin" or "quiero" would
 *   finalize half a request, and "No." + "Quiero cerveza." reads as *wanting*
 *   beer. A commit ending on a dangling word is held (same utterance id) and
 *   joined, without the boundary punctuation, to the next one.
 *
 * Partials are forwarded raw (`partials_stabilized: false`): the backend's
 * PartialGate needs two agreeing reads before acting and keeps alcohol
 * final-only. Text lives here only until it is forwarded.
 */

export const MIN_CONFIDENCE = -1.3;
export const TWIN_WAIT_MS = 300;
export const SETTLE_MS = 1_200;
export const HOLD_MS = 1_500;
/** A forced commit that never comes back must not freeze settling forever. */
const SETTLE_TIMEOUT_MS = 3_000;

/** Mirrors `_DANGLING` in edge/agent/speech/pipeline.py. */
const DANGLING = new Set([
    "y",
    "o",
    "pero",
    "sin",
    "no",
    "ni",
    "quiero",
    "necesito",
    "un",
    "una",
    "con",
    "and",
    "without",
    "want",
]);

export interface TurnUtterance {
    text: string;
    isFinal: boolean;
    utteranceId: string;
}

/** Same folding as `normalize` in app/contract/speech_matching.py (keeps ñ). */
export function normalize(text: string): string {
    const lowered = text.normalize("NFC").toLowerCase().replaceAll("ñ", "\0");
    const folded = lowered.normalize("NFKD").replace(/\p{M}/gu, "").replaceAll("\0", "ñ");
    return folded.replace(/[^a-z0-9ñ]+/g, " ").trim();
}

/** Mean per-word logprob of a timestamped commit; null when Scribe gave none. */
export function meanLogprob(words: unknown): number | null {
    if (!Array.isArray(words)) return null;
    const values: number[] = [];
    for (const word of words as unknown[]) {
        if (typeof word !== "object" || word === null) continue;
        const { type, logprob } = word as { type?: unknown; logprob?: unknown };
        if ((type ?? "word") !== "word") continue;
        if (typeof logprob === "number" && Number.isFinite(logprob)) values.push(logprob);
    }
    return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

function endsDangling(text: string): boolean {
    const words = normalize(text).split(" ");
    return DANGLING.has(words[words.length - 1] ?? "");
}

/** "No." + "¿Quiero cerveza?" → "No ¿Quiero cerveza?": one clause, negation kept. */
function join(held: string, text: string): string {
    if (held === "") return text;
    if (text === "") return held;
    return `${held.replace(/[\s.,;:!?…]+$/u, "")} ${text.replace(/^[\s¡]+/u, "")}`;
}

export class SpeechTurn {
    private utterance = 0;
    private partialsSent = false;
    private held = "";
    private heldAt = 0;
    private pending: { text: string; at: number } | null = null;
    private finalized: { text: string; at: number } | null = null;
    private longest = "";
    private grewAt = 0;
    private dangling = false;
    private settlingSince: number | null = null;

    constructor(
        private readonly session: string,
        private readonly send: (utterance: TurnUtterance) => void,
    ) {}

    partial(text: string, now: number): void {
        if (this.settlingSince === null) {
            const norm = normalize(text);
            if (norm.length > this.longest.length) {
                this.longest = norm;
                this.grewAt = now;
            }
        }
        this.dangling = endsDangling(text);
        this.partialsSent = true;
        this.emit(join(this.held, text), false);
    }

    /** `committed_transcript`: wait briefly for its timestamped twin. */
    committed(text: string, now: number): void {
        if (this.isEcho(text, now)) return;
        if (this.pending !== null) this.finalize(this.pending.text, null, now);
        this.pending = { text, at: now };
    }

    /** `committed_transcript_with_timestamps`: the confidence-bearing twin. */
    committedWithWords(text: string, words: unknown, now: number): void {
        if (this.pending !== null && this.pending.text !== text) {
            this.finalize(this.pending.text, null, now);
        }
        this.pending = null;
        if (this.isEcho(text, now)) return;
        this.finalize(text, meanLogprob(words), now);
    }

    /** Call on every mic chunk. True: the caller must send a Scribe commit now. */
    tick(now: number): boolean {
        if (this.pending !== null && now - this.pending.at >= TWIN_WAIT_MS) {
            this.finalize(this.pending.text, null, now);
        }
        if (this.held !== "" && now - this.heldAt >= HOLD_MS) {
            this.finish(this.held);
        }
        if (this.settlingSince !== null) {
            if (now - this.settlingSince >= SETTLE_TIMEOUT_MS) this.settlingSince = null;
            return false;
        }
        if (
            this.longest !== "" &&
            now - this.grewAt >= SETTLE_MS &&
            !this.dangling &&
            this.longest.split(" ").length >= 2
        ) {
            this.settlingSince = now;
            this.forgetGrowth();
            return true;
        }
        return false;
    }

    /** The Scribe session ended: settle what is in flight, retract the rest. */
    reset(now: number): void {
        if (this.pending !== null) this.finalize(this.pending.text, null, now);
        this.pending = null;
        if (this.held !== "") this.finish(this.held);
        else if (this.partialsSent) this.finish("");
        this.forgetGrowth();
        this.settlingSince = null;
        this.finalized = null;
    }

    /** A twin that arrives after TWIN_WAIT_MS repeats a final already sent. */
    private isEcho(text: string, now: number): boolean {
        const last = this.finalized;
        return last !== null && last.text === text && now - last.at < 1_000;
    }

    private finalize(text: string, confidence: number | null, now: number): void {
        this.pending = null;
        this.finalized = { text, at: now };
        this.settlingSince = null;
        this.forgetGrowth();
        const heard = confidence !== null && confidence < MIN_CONFIDENCE ? "" : text.trim();
        const combined = join(this.held, heard);
        if (heard !== "" && endsDangling(combined)) {
            this.held = combined;
            this.heldAt = now;
            return;
        }
        if (combined === "" && !this.partialsSent) {
            this.utterance++; // nothing asked, nothing to retract
            return;
        }
        this.finish(combined);
    }

    private finish(text: string): void {
        this.emit(text, true);
        this.held = "";
        this.partialsSent = false;
        this.utterance++;
    }

    private forgetGrowth(): void {
        this.longest = "";
        this.grewAt = 0;
        this.dangling = false;
    }

    private emit(text: string, isFinal: boolean): void {
        this.send({
            text: text.slice(0, 1000),
            isFinal,
            utteranceId: `${this.session}:${this.utterance}`,
        });
    }
}
