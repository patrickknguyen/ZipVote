import { StateCode } from "@/types";
import { MA_ZIP_DISTRICTS } from "@/data/districts_ma";

/**
 * Which covered state a zip code belongs to, or null if not covered yet.
 * Statewide races are the same for every zip in a state, so the state is
 * all that's needed. (District races will also need a district picker.)
 *   Massachusetts: 010–027, plus 055 (a few PO boxes in Andover)
 *   California:    900–961
 */
export function zipToState(zip: string): StateCode | null {
    if (!/^\d{5}$/.test(zip)) return null;
    const prefix = Number(zip.slice(0, 3));
    if ((prefix >= 10 && prefix <= 27) || prefix === 55) return "MA";
    if (prefix >= 900 && prefix <= 961) return "CA";
    return null;
}

/**
 * U.S. House district ids ("MA-7") a zip overlaps. One → known; several →
 * ask the user; none → unknown (PO-box zips, or states without district
 * data yet: California's map changed for 2026 under Proposition 50).
 */
export function zipToDistricts(zip: string): string[] {
    const state = zipToState(zip);
    if (state === "MA") return (MA_ZIP_DISTRICTS[zip] ?? []).map((n) => `MA-${n}`);
    return [];
}
