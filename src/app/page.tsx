"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/Button";
import { text } from "@/lib/styles";

const SUPPORTED_ZIPS = ["02144"];

export default function HomePage() {
  const [zip, setZip] = useState("");
  const [error, setError] = useState("");
  const router = useRouter();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = zip.trim();

    if (!SUPPORTED_ZIPS.includes(trimmed)) {
      setError(
        `We don't have data for ${trimmed} yet. Try 02144 for the prototype.`
      );
      return;
    }
    router.push(`/quiz?zip=${trimmed}`);
  }

  return (
    <PageShell centered>
      <div className="relative z-10 flex flex-col items-center text-center w-full max-w-3xl pb-24">

        {/* Season badge */}
        <span className="mb-24 inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-100/50 px-5 py-2 text-sm font-medium text-slate-600 backdrop-blur-sm">
          🗳️ 2026 General Election · Nov 3
        </span>

        {/* Logo */}
        <h1 className={`${text.pageTitle} text-7xl sm:text-8xl text-white drop-shadow-sm mb-5`}>
          ZipVote
        </h1>

        {/* Tagline */}
        <p className="text-xl sm:text-2xl font-medium text-slate-600 mb-3 leading-snug">
          Your zip code. Your candidates. Your choice.
        </p>
        <p className="text-lg text-slate-600 mb-8 max-w-2xl leading-relaxed">
          Answer questions about issues that matter to you,<br className="hidden sm:block" />
          then see which candidates align with your values.
        </p>

        {/* Zip form */}
        <form onSubmit={handleSubmit} className="w-full flex flex-col sm:flex-row justify-center items-center gap-4 mt-4">
          <input
            id="zip-input"
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            maxLength={5}
            value={zip}
            onChange={(e) => {
              const val = e.target.value.replace(/\D/g, "");
              setZip(val);
              setError("");
            }}
            placeholder="Enter zipcode"
            className="w-40 rounded-full border-2 border-slate-200 bg-white px-4 py-2 text-center text-lg font-medium tracking-widest text-slate-900 tabular-nums shadow-sm transition placeholder:text-base placeholder:font-normal placeholder:tracking-normal placeholder:text-slate-400 focus:border-violet-400 focus:outline-none focus:ring-4 focus:ring-violet-400/20"
          />
          <Button
            id="zip-submit"
            type="submit"
            variant="primary"
            className="px-8 py-3.5 text-lg shadow-md"
          >
            Start
          </Button>
        </form>
        {error && (
          <p className="mt-3 text-sm text-red-500">{error}</p>
        )}
      </div>
    </PageShell>
  );
}
