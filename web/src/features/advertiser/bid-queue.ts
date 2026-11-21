/** One in-flight write, one latest draft. Older responses never erase newer input. */
export type BidState =
    { kind: "idle"; value: number | null } | { kind: "sending" | "failed"; value: number };

export class BidQueue {
    private pending: number | null = null;
    private running = false;
    private active = true;

    constructor(
        private readonly send: (value: number) => Promise<unknown>,
        private readonly changed: (state: BidState) => void,
        private readonly settled: () => void,
    ) {}

    set(value: number): void {
        if (!this.active) return;
        this.pending = value;
        this.changed({ kind: "sending", value });
        if (!this.running) void this.drain();
    }

    dispose(): void {
        this.active = false;
        this.pending = null;
    }

    private status(): { active: boolean; pending: number | null } {
        return { active: this.active, pending: this.pending };
    }

    private async drain(): Promise<void> {
        this.running = true;
        while (this.active && this.pending !== null) {
            const value = this.pending;
            this.pending = null;
            try {
                await this.send(value);
                const status = this.status();
                if (!status.active) break;
                if (status.pending === null) {
                    this.settled();
                    this.changed({ kind: "idle", value });
                }
            } catch {
                const status = this.status();
                if (status.active && status.pending === null) {
                    this.changed({ kind: "failed", value });
                }
            }
        }
        this.running = false;
    }
}
