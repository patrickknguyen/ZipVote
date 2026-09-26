import {
    UserResponse,
    CandidateMatch,
    Candidate,
    TopicResult,
    MatchDetail,
    CandidateTopicAlignment,
    Statement,
} from "@/types";

/**
 * Agreement scoring:
 * Only statements where the candidate has a stated position count. For each,
 * closeness = 1 - |user - candidate| / 4, so matching intensity matters:
 * "Agree" vs. a candidate's "Strongly agree" is 75%, not 100%.
 * Score = average closeness, 0-100. Separately, answers within 1 point are
 * counted as "same side" for the label.
 */
export function computeMatches(
    candidates: Candidate[],
    responses: UserResponse[],
    allStatements: Statement[]
): CandidateMatch[] {
    return candidates.map((candidate) => {
        let totalAlignment = 0;
        let count = 0;
        let agree = 0;
        const details: MatchDetail[] = [];

        for (const response of responses) {
            const statement = allStatements.find((s) => s.id === response.statementId);
            if (!statement) continue;

            const candidateAgreement = statement.candidateAgreement[candidate.id];
            // Only the candidate's stated positions count. Silence (null) is
            // skipped: it neither raises nor lowers their score.
            if (candidateAgreement == null) continue;

            // Distance 0-4 on the Likert scale → closeness 1.0-0.0, so
            // "Agree" vs. a candidate's "Strongly agree" is a partial match
            const distance = Math.abs(response.value - candidateAgreement);
            totalAlignment += 1 - distance / 4;
            if (distance <= 1) agree++; // same side, for the "same side on X of Y" label
            details.push({
                statement,
                candidateValue: candidateAgreement,
                userValue: response.value,
                agrees: distance <= 1,
            });
            count++;
        }

        // Average closeness on this candidate's stated positions.
        // Fewer than 2 → no score ("not enough info", never 0%).
        const score = count >= 2 ? Math.round((totalAlignment / count) * 100) : null;
        return { candidate, score, basedOn: count, agreeCount: agree, details, maxScore: 100 };
    });
}

/**
 * Returns a percentage (0-100) for a match result.
 */
export function matchPercent(match: CandidateMatch): number | null {
    return match.score;
}

/**
 * Computes which topics the user expressed the strongest opinions on
 * (i.e., furthest from neutral 3 on average across that topic's statements).
 *
 * For each topic also computes:
 *  - candidateAlignments: per-candidate alignment on just that topic's statements
 *  - biggestWedge: the candidate closest to the user on this topic, paired
 *    with the candidate furthest from them.
 *
 * Returns topics sorted by user intensity descending.
 */
export function computeTopicResults(
    responses: UserResponse[],
    allStatements: Statement[],
    candidates: Candidate[]
): TopicResult[] {
    // Build a map of topic → {statements, responses on those statements}
    const topicMap = new Map<
        string,
        {
            statements: Statement[];
            responseMap: Map<string, number>; // statementId → user value
            totalIntensity: number;
            count: number;
            raceInsight?: string;
        }
    >();

    for (const response of responses) {
        const statement = allStatements.find((s) => s.id === response.statementId);
        if (!statement) continue;

        const intensity = Math.abs(response.value - 3) / 2; // 0 (neutral) to 1 (strongly)
        const existing = topicMap.get(statement.topic);
        if (existing) {
            existing.totalIntensity += intensity;
            existing.count++;
            existing.statements.push(statement);
            existing.responseMap.set(statement.id, response.value);
            if (!existing.raceInsight && statement.raceInsight) {
                existing.raceInsight = statement.raceInsight;
            }
        } else {
            topicMap.set(statement.topic, {
                statements: [statement],
                responseMap: new Map([[statement.id, response.value]]),
                totalIntensity: intensity,
                count: 1,
                raceInsight: statement.raceInsight,
            });
        }
    }

    const results: TopicResult[] = [];

    for (const [topic, data] of topicMap.entries()) {
        const avgIntensity = data.totalIntensity / data.count;

        // ── Per-candidate alignment on this topic ──────────────────────────
        // Candidates with a stated position on any of this topic's statements
        const relevantCandidates = candidates.filter((c) =>
            data.statements.some((s) => s.candidateAgreement[c.id] != null)
        );

        const candidateAlignments: CandidateTopicAlignment[] = relevantCandidates
            .map((candidate) => {
                let totalAlign = 0;
                let n = 0;
                for (const statement of data.statements) {
                    const userVal = data.responseMap.get(statement.id);
                    const candAgreement = statement.candidateAgreement[candidate.id];
                    if (userVal === undefined || candAgreement == null) continue;
                    const distance = Math.abs(userVal - candAgreement);
                    totalAlign += 1 - distance / 4; // same closeness rule as the overall score
                    n++;
                }
                return {
                    candidate,
                    alignmentScore: n > 0 ? Math.round((totalAlign / n) * 100) : null,
                };
            })
            // Candidates with no known position on this topic aren't ranked on it
            .filter((a): a is CandidateTopicAlignment => a.alignmentScore !== null)
            .sort((a, b) => b.alignmentScore - a.alignmentScore);

        // ── Biggest wedge: anchored on the user's closest candidate ─────────
        //   1. agree = the candidate whose average position on this topic is
        //      closest to the user's answers.
        //   2. disagree = the candidate furthest from that anchor (gap ≥ 1).
        let biggestWedge: TopicResult["biggestWedge"] = undefined;

        if (relevantCandidates.length >= 2) {
            const avgAgreements = relevantCandidates.map((c) => {
                const vals = data.statements
                    .filter((s) => s.candidateAgreement[c.id] != null)
                    .map((s) => s.candidateAgreement[c.id] as number);
                const avg = vals.length > 0 ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
                return { candidate: c, avg };
            })
            // Candidates with no documented position on this topic can't anchor a wedge
            .filter((a): a is { candidate: Candidate; avg: number } => a.avg !== null);

            const userAvg =
                Array.from(data.responseMap.values()).reduce((a, b) => a + b, 0) /
                data.responseMap.size;

            // Step 1: lock in the agree candidate (closest to user)
            // (needs at least 2 candidates with known positions to compare)
            const anchor =
                avgAgreements.length >= 2
                    ? avgAgreements.reduce((best, cur) =>
                          Math.abs(cur.avg - userAvg) < Math.abs(best.avg - userAvg) ? cur : best
                      )
                    : null;

            // Step 2: find the candidate furthest from the anchor
            let bestScore = -1;
            let disagreeCandidate: Candidate | null = null;

            for (const other of avgAgreements) {
                if (!anchor) break;
                if (other.candidate.id === anchor.candidate.id) continue;
                // Only contrast candidates running against each other
                if (other.candidate.race !== anchor.candidate.race) continue;
                const gap = Math.abs(anchor.avg - other.avg);
                if (gap < 1) continue; // not meaningfully different

                const score = gap;

                if (score > bestScore) {
                    bestScore = score;
                    disagreeCandidate = other.candidate;
                }
            }

            if (anchor && disagreeCandidate) {
                biggestWedge = {
                    agree: anchor.candidate,
                    disagree: disagreeCandidate,
                    gap: bestScore,
                };
            }
        }


        results.push({
            topic,
            userIntensity: avgIntensity,
            topStatements: data.statements,
            raceInsight: data.raceInsight,
            candidateAlignments,
            biggestWedge,
        });
    }

    return results.sort((a, b) => b.userIntensity - a.userIntensity);
}
