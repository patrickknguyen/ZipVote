/**
 * Design tokens — shared Tailwind class strings for the ZipVote design system.
 * Import these instead of writing ad-hoc classes in every component.
 */

// ── Buttons ──────────────────────────────────────────────────────────────────
export const btn = {
    /** Dark-filled primary action (e.g. "New Zip", "Start") */
    primary:
        "rounded-full bg-violet-500 hover:bg-violet-600 active:scale-[0.98] px-5 py-2 text-white font-semibold text-sm transition shadow-lg shadow-violet-500/20",
    /** Outlined secondary action (e.g. "← Retake") */
    secondary:
        "rounded-full border border-slate-200 bg-white hover:bg-slate-50 px-5 py-2 text-slate-700 font-semibold text-sm transition shadow-sm",
    /** Ghost – used for subtle inline actions */
    ghost:
        "rounded-full px-4 py-2 text-slate-700 hover:text-slate-900 hover:bg-slate-50 font-medium text-sm transition",
} as const;

// ── Typography ────────────────────────────────────────────────────────────────
export const text = {
    /** Page / section title (h1 / h2) */
    pageTitle: "text-4xl sm:text-5xl font-extrabold text-slate-900 tracking-tight",
    sectionTitle: "text-xl font-bold text-slate-900",
    /** Small-caps label above sections or inside cards (e.g. "U.S. SENATE") */
    label: "text-[10px] font-bold uppercase tracking-widest text-slate-600",
    /** Standard body copy */
    body: "text-sm text-slate-700 leading-relaxed",
    /** Subdued caption / helper text */
    caption: "text-xs text-slate-500",
} as const;

// ── Cards ─────────────────────────────────────────────────────────────────────
export const card = {
    /** Standard rounded card with border */
    base: "rounded-2xl border border-slate-200 bg-white shadow-sm",
    /** Inner content area (expanded panel background) */
    inner: "bg-slate-50/60",
} as const;

// ── Badges ────────────────────────────────────────────────────────────────────
export const badge = {
    democrat: "text-indigo-700 bg-indigo-50 border border-indigo-100 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide",
    republican: "text-red-700 bg-red-50 border border-red-100 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide",
    independent: "text-slate-700 bg-slate-100 border border-slate-200 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide",
    neutral: "text-slate-500 bg-slate-100 border border-slate-200 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
} as const;

// ── Layout ────────────────────────────────────────────────────────────────────
export const layout = {
    /** Full-page <main> shell */
    page: "relative min-h-screen overflow-hidden bg-white",
    /** Max-width content container */
    container: "relative z-10 max-w-7xl mx-auto",
    /** Centered content container (home / quiz) */
    containerCentered: "relative z-10 flex flex-col items-center text-center w-full max-w-2xl",
} as const;
