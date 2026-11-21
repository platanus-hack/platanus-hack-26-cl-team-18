import { describe, expect, it } from "vitest";
import { demoHeaders, demoHref, demoKey, demoRoute } from "./demo";

describe("demo surface routing", () => {
    it("recognises the hub and its pages", () => {
        expect(demoRoute("/demo/abc_123")).toEqual({ key: "abc_123", page: null });
        expect(demoRoute("/demo/abc_123/")).toEqual({ key: "abc_123", page: null });
        expect(demoRoute("/demo/abc_123/clerk")).toEqual({ key: "abc_123", page: "clerk" });
    });

    it("never treats the real station or unknown pages as demo", () => {
        expect(demoRoute("/console")).toBeNull();
        expect(demoRoute("/advertiser")).toBeNull();
        expect(demoRoute("/demo")).toBeNull();
        expect(demoRoute("/demo/abc/admin")).toBeNull();
        expect(demoRoute("/demo/a b/clerk")).toBeNull();
        expect(demoKey("/screen")).toBeNull();
    });

    it("sends the key only from demo pages", () => {
        expect(demoHeaders("/demo/k3y/advertiser")).toEqual({ "X-Demo-Key": "k3y" });
        expect(demoHeaders("/console")).toEqual({});
    });

    it("builds links that keep the screen query", () => {
        expect(demoHref("k3y", "clerk", "?id=demo-01")).toBe("/demo/k3y/clerk?id=demo-01");
        expect(demoHref("k3y", null)).toBe("/demo/k3y");
    });
});
