import { VennBackground } from "@/components/VennBackground";
import { layout } from "@/lib/styles";

interface PageShellProps {
    children: React.ReactNode;
    /** Centers content vertically + horizontally (home / quiz). Default: false (results). */
    centered?: boolean;
    /** Extra classes on the outer <main> */
    className?: string;
}

/**
 * Shared page shell — consistent bg, VennBackground, and z-layer for all pages.
 */
export function PageShell({ children, centered = false, className = "" }: PageShellProps) {
    return (
        <main
            className={`${layout.page} ${centered ? "flex flex-col items-center justify-center px-4" : "px-6 py-8"
                } ${className}`}
        >
            <VennBackground variant={centered ? "light" : undefined} />
            {children}
            <Disclosure />
        </main>
    );
}

/** Shown on every page: what this is, how it was made, where to get the real ballot. */
function Disclosure() {
    return (
        <p className="relative z-10 mx-auto mt-12 max-w-2xl px-4 pb-6 text-center text-xs leading-relaxed text-slate-500">
            Prototype. Questions and position summaries are written by a language model from each
            candidate&apos;s campaign website and Wikipedia, checked automatically against quoted
            sources, and spot-checked by hand. Not an official voter guide. See your official ballot at{" "}
            <a
                href="https://www.sec.state.ma.us/divisions/elections/elections-and-voting.htm"
                target="_blank"
                rel="noopener noreferrer"
                className="underline hover:text-slate-700"
            >
                sec.state.ma.us
            </a>
            .
        </p>
    );
}
