import { describe, expect, it } from "vitest";
import { cn } from "./cn";

describe("cn", () => {
    it("keeps a type tier next to a text color", () => {
        expect(cn("text-title", "text-accent")).toBe("text-title text-accent");
        expect(cn("text-compact text-on-primary")).toBe("text-compact text-on-primary");
    });

    it("still lets a later type tier win", () => {
        expect(cn("text-body", "text-kpi")).toBe("text-kpi");
    });
});
