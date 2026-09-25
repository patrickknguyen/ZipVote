// Race ids must match RACES in agents/config.py
export type Race = "senate" | "house_ma7" | "governor";

export type Party = "Democrat" | "Republican" | "Independent";

export interface Candidate {
    id: string;
    name: string;
    party: Party;
    race: Race;
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
    race: Race;
    topic: string;
    text: string; // Declarative sentence, e.g. "The federal government should…"
    // 1=strongly disagree … 5=strongly agree; null = not enough public information
    candidateAgreement: Record<string, number | null>;
    // Verbatim quote and source URL behind each score (null when unknown)
    evidence?: Record<string, { quote: string; source: string } | null>;
    raceInsight?: string;
    // "Learn more" background shown in quiz dropdown
    policyBackground?: string;
    // Source documentation/article for the policy background
    policySource?: string;
    // One-sentence position per candidate, shown on results screen
    candidatePositions?: Record<string, string>;
}

export interface UserResponse {
    statementId: string;
    value: number; // 1-5 Likert
}

// Alias kept so URL encoding stays compatible
export type UserAnswer = UserResponse;

export interface CandidateMatch {
    candidate: Candidate;
    score: number;    // 0-100 alignment percentage
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
