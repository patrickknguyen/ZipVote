"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/Button";
import { text } from "@/lib/styles";
import { zipToState, zipToDistricts } from "@/lib/coverage";
import { HOUSE_DISTRICTS, DISTRICT_LOOKUP_URL } from "@/data/house";

export default function HomePage() {
  const [zip, setZip] = useState("");
  const [error, setError] = useState("");
  // Set when a zip crosses congressional district lines: ask which one
  const [districtChoices, setDistrictChoices] = useState<string[]>([]);
  const router = useRouter();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = zip.trim();

    if (trimmed.length !== 5) {
      setError("Enter a 5-digit zip code.");
      return;
    }
    if (!zipToState(trimmed)) {
      setError(
        "ZipVote is starting with statewide races in Massachusetts and California, and your area isn't covered yet. Try a zip like 02144 or 94110 to see how it works."
      );
      return;
    }
    const districts = zipToDistricts(trimmed);
    if (districts.length > 1) {
      setDistrictChoices(districts);
      return;
    }
    const cd = districts.length === 1 ? `&cd=${districts[0]}` : "";
    router.push(`/quiz?zip=${trimmed}${cd}`);
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
              setDistrictChoices([]);
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
        {districtChoices.length > 1 && (
          <div className="mt-6 w-full max-w-md rounded-2xl border border-slate-200 bg-white/85 p-5 text-left backdrop-blur-sm">
            <p className="text-base font-semibold text-slate-900">
              {zip} crosses {districtChoices.length} congressional districts. Which one is yours?
            </p>
            <div className="mt-3 space-y-2">
              {districtChoices.map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => router.push(`/quiz?zip=${zip}&cd=${d}`)}
                  className="flex w-full items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3 text-left transition hover:border-violet-300 hover:bg-violet-50"
                >
                  <span className="font-semibold text-slate-900">{d}</span>
                  {HOUSE_DISTRICTS[d] && (
                    <span className="text-sm text-slate-500">
                      Represented now by {HOUSE_DISTRICTS[d].representative}
                    </span>
                  )}
                </button>
              ))}
            </div>
            {DISTRICT_LOOKUP_URL[zipToState(zip) ?? ""] && (
              <a
                href={DISTRICT_LOOKUP_URL[zipToState(zip) ?? ""]}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-3 inline-block text-sm text-slate-500 underline hover:text-slate-700"
              >
                Not sure? Your voter registration lists your district ↗
              </a>
            )}
          </div>
        )}
        {error && (
          <p
            role="status"
            className="mt-4 max-w-md rounded-xl border border-slate-200 bg-white/80 px-4 py-3 text-sm leading-relaxed text-slate-600 backdrop-blur-sm"
          >
            {error}
          </p>
        )}
      </div>
    </PageShell>
  );
}
