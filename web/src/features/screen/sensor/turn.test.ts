/**
 * The noisy-venue defences of the /screen speech sensor, as measured on the edge
 * agent (docs/evals.md "What the live tier found"): babble inventions, a crowd
 * that never goes silent, a VAD split mid-negation.
 */
import { beforeEach, describe, expect, it } from "vitest";
import {
    HOLD_MS,
    MIN_CONFIDENCE,
    SETTLE_MS,
    SpeechTurn,
    TWIN_WAIT_MS,
    meanLogprob,
    normalize,
    type TurnUtterance,
} from "./turn";

let sent: TurnUtterance[];
let turn: SpeechTurn;

const words = (logprob: number) => [
    { text: "un", type: "word", logprob },
    { text: " ", type: "spacing", logprob: -9 },
    { text: "café", type: "word", logprob },
];

function finals(): string[] {
    return sent.filter((u) => u.isFinal).map((u) => u.text);
}

beforeEach(() => {
    sent = [];
    turn = new SpeechTurn("s", (u) => sent.push(u));
});

describe("low-confidence finals (babble inventions)", () => {
    it("a final Scribe was unsure of retracts its partials instead of asking for coffee", () => {
        turn.partial("Un café", 0);
        turn.committed("Un café", 100);
        turn.committedWithWords("Un café", words(-2.1), 120);
        expect(sent.at(-1)).toEqual({ text: "", isFinal: true, utteranceId: "s:0" });
    });

    it("real speech passes with its confidence", () => {
        turn.partial("Un café", 0);
        turn.committed("Un café", 100);
        turn.committedWithWords("Un café", words(-0.6), 120);
        expect(finals()).toEqual(["Un café"]);
    });

    it("a low-confidence final with no partials before it sends nothing at all", () => {
        turn.committed("Creo", 100);
        turn.committedWithWords("Creo", words(MIN_CONFIDENCE - 0.5), 120);
        expect(sent).toEqual([]);
    });

    it("no twin: the plain commit counts after the wait, and a late twin is not a second final", () => {
        turn.committed("Un café", 0);
        expect(turn.tick(TWIN_WAIT_MS - 1)).toBe(false);
        expect(finals()).toEqual([]);
        turn.tick(TWIN_WAIT_MS);
        expect(finals()).toEqual(["Un café"]);
        turn.committedWithWords("Un café", words(-0.5), TWIN_WAIT_MS + 50);
        expect(finals()).toEqual(["Un café"]);
    });

    it("the next utterance gets the next id", () => {
        turn.committed("Un café", 0);
        turn.committedWithWords("Un café", words(-0.5), 20);
        turn.partial("y un red bull", 500);
        expect(sent.map((u) => u.utteranceId)).toEqual(["s:0", "s:1"]);
    });

    it("spacing tokens and junk don't count toward the mean", () => {
        expect(meanLogprob(words(-0.5))).toBeCloseTo(-0.5);
        expect(meanLogprob([{ logprob: "x" }, null, 3])).toBeNull();
        expect(meanLogprob(undefined)).toBeNull();
    });
});

describe("settled-turn commit (a crowd that never goes silent)", () => {
    it("asks for a commit once a two-word partial stops growing", () => {
        turn.partial("una chela", 0);
        expect(turn.tick(SETTLE_MS - 1)).toBe(false);
        expect(turn.tick(SETTLE_MS)).toBe(true);
        expect(turn.tick(SETTLE_MS + 500)).toBe(false); // once, until the commit comes back
    });

    it("a one-word invention never settles into a final", () => {
        turn.partial("Sopaipilla", 0);
        expect(turn.tick(SETTLE_MS * 3)).toBe(false);
    });

    it("never cuts a turn ending on a dangling word", () => {
        turn.partial("quiero un café sin", 0);
        expect(turn.tick(SETTLE_MS * 2)).toBe(false);
    });

    it("growing partials keep the turn open", () => {
        turn.partial("quiero un", 0);
        turn.partial("quiero un café", SETTLE_MS - 100);
        expect(turn.tick(SETTLE_MS + 100)).toBe(false);
        expect(turn.tick(2 * SETTLE_MS - 100)).toBe(true);
    });
});

describe("dangling commits wait for the rest of the request", () => {
    it('"No." + "Quiero cerveza." stays one negated request', () => {
        turn.partial("No", 0);
        turn.committed("No.", 100);
        turn.committedWithWords("No.", words(-0.3), 120);
        expect(finals()).toEqual([]);
        turn.partial("Quiero cerveza", 400);
        expect(sent.at(-1)).toMatchObject({ text: "No Quiero cerveza", isFinal: false });
        turn.committed("Quiero cerveza.", 700);
        turn.committedWithWords("Quiero cerveza.", words(-0.4), 720);
        expect(finals()).toEqual(["No Quiero cerveza."]);
        expect(new Set(sent.map((u) => u.utteranceId))).toEqual(new Set(["s:0"]));
    });

    it("a held commit nobody finishes is sent on its own after a pause", () => {
        turn.committed("Un café sin", 0);
        turn.committedWithWords("Un café sin", words(-0.3), 20);
        turn.tick(20 + HOLD_MS - 1);
        expect(finals()).toEqual([]);
        turn.tick(20 + HOLD_MS);
        expect(finals()).toEqual(["Un café sin"]);
    });
});

describe("session end", () => {
    it("retracts a half-heard request and settles a commit in flight", () => {
        turn.partial("un café", 0);
        turn.reset(10);
        expect(sent.at(-1)).toEqual({ text: "", isFinal: true, utteranceId: "s:0" });
        turn.partial("un red bull", 20);
        turn.committed("Un Red Bull", 30);
        turn.reset(40);
        expect(finals()).toEqual(["", "Un Red Bull"]);
    });

    it("a quiet session ends without sending anything", () => {
        turn.reset(0);
        expect(sent).toEqual([]);
    });
});

it("normalize folds like the backend (accents gone, ñ kept)", () => {
    expect(normalize("¿Un CAFÉ, señor?")).toBe("un cafe señor");
});
