/**
 * U.S. House districts: who represents each one now, and who is on the
 * November 3, 2026 ballot. Keyed by "MA-7" style ids.
 *
 * - representative: the current member of Congress (119th Congress), shown
 *   in the district picker so people can recognize their district.
 * - candidates: fill ONLY from the Secretary of the Commonwealth's official
 *   2026 State Election candidate list. Leave empty until checked; the app
 *   then shows the district without claiming who is running.
 */
export interface HouseDistrict {
    id: string;
    representative: string;
    candidates: { name: string; party: string; incumbent?: boolean }[];
}

export const HOUSE_DISTRICTS: Record<string, HouseDistrict> = {
    "MA-1": { id: "MA-1", representative: "Richard Neal", candidates: [] },
    "MA-2": { id: "MA-2", representative: "Jim McGovern", candidates: [] },
    "MA-3": { id: "MA-3", representative: "Lori Trahan", candidates: [] },
    "MA-4": { id: "MA-4", representative: "Jake Auchincloss", candidates: [] },
    "MA-5": { id: "MA-5", representative: "Katherine Clark", candidates: [] },
    "MA-6": { id: "MA-6", representative: "Seth Moulton", candidates: [] },
    "MA-7": { id: "MA-7", representative: "Ayanna Pressley", candidates: [] },
    "MA-8": { id: "MA-8", representative: "Stephen Lynch", candidates: [] },
    "MA-9": { id: "MA-9", representative: "Bill Keating", candidates: [] },
};

/** Official voter lookups, for anyone unsure which district they're in. */
export const DISTRICT_LOOKUP_URL: Record<string, string> = {
    MA: "https://www.sec.state.ma.us/VoterRegistrationSearch/MyVoterRegStatus.aspx",
};
