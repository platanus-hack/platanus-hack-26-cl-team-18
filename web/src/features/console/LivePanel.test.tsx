import { LiveSetSchema, type LiveSet, type LiveStatus } from "@/contract";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LivePanel } from "./LivePanel";

/** A fake backend switchboard: the panel must always show what it answers. */
let backend: LiveStatus;
let failPost = false;
const posted: LiveSet[] = [];

function derive(live: boolean, mic: boolean, revision: number): LiveStatus {
    return {
        type: "live.status",
        v: 1,
        screen_id: "demo-01",
        revision,
        live,
        mic,
        speech_opt_in: true,
        sensing: live,
        listening: live && mic,
        ai_generation: live,
        agents: ["speech", "vision"],
        changed_at: "2026-11-21T15:00:00Z",
    };
}

let root: Root;
let host: HTMLDivElement;
const fetchMock = vi.fn<typeof fetch>();

beforeEach(async () => {
    vi.useFakeTimers();
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.stubGlobal("fetch", fetchMock);
    backend = derive(false, true, 0);
    failPost = false;
    posted.length = 0;
    fetchMock.mockImplementation((_input, options) => {
        if (options?.method === "POST") {
            if (failPost) return Promise.resolve(new Response("nope", { status: 500 }));
            const change = LiveSetSchema.parse(JSON.parse(options.body as string) as unknown);
            posted.push(change);
            backend = derive(
                change.live ?? backend.live,
                change.mic ?? backend.mic,
                backend.revision + 1,
            );
        }
        return Promise.resolve(new Response(JSON.stringify(backend)));
    });
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await act(async () => {
        root.render(<LivePanel screen="demo-01" />);
        await Promise.resolve();
    });
    await settle();
});

afterEach(async () => {
    await act(async () => {
        root.unmount();
        await Promise.resolve();
    });
    host.remove();
    fetchMock.mockReset();
    vi.useRealTimers();
    vi.unstubAllGlobals();
});

async function settle() {
    await act(async () => {
        await vi.advanceTimersByTimeAsync(20);
    });
}

function button(text: string) {
    const found = [...host.querySelectorAll("button")].find((b) => b.textContent.includes(text));
    if (!found) throw new Error(`no button "${text}" in: ${host.textContent}`);
    return found;
}

async function click(text: string) {
    await act(async () => {
        button(text).click();
        await Promise.resolve();
    });
    await settle();
}

it("starts in standby and goes live with one click", async () => {
    expect(host.textContent).toContain("En espera");
    expect(host.textContent).toContain("Cámara en espera");
    await click("Iniciar en vivo");
    expect(posted).toEqual([
        { type: "live.set", v: 1, screen_id: "demo-01", live: true, mic: null },
    ]);
    expect(host.textContent).toContain("Cámara analizando");
    expect(host.textContent).toContain("Micrófono escuchando");
    expect(host.querySelector("[data-live]")?.getAttribute("data-live")).toBe("on");
});

it("mutes only the mic and stops everything with Detener", async () => {
    await click("Iniciar en vivo");
    await click("Silenciar micrófono");
    expect(posted.at(-1)).toMatchObject({ live: null, mic: false });
    expect(host.textContent).toContain("Micrófono silenciado");
    expect(host.textContent).toContain("Cámara analizando");
    await click("Detener");
    expect(posted.at(-1)).toMatchObject({ live: false, mic: null });
    expect(host.textContent).toContain("En espera");
});

it("a failed click keeps showing the backend's truth and says so", async () => {
    failPost = true;
    await click("Iniciar en vivo");
    expect(host.textContent).toContain("No se aplicó el cambio");
    expect(host.textContent).toContain("En espera");
});

it("reflects a change made from the other laptop on the next poll", async () => {
    backend = derive(true, true, 4);
    await act(async () => {
        await vi.advanceTimersByTimeAsync(1100);
    });
    expect(host.textContent).toContain("En vivo");
});
