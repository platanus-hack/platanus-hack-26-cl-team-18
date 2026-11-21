import { describe, expect, it } from "vitest";
import { readableAccent } from "./accent";

describe("readableAccent", () => {
    it.each(["#c6f26b", "#f5bb84", "#b9adff", "#f6d15a"])("keeps the demo brand %s", (hex) => {
        expect(readableAccent(hex)).toBe(hex);
    });

    it.each(["#1a237e", "#8b0000", "#13131e"])("drops %s, which would hide the price", (hex) => {
        expect(readableAccent(hex)).toBeNull();
    });

    it.each([null, undefined, "", "red", "#fff"])("ignores %j", (value) => {
        expect(readableAccent(value)).toBeNull();
    });
});
