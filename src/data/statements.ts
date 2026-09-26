/**
 * Loads each state's generated ballot (data/processed/statements_ballot_<STATE>.json).
 * There is deliberately no mock fallback: a state that hasn't been generated
 * has no statements, and the app shows an empty state rather than unsourced
 * placeholder scores about real candidates.
 *
 * Generate with:
 *   python -m agents.scout
 *   python -m agents.wedge            # all states
 *   python -m agents.wedge --state CA # one state
 */

import { Statement, CandidateBio, StateCode } from "@/types";

const ballots: Record<StateCode, Statement[]> = { MA: [], CA: [] };
const allBios: Record<string, CandidateBio> = {};

// Static requires so the bundler includes each file at build time.
// A missing file means that state hasn't been generated yet.
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const b = require("../../data/processed/statements_ballot_MA.json");
    ballots.MA = b.statements;
    Object.assign(allBios, b.bios ?? {});
} catch {}
try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const b = require("../../data/processed/statements_ballot_CA.json");
    ballots.CA = b.statements;
    Object.assign(allBios, b.bios ?? {});
} catch {}

/** The question set for a state's ballot (empty if not covered or not generated). */
export function getStatements(state: StateCode | null): Statement[] {
    return state ? ballots[state] : [];
}

/** Sourced candidate backgrounds, keyed by candidate id (ids are unique across states). */
export const bios: Record<string, CandidateBio> = allBios;
