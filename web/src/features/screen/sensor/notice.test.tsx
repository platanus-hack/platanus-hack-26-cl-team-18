import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DeviceStates } from "./link";
import { ASK_NOTICE_DELAY_MS, noticeText, SensorNotice } from "./notice";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
    vi.useFakeTimers();
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
});
afterEach(() => {
    act(() => {
        root.unmount();
    });
    host.remove();
    vi.useRealTimers();
});

function show(devices: DeviceStates) {
    act(() => {
        root.render(<SensorNotice devices={devices} />);
    });
}
const text = () => host.textContent;

describe("SensorNotice", () => {
    it("shows nothing while the devices work or are off on purpose", () => {
        show({ camera: "on", mic: "off" });
        expect(host.innerHTML).toBe("");
    });

    it("a blocked camera says how to unblock it, at once", () => {
        show({ camera: "blocked", mic: "off" });
        expect(text()).toContain("Cámara bloqueada");
    });

    it("a prompt answered quickly never flashes; one left waiting says what to click", () => {
        show({ camera: "asking", mic: "off" });
        expect(host.innerHTML).toBe("");
        act(() => {
            vi.advanceTimersByTime(ASK_NOTICE_DELAY_MS);
        });
        expect(text()).toContain("Recordar esta decisión");
        show({ camera: "on", mic: "off" });
        expect(host.innerHTML).toBe("");
        show({ camera: "asking", mic: "off" }); // a fresh prompt waits again
        expect(host.innerHTML).toBe("");
    });

    it("a mic problem is shown beside a working camera", () => {
        show({ camera: "on", mic: "blocked" });
        expect(text()).toContain("Micrófono bloqueado");
    });

    it.each(["off", "on", "starting", "failed"] as const)(
        "%s is not the operator's problem",
        (state) => {
            expect(noticeText("camera", state)).toBeNull();
        },
    );
});
