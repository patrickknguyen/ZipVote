export function VennBackground({ variant = 'light' }: { variant?: 'light' | 'dark' }) {
    const isDark = variant === 'dark';

    return (
        <>
            {/* ── Noise texture overlay ── */}
            <div
                className="pointer-events-none fixed inset-0 z-0 opacity-[0.04]"
                style={{
                    backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E")`,
                    backgroundRepeat: "repeat",
                    backgroundSize: "128px 128px",
                }}
            />

            {/* ── Venn diagram background ── */}
            <div className={`pointer-events-none fixed inset-0 z-0 flex items-center justify-center transition-colors duration-500 ${isDark ? 'bg-slate-950' : 'bg-white'}`}>
                <svg
                    viewBox="0 0 900 600"
                    className={`w-[112%] h-[112%] transition-opacity duration-1000 ${isDark ? 'opacity-40 mix-blend-screen' : 'opacity-[0.5]'}`}
                    preserveAspectRatio="xMidYMid meet"
                >
                    <defs>
                        <radialGradient id="redGrad" cx="50%" cy="50%" r="55%">
                            <stop offset="0%" stopColor="#ef4444" />
                            <stop offset="80%" stopColor="#dc2626" stopOpacity="0.3" />
                            <stop offset="100%" stopColor="#b91c1c" stopOpacity="0" />
                        </radialGradient>
                        <radialGradient id="blueGrad" cx="50%" cy="50%" r="55%">
                            <stop offset="0%" stopColor="#3b82f6" />
                            <stop offset="80%" stopColor="#2563eb" stopOpacity="0.3" />
                            <stop offset="100%" stopColor="#1d4ed8" stopOpacity="0" />
                        </radialGradient>
                        <filter id="softBlur">
                            <feGaussianBlur stdDeviation="40" />
                        </filter>
                    </defs>

                    {/* Red circle — left */}
                    <ellipse
                        cx="315" cy="300" rx="255" ry="225"
                        fill="url(#redGrad)"
                        filter="url(#softBlur)"
                    />
                    {/* Blue circle — right */}
                    <ellipse
                        cx="585" cy="300" rx="255" ry="225"
                        fill="url(#blueGrad)"
                        filter="url(#softBlur)"
                    />
                </svg>
            </div>
        </>
    );
}
