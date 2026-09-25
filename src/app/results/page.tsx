"use client";

import { useSearchParams, useRouter } from "next/navigation";
import { Suspense, useState } from "react";
import Image from "next/image";
import { candidates } from "@/data/candidates";
import { races } from "@/data/races";
import { statements } from "@/data/statements";
import { computeMatches, computeTopicResults, matchPercent } from "@/lib/matching";
import { UserResponse, CandidateMatch, TopicResult, Statement, Party } from "@/types";

// ── Topic colour map ─────────────────────────────────────────────────────────
import { VennBackground } from "@/components/VennBackground";
import { topicColors, defaultColors } from "@/lib/theme";
import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/Button";
import { text as ds, badge } from "@/lib/styles";

// ── Helpers ───────────────────────────────────────────────────────────────────
/** Party → dot, bar, and badge styles. Independents get a neutral slate. */
const partyStyles: Record<Party, { dot: string; bar: string; badge: string }> = {
    Democrat: { dot: "bg-indigo-400", bar: "bg-indigo-400", badge: badge.democrat },
    Republican: { dot: "bg-red-400", bar: "bg-red-400", badge: badge.republican },
    Independent: { dot: "bg-slate-400", bar: "bg-slate-400", badge: badge.independent },
};

/** Return the first statement in a TopicResult that has candidatePositions */
function getPositionsStatement(result: TopicResult): Statement | undefined {
    return result.topStatements.find((s) => s.candidatePositions);
}

// ── Main component ────────────────────────────────────────────────────────────
function ResultsContent() {
    const searchParams = useSearchParams();
    const router = useRouter();
    const zip = searchParams.get("zip") ?? "02144";
    const answersParam = searchParams.get("answers");

    let responses: UserResponse[] = [];
    try {
        responses = answersParam ? JSON.parse(decodeURIComponent(answersParam)) : [];
    } catch {
        responses = [];
    }

    const matches = computeMatches(candidates, responses, statements);
    const topicResults = computeTopicResults(responses, statements, candidates);

    // One alignment section per race that has statements
    const raceSections = races
        .filter((r) => statements.some((s) => s.race === r.id))
        .map((r) => ({
            ...r,
            matches: matches
                .filter((m) => m.candidate.race === r.id)
                .sort((a, b) => b.score - a.score),
        }));

    const topWedges = topicResults.slice(0, 3);

    return (
        <PageShell>
            <div className="relative z-10 max-w-7xl mx-auto">

                {/* ── Compact Header ── */}
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-8 pb-6 border-b border-slate-100">
                    <div>
                        <span className="mb-24 inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-100/50 px-5 py-2 text-sm font-medium text-slate-600 backdrop-blur-sm">
                            📍 {zip} · 2026 Elections
                        </span>
                        <h1 className={`${ds.pageTitle} text-4xl sm:text-5xl`}>
                            What matters to you
                        </h1>
                        <p className="text-lg text-slate-500 mt-1.5">
                            Your issues · where candidates stand
                        </p>
                    </div>
                    <div className="flex gap-2 flex-shrink-0">
                        <Button variant="secondary" onClick={() => router.push(`/quiz?zip=${zip}`)}>
                            ← Retake
                        </Button>
                        <Button variant="primary" onClick={() => router.push("/")}>
                            New Zip
                        </Button>
                    </div>
                </div>

                {/* ── 60/40 body grid ── */}
                <div className="grid grid-cols-1 lg:grid-cols-5 gap-8 items-start">

                    {/* Left column: Top Issues — 60% width */}
                    <section className="lg:col-span-3">
                        <h2 className={`${ds.sectionTitle} mb-5`}>
                            Your Top Issues
                        </h2>
                        <div className="space-y-5">
                            {topWedges.map((tr) => (
                                <WedgeCard key={tr.topic} result={tr} />
                            ))}
                        </div>
                    </section>

                    {/* Right column: Candidate Alignment — 40% width */}
                    <section className="lg:col-span-2">
                        <h2 className={`${ds.sectionTitle} mb-5`}>
                            How Candidates Align
                        </h2>
                        {raceSections.map((r) => (
                            <AlignmentSection key={r.id} title={r.title} matches={r.matches} />
                        ))}
                    </section>

                </div>
            </div>
        </PageShell>
    );
}

// ── WedgeCard ─────────────────────────────────────────────────────────────────
function WedgeCard({ result }: { result: TopicResult }) {
    const c = topicColors[result.topic] ?? defaultColors;
    const top = result.candidateAlignments[0];
    const wedge = result.biggestWedge;
    const posStatement = getPositionsStatement(result);

    return (
        <div className={`rounded-2xl border ${c.border} ${c.bg} p-5 backdrop-blur-sm shadow-sm`}>
            {/* Topic header — label only, no dot */}
            <div className="mb-1">
                <span className={`text-sm font-bold uppercase tracking-widest ${c.text}`}>
                    {result.topic}
                </span>
            </div>

            {/* Why this race matters */}
            {result.raceInsight && (
                <p className="text-slate-700 text-base leading-relaxed mb-3">
                    {result.raceInsight}
                </p>
            )}

            {/* Candidate alignment on this topic */}
            <div className="space-y-3">
                {/* Top match */}
                {top && (
                    <div className="rounded-xl bg-white border border-slate-200 overflow-hidden shadow-sm">
                        <div className="flex items-center gap-3 px-4 py-2">
                            {/* Avatar: photo or initials fallback */}
                            <div className="relative w-8 h-8 flex-shrink-0">
                                {top.candidate.imageUrl ? (
                                    <Image
                                        src={top.candidate.imageUrl}
                                        alt={top.candidate.name}
                                        fill
                                        className="rounded-full object-cover ring-1 ring-slate-100 shadow-sm"
                                        sizes="32px"
                                    />
                                ) : (
                                    <div
                                        className={`w-8 h-8 rounded-full ${top.candidate.color} flex items-center justify-center text-white font-bold text-[10px] shadow-sm ring-1 ring-slate-100`}
                                    >
                                        {top.candidate.imageInitials}
                                    </div>
                                )}
                            </div>
                            <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2">
                                    <p className="text-slate-900 text-base font-bold truncate">
                                        {top.candidate.name}
                                    </p>
                                </div>
                                <p className="text-slate-600 text-sm">
                                    {top.candidate.party} · most aligned with you
                                </p>
                            </div>
                            <span className="text-slate-700 font-extrabold text-lg flex-shrink-0">
                                {top.alignmentScore}%
                            </span>
                        </div>
                        {posStatement?.candidatePositions?.[top.candidate.id] && (
                            <div className="border-t border-slate-100 px-4 py-3 bg-slate-50/50">
                                <p className="text-slate-600 text-base leading-relaxed">
                                    {posStatement.candidatePositions[top.candidate.id]}
                                </p>
                            </div>
                        )}
                    </div>
                )}

                {/* Biggest wedge callout — expanded with per-candidate positions */}
                {wedge && wedge.agree.id !== wedge.disagree.id && (
                    <div className="rounded-xl bg-slate-50 border border-slate-200 px-4 py-3 space-y-2">
                        <div className="flex items-start gap-3">
                            <span className="text-slate-400 text-sm mt-0.5 flex-shrink-0">⚡</span>
                            <p className="text-slate-600 text-sm leading-relaxed">
                                <span className="text-slate-800 font-semibold">{wedge.agree.name}</span>
                                {" "}and{" "}
                                <span className="text-slate-800 font-semibold">{wedge.disagree.name}</span>
                                {" "}are far apart on this issue.
                            </p>
                        </div>
                        {/* Per-candidate position quotes explaining the gap */}
                        {posStatement?.candidatePositions && (
                            <div className="ml-6 space-y-1.5">
                                {[wedge.agree, wedge.disagree].map((c) =>
                                    posStatement.candidatePositions?.[c.id] ? (
                                        <div key={c.id} className="flex items-start gap-2">
                                            <span className={`mt-[7px] w-1.5 h-1.5 rounded-full flex-shrink-0 ${partyStyles[c.party].dot}`} />
                                            <p className="text-slate-500 text-sm leading-relaxed">
                                                <span className="font-semibold text-slate-600">{c.name}:</span>{" "}
                                                {posStatement.candidatePositions[c.id]}
                                            </p>
                                        </div>
                                    ) : null
                                )}
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

// ── Expandable candidate alignment bars ───────────────────────────────────────
function AlignmentSection({
    title,
    matches,
}: {
    title: string;
    matches: CandidateMatch[];
}) {
    if (matches.length === 0) return null;
    const maxScore = Math.max(...matches.map((m) => m.score));

    return (
        <div className="mb-8">
            <p className={`${ds.label} mb-3 pl-0.5`}>
                {title}
            </p>
            <div className="space-y-3">
                {matches.map((match) => (
                    <CandidateCard key={match.candidate.id} match={match} isTop={match.score === maxScore} />
                ))}
            </div>
        </div>
    );
}

function CandidateCard({ match, isTop }: { match: CandidateMatch; isTop: boolean }) {
    const [expanded, setExpanded] = useState(false);
    const pct = matchPercent(match);
    const partyColor = partyStyles[match.candidate.party].bar;

    return (
        <div
            className="rounded-2xl border border-slate-200 bg-white transition-all overflow-hidden cursor-pointer select-none hover:border-slate-300 hover:shadow-sm"
            onClick={() => setExpanded((v) => !v)}
        >
            <div className="flex items-center gap-4 p-4">
                {/* Avatar: photo headshot or initials fallback */}
                <div className="relative w-10 h-10 flex-shrink-0">
                    {match.candidate.imageUrl ? (
                        <Image
                            src={match.candidate.imageUrl}
                            alt={match.candidate.name}
                            fill
                            className="rounded-full object-cover ring-2 ring-white shadow-sm"
                            sizes="40px"
                        />
                    ) : (
                        <div
                            className={`w-10 h-10 rounded-full ${match.candidate.color} flex items-center justify-center text-white font-bold text-xs shadow-sm ring-2 ring-white`}
                        >
                            {match.candidate.imageInitials}
                        </div>
                    )}
                </div>
                <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap mb-2">
                        <span className="text-slate-900 font-bold text-base">
                            {match.candidate.name}
                        </span>
                        <span className={partyStyles[match.candidate.party].badge}>
                            {match.candidate.party}
                        </span>

                    </div>
                    {/* Progress Track */}
                    <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                        <div
                            className={`h-full rounded-full transition-all duration-700 ${partyColor}`}
                            style={{ width: `${pct}%` }}
                        />
                    </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0 ml-2">
                    <span className={`text-xl font-extrabold ${isTop ? "text-slate-900" : "text-slate-700"}`}>
                        {pct}%
                    </span>
                    <span className={`text-slate-500 text-sm transition-transform duration-200 ${expanded ? "rotate-90" : "rotate-0 text-slate-500"}`}>
                        ›
                    </span>
                </div>
            </div>

            {/* Expanded panel: bio + source links */}
            {expanded && (
                <div className="border-t border-slate-200 px-5 py-4 bg-slate-50/60 space-y-4">
                    {/* Biography */}
                    {match.candidate.bio && (
                        <p className="text-slate-700 text-base leading-relaxed">
                            {match.candidate.bio}
                        </p>
                    )}
                    {/* Source links */}
                    {(match.candidate.wikipedia || match.candidate.ballotpedia) && (
                        <div className="flex items-center gap-3 pt-1">
                            <span className="text-xs font-bold uppercase tracking-widest text-slate-400">Learn more</span>
                            {match.candidate.wikipedia && (
                                <a
                                    href={match.candidate.wikipedia}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    onClick={(e) => e.stopPropagation()}
                                    className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900 underline underline-offset-2 transition"
                                >
                                    Wikipedia
                                </a>
                            )}
                            {match.candidate.ballotpedia && (
                                <a
                                    href={match.candidate.ballotpedia}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    onClick={(e) => e.stopPropagation()}
                                    className="inline-flex items-center gap-1 text-sm text-slate-600 hover:text-slate-900 underline underline-offset-2 transition"
                                >
                                    Ballotpedia
                                </a>
                            )}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

export default function ResultsPage() {
    return (
        <Suspense>
            <ResultsContent />
        </Suspense>
    );
}
