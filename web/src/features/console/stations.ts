/**
 * The station picker's network. Only MUT_01 is wired to a real screen
 * (`demo-01`); the others are listed offline so the console reads as a fleet.
 */
export const STATIONS: readonly { id: string; name: string; live: boolean }[] = [
    { id: "demo-01", name: "MUT_01", live: true },
    { id: "mut-02", name: "MUT_02", live: false },
    { id: "mut-03", name: "MUT_03", live: false },
    { id: "mut-04", name: "MUT_04", live: false },
    { id: "mut-05", name: "MUT_05", live: false },
];

/** The display name for a screen id; an unknown id shows as itself. */
export function stationName(id: string): string {
    return STATIONS.find((station) => station.id === id)?.name ?? id;
}
