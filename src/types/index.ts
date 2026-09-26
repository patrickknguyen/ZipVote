// Race ids must match RACES in agents/config.py
export type Race = "senate" | "governor" | "ca_governor";

// States with generated ballots. Each has its own question set.
export type StateCode = "MA" | "CA";

export type Party = "Democrat" | "Republican" | "Independent";

export interface Candidate {
    id: string;
    name: string;
    party: Party;
    race: Race;
    state: StateCode;
    // Factual role only (e.g. "U.S. Senator since 2013"). Positions come from
    // the sourced pipeline output, never from hand-written copy here.
    bio: string;
    imageInitials: string;
    imageUrl?: string;      // headshot — Wikipedia thumbnail URL preferred
    wikipedia?: string;     // full Wikipedia article URL
    ballotpedia?: string;   // full Ballotpedia article URL
    color: string; // tailwind bg color class
}

/**
 * A declarative statement the user rates on a 1-5 Likert scale.
 * candidateAgreement maps candidateId → their own agreement (1-5).
 * The closer the user's response is to a candidate's agreement, the higher the alignment.
 */
export interface Statement {
    id: string;
    // "ballot": one question set for the whole ballot. Any candidate with a
    // score in candidateAgreement is rated on it, whatever office they seek.
    race: string;
    topic: string;
    text: string; // Declarative sentence, e.g. "The federal government should…"
    // 1=strongly disagree … 5=strongly agree; null = not enough public information
    candidateAgreement: Record<string, number | null>;
    // Verbatim quote and source URL behind each score (null when unknown)
    evidence?: Record<string, { quote: string; source: string } | null>;
    // false when a candidate's quote shows their side indirectly (a related
    // action or statement) rather than stating this exact policy
    evidenceDirect?: Record<string, boolean>;
    raceInsight?: string;
    // "Learn more" background shown in quiz dropdown
    policyBackground?: string;
    // Source documentation/article for the policy background
    policySource?: string;
    // One-sentence position per candidate, shown on results screen
    candidatePositions?: Record<string, string>;
}

/** Short background generated from sources; every sentence has a quote. */
export interface CandidateBio {
    sentences: { text: string; quote: string; source: string }[];
    selfDescribed: boolean; // true when the only source is the campaign's own About page
}

/** One question behind a candidate's score: what they said vs. what the user said. */
export interface MatchDetail {
    statement: Statement;
    candidateValue: number; // candidate's 1-5 position
    userValue: number;      // user's 1-5 answer
    agrees: boolean;        // within 1 point
}

export interface UserResponse {
    statementId: string;
    value: number; // 1-5 Likert
}

// Alias kept so URL encoding stays compatible
export type UserAnswer = UserResponse;

export interface CandidateMatch {
    candidate: Candidate;
    score: number | null; // 0-100 alignment; null = no known positions to compare
    basedOn: number;      // how many of this candidate's stated positions the user rated
    agreeCount: number;   // of those, how many the user agreed with (within 1 point)
    details: MatchDetail[]; // each question behind the score, for the results dropdown
    maxScore: number; // always 100 conceptually, kept for API compat
}

export interface CandidateTopicAlignment {
    candidate: Candidate;
    alignmentScore: number; // 0-100 for this topic only
}

export interface TopicResult {
    topic: string;
    userIntensity: number;   // how far from neutral (0=neutral, 1=max)
    topStatements: Statement[];
    raceInsight?: string;
    // Candidates sorted by alignment on this topic (descending)
    candidateAlignments: CandidateTopicAlignment[];
    // Pair with the biggest candidate disagreement on this topic
    biggestWedge?: { agree: Candidate; disagree: Candidate; gap: number };
}
