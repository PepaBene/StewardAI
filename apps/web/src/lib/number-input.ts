/**
 * Separators in a typed number. Both "," and ".": the last one is the decimal point ("1.234,5").
 * Only ",": groups of exactly three digits are thousands ("1,500", "1,000,000"); anything else
 * is a decimal comma ("12,5"). Used by every form that reads a number the owner typed.
 */
export function normaliseSeparators(text: string): string {
  const s = text.trim();
  if (s.includes(",") && s.includes(".")) {
    const decimal = Math.max(s.lastIndexOf(","), s.lastIndexOf("."));
    return s.slice(0, decimal).replace(/[.,]/g, "") + "." + s.slice(decimal + 1);
  }
  if (/^[-+−]?\d{1,3}(,\d{3})+$/.test(s)) return s.replace(/,/g, "");
  return s.replace(",", ".");
}
