import { describe, expect, it } from "vitest";
import { STATIONS, stationName } from "./stations";

describe("stations", () => {
    it("names the real screen MUT_01 and keeps exactly one live", () => {
        expect(stationName("demo-01")).toBe("MUT_01");
        expect(STATIONS.filter((station) => station.live)).toHaveLength(1);
    });

    it("shows an unknown screen id as itself", () => {
        expect(stationName("mall-07")).toBe("mall-07");
    });
});
