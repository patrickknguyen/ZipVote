"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { statements } from "@/data/statements";
import { UserResponse } from "@/types";
import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/Button";

function QuizContent() {
    const router = useRouter();
    const searchParams = useSearchParams();
    const zip = searchParams.get("zip") ?? "02144";

    const [currentIndex, setCurrentIndex] = useState(0);
    const [selected, setSelected] = useState<number | null>(null);
    const [responses, setResponses] = useState<UserResponse[]>([]);
    const [showLearnMore, setShowLearnMore] = useState(false);

    const current = statements[currentIndex];
    const progress = ((currentIndex + 1) / statements.length) * 100;

    function handleSelect(value: number) {
        setSelected(value);
        setTimeout(() => advance(value), 350);
    }

    function advance(value: number) {
        const updated = [...responses, { statementId: current.id, value }];
        setResponses(updated);

        if (currentIndex + 1 < statements.length) {
            setCurrentIndex((i) => i + 1);
            setSelected(null);
            setShowLearnMore(false);
        } else {
            const encoded = encodeURIComponent(JSON.stringify(updated));
            router.push(`/results?zip=${zip}&answers=${encoded}`);
        }
    }

    // No generated statements yet (pipeline hasn't run): say so plainly
    // instead of showing placeholder questions about real candidates.
    if (!current) {
        return (
            <PageShell centered className="py-12">
                <div className="relative z-10 max-w-xl text-center space-y-4">
                    <p className="text-2xl font-bold text-slate-900">Questions aren&apos;t ready yet</p>
                    <p className="text-slate-600">
                        We haven&apos;t generated questions for {zip}&apos;s races. Check back soon.
                    </p>
                    <Button variant="secondary" onClick={() => router.push("/")}>
                        ← Back
                    </Button>
                </div>
            </PageShell>
        );
    }

    return (
        <PageShell centered className="py-12">
            {/* Progress bar */}
            <div className="relative z-10 w-full max-w-4xl mb-12">
                <div className="flex justify-between items-end text-xs font-semibold text-slate-600 mb-3 uppercase tracking-wider">
                    <Button
                        variant="ghost"
                        className="hover:text-slate-900 hover:bg-white/40 backdrop-blur-md transition flex items-center gap-1 px-0"
                        onClick={() => {
                            if (currentIndex > 0) {
                                setCurrentIndex((i) => i - 1);
                                setSelected(null);
                                setShowLearnMore(false);
                            } else {
                                router.push(`/?zip=${zip}`);
                            }
                        }}
                    >
                        ← Back
                    </Button>
                    <span>Question {currentIndex + 1} of {statements.length}</span>
                </div>
                <div className="h-1.5 w-full rounded-full bg-slate-100 overflow-hidden">
                    <div
                        className="h-full rounded-full transition-all duration-500 ease-out bg-violet-400"
                        style={{ width: `${progress}%` }}
                    />
                </div>
            </div>

            <div className="relative z-10 w-full max-w-4xl">
                {/* Statement */}
                <h2 className="text-xl sm:text-3xl font-bold text-slate-900 mb-10 leading-snug text-center min-h-[9rem] flex items-center justify-center px-4">
                    {current.text}
                </h2>

                {/* Likert scale */}
                <div className="flex flex-col items-center mb-10">
                    <div className="w-full max-w-xl">
                        <div className="flex justify-between items-center mb-4 px-2">
                            {[1, 2, 3, 4, 5].map((val) => {
                                const isSelected = selected === val;
                                return (
                                    <button
                                        key={val}
                                        id={`likert-${val}`}
                                        onClick={() => handleSelect(val)}
                                        className={`w-12 h-12 sm:w-14 sm:h-14 rounded-full border-2 transition-all duration-200 flex items-center justify-center shadow-md
                                            ${isSelected
                                                ? "border-violet-400 bg-violet-400 scale-110 ring-4 ring-violet-100"
                                                : "border-slate-200 bg-white hover:bg-violet-100"
                                            }`}
                                        aria-label={`Option ${val}`}
                                    />
                                );
                            })}
                        </div>
                        <div className="flex justify-between px-1 text-xs font-medium text-slate-600 select-none uppercase tracking-wide">
                            <span className="text-left w-20">Strongly<br />Disagree</span>
                            <span className="text-center w-20">Neutral</span>
                            <span className="text-right w-20">Strongly<br />Agree</span>
                        </div>
                    </div>
                </div>

                {/* Learn more toggle */}
                {current.policyBackground && (
                    <div className="mt-8 flex flex-col items-center">
                        <Button
                            id="learn-more-toggle"
                            variant="ghost"
                            onClick={() => setShowLearnMore((v) => !v)}
                            className={`flex items-center gap-2 hover:bg-white/40 ${showLearnMore ? "text-slate-900 bg-white/40 backdrop-blur-md" : ""}`}
                        >
                            <span className={`inline-block transition-transform duration-200 ${showLearnMore ? "rotate-90" : "rotate-0"}`}>
                                ›
                            </span>
                            <span>{showLearnMore ? "Hide policy details" : "Learn more about this policy"}</span>
                        </Button>

                        {showLearnMore && (
                            <div className="mt-4 w-full rounded-2xl border border-slate-200 bg-white/40 p-6 backdrop-blur-md shadow-lg animate-in fade-in slide-in-from-top-2 duration-200 text-left">
                                <p className="text-sm text-slate-700 leading-relaxed">
                                    {current.policyBackground}
                                </p>
                                {current.policySource && (
                                    <a
                                        href={current.policySource}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="mt-3 inline-flex items-center gap-1 text-sm font-medium text-violet-600 hover:text-violet-700 underline underline-offset-2 transition-colors"
                                    >
                                        Source Information
                                    </a>
                                )}
                            </div>
                        )}
                    </div>
                )}
            </div>
        </PageShell>
    );
}

export default function QuizPage() {
    return (
        <Suspense>
            <QuizContent />
        </Suspense>
    );
}
