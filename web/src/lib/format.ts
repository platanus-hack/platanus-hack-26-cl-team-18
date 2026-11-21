/**
 * Display formatting for es-CL: dot thousands, comma decimals. Presentation
 * only; money stays a decimal string everywhere else.
 */
export function decimalCl(value: string, fractionDigits?: number): string {
    const negative = value.startsWith("-");
    const [whole = "0", fraction = ""] = value.replace("-", "").split(".");
    const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    const digits =
        fractionDigits === undefined
            ? fraction
            : fraction.padEnd(fractionDigits, "0").slice(0, fractionDigits);
    return `${negative ? "-" : ""}${grouped}${digits ? `,${digits}` : ""}`;
}

export const moneyCl = (amount: string) => `$${decimalCl(amount)}`;
