import { describe, expect, it, vi } from "vitest";
import { BidQueue, type BidState } from "./bid-queue";

const tick = async () => {
    await Promise.resolve();
    await Promise.resolve();
};

describe("bidding-war writes", () => {
    it("coalesces rapid drags and never clears a newer draft on an older response", async () => {
        const resolve: (() => void)[] = [];
        const send = vi.fn(() => new Promise<void>((done) => resolve.push(done)));
        const states: BidState[] = [];
        const settled = vi.fn();
        const queue = new BidQueue(send, (s) => states.push(s), settled);
        queue.set(1.1);
        queue.set(2.2);
        queue.set(4.8);
        expect(send.mock.calls).toHaveLength(1);
        resolve[0]!();
        await tick();
        expect(send.mock.calls).toEqual([[1.1], [4.8]]);
        expect(states.at(-1)).toEqual({ kind: "sending", value: 4.8 });
        expect(settled).not.toHaveBeenCalled();
        resolve[1]!();
        await tick();
        expect(states.at(-1)).toEqual({ kind: "idle", value: 4.8 });
        expect(settled).toHaveBeenCalledOnce();
    });
    it("retains a failed draft for retry and recovers", async () => {
        const states: BidState[] = [];
        const send = vi.fn().mockRejectedValueOnce(new Error()).mockResolvedValueOnce({});
        const queue = new BidQueue(send, (s) => states.push(s), vi.fn());
        queue.set(3);
        await tick();
        expect(states.at(-1)).toEqual({ kind: "failed", value: 3 });
        queue.set(3);
        await tick();
        expect(states.at(-1)).toEqual({ kind: "idle", value: 3 });
    });
    it("does not send queued writes or update an unmounted page", async () => {
        let finish = () => {};
        const send = vi.fn(
            () =>
                new Promise<void>((done) => {
                    finish = done;
                }),
        );
        const changed = vi.fn();
        const settled = vi.fn();
        const queue = new BidQueue(send, changed, settled);
        queue.set(2);
        queue.set(5);
        queue.dispose();
        changed.mockClear();
        finish();
        await tick();
        expect(send).toHaveBeenCalledOnce();
        expect(changed).not.toHaveBeenCalled();
        expect(settled).not.toHaveBeenCalled();
    });
});
