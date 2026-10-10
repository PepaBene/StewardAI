/** Dollars for a spend figure: tiny amounts keep their precision, larger ones round to cents. */
export function usdLabel(value: number): string {
  return value > 0 && value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
}

/** Minutes as "45 min" or "2 h 10 min"; "n/a" when unknown. */
export function minutesLabel(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return "n/a";
  const whole = Math.round(minutes);
  if (whole < 60) return `${whole} min`;
  const rest = whole % 60;
  return rest === 0 ? `${Math.floor(whole / 60)} h` : `${Math.floor(whole / 60)} h ${rest} min`;
}
