import { Race } from "@/types";

/** Display order and labels for the races on the ballot. Ids match agents/config.py. */
export const races: { id: Race; title: string }[] = [
    { id: "senate", title: "U.S. Senate" },
    { id: "house_ma7", title: "U.S. House · MA-7" },
    { id: "governor", title: "Governor" },
];
