import {
    UserResponse,
    CandidateMatch,
    Candidate,
    TopicResult,
    CandidateTopicAlignment,
    Statement,
} from "@/types";

/**
 * Likert alignment scoring:
 * For each statement the user responded to, we compute the distance between
 * the user's rating (1-5) and each candidate's agreement (1-5).
 * Distance 0 = perfect match → contribution of 1.0
 * Distance 4 = total opposite → contribution of 0.0
 *
 * Alignment score = average contribution across all statements relevant to
 * the candidate's race, expressed as 0-100.
 */
export function computeMatches(
    candidates: Candidate[],
    responses: UserResponse[],
    allStatements: Statement[]
): CandidateMatch[] {
    return candidates.map((candidate) => {
        let totalAlignment = 0;
        let count = 0;

        for (const response of responses) {
            const statement = allStatements.find((s) => s.id === response.statementId);
            if (!statement) continue;
            if (statement.race !== candidate.race) continue;

            const candidateAgreement = statement.candidateAgreement[candidate.id];
            // Unknown positions (null) are skipped, not treated as neutral
            if (candidateAgreement == null) continue;

            // Distance 0-4, translated to alignment 0-1
            const distance = Math.abs(response.value - candidateAgreement);
            const alignment = 1 - distance / 4;
            totalAlignment += alignment;
            count++;
        }

        const score = count > 0 ? Math.round((totalAlignment / count) * 100) : 0;
        return { candidate, score, maxScore: 100 };
    });
}

/**
 * Returns a percentage (0-100) for a match result.
 */
export function matchPercent(match: CandidateMatch): number {
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
        // Only include candidates whose race covers at least one statement in this topic
        const relevantRaces = new Set(data.statements.map((s) => s.race));
        const relevantCandidates = candidates.filter((c) => relevantRaces.has(c.race));

        const candidateAlignments: CandidateTopicAlignment[] = relevantCandidates
            .map((candidate) => {
                let totalAlign = 0;
                let n = 0;
                for (const statement of data.statements) {
                    if (statement.race !== candidate.race) continue;
                    const userVal = data.responseMap.get(statement.id);
                    const candAgreement = statement.candidateAgreement[candidate.id];
                    if (userVal === undefined || candAgreement == null) continue;
                    const distance = Math.abs(userVal - candAgreement);
                    totalAlign += 1 - distance / 4;
                    n++;
                }
                return {
                    candidate,
                    alignmentScore: n > 0 ? Math.round((totalAlign / n) * 100) : 50,
                };
            })
            .sort((a, b) => b.alignmentScore - a.alignmentScore);

        // ── Biggest wedge: anchored on the user's closest candidate ─────────
        //   1. agree = the candidate whose average position on this topic is
        //      closest to the user's answers.
        //   2. disagree = the candidate furthest from that anchor (gap ≥ 1).
        let biggestWedge: TopicResult["biggestWedge"] = undefined;

        if (relevantCandidates.length >= 2) {
            const avgAgreements = relevantCandidates.map((c) => {
                const vals = data.statements
                    .filter((s) => s.race === c.race && s.candidateAgreement[c.id] != null)
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
