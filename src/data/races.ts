import { Race, StateCode } from "@/types";

/** Display order and labels for each state's races. Ids match agents/config.py. */
export const races: { id: Race; state: StateCode; title: string }[] = [
    { id: "senate", state: "MA", title: "U.S. Senate" },
    { id: "governor", state: "MA", title: "Governor" },
    { id: "ca_governor", state: "CA", title: "Governor" },
];
