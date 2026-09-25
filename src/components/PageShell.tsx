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
        </main>
    );
}
