import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "./App";

vi.mock("@/lib/ws/typed-socket", () => ({
    TypedSocket: class {
        start() {}
        stop() {}
        send() {}
    },
}));

let root: Root;
let host: HTMLDivElement;

beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    vi.stubGlobal(
        "fetch",
        vi.fn(() => Promise.reject(new Error("no backend in this test"))),
    );
    window.localStorage.clear();
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
});

afterEach(async () => {
    await act(async () => {
        root.unmount();
        await Promise.resolve();
    });
    host.remove();
    window.history.pushState({}, "", "/");
    window.localStorage.clear();
    vi.unstubAllGlobals();
});

async function open(path: string) {
    window.history.pushState({}, "", path);
    await act(async () => {
        root.render(<App />);
        await Promise.resolve();
    });
    // Pages are lazy chunks: wait until one has rendered.
    for (let i = 0; i < 100 && host.querySelector("nav") === null; i++) {
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 20));
        });
    }
}

const links = () => [...host.querySelectorAll("nav a")].map((a) => a.getAttribute("href"));

it("the home page is the admin console with the live switch", async () => {
    await open("/");
    expect(host.querySelector('[aria-label="En vivo · demo-01"]')).not.toBeNull();
    expect(links()).toEqual(["/", "/screen?id=demo-01"]);
});

it.each(["/advertiser", "/clerk", "/campaigns", "/dev", "/demo", "/demo/k3y/admin"])(
    "%s is not a page: it shows the console",
    async (path) => {
        await open(path);
        expect(host.querySelector('[aria-label="En vivo · demo-01"]')).not.toBeNull();
        expect(links().some((href) => href?.includes("/demo/"))).toBe(false);
    },
);

it("the hidden hub lists the demo tools and this browser keeps them in the nav", async () => {
    await open("/demo/k3y");
    expect(host.textContent).toContain("Herramientas para mostrar el producto");
    expect(links()).toEqual(
        expect.arrayContaining([
            "/demo/k3y/advertiser",
            "/demo/k3y/clerk?id=demo-01",
            "/demo/k3y/campaigns",
            "/demo/k3y/dev?screen=demo-01",
        ]),
    );
    await act(async () => {
        root.unmount();
        await Promise.resolve();
    });
    root = createRoot(host);
    await open("/");
    expect(links()).toEqual(expect.arrayContaining(["/demo/k3y", "/demo/k3y/advertiser"]));
});
