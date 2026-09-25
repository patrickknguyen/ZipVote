import { btn } from "@/lib/styles";

type Variant = "primary" | "secondary" | "ghost";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
    variant?: Variant;
}

/**
 * Shared button — use `variant` to pick the design-token style.
 */
export function Button({ variant = "primary", className = "", children, ...props }: ButtonProps) {
    return (
        <button className={`${btn[variant]} ${className}`} {...props}>
            {children}
        </button>
    );
}
