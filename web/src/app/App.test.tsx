import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "./App";

let root: Root;
let host: HTMLDivElement;

beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
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
    vi.unstubAllGlobals();
});

async function open(path: string) {
    window.history.pushState({}, "", path);
    await act(async () => {
        root.render(<App />);
        await Promise.resolve();
    });
}

const links = () => [...host.querySelectorAll("a")].map((a) => a.getAttribute("href"));

it("the home page shows only the real station", async () => {
    await open("/");
    expect(links()).toEqual(expect.arrayContaining(["/console", "/screen?id=demo-01"]));
    expect(links().some((href) => href?.includes("demo/") || href === "/advertiser")).toBe(false);
});

it.each(["/advertiser", "/clerk", "/campaigns", "/dev", "/demo", "/demo/k3y/admin"])(
    "%s is not a page anymore: it falls back to the home page",
    async (path) => {
        await open(path);
        expect(host.textContent).toContain("Avisos en canasta");
    },
);

it("the hidden hub lists the demo tools under its key", async () => {
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
});
