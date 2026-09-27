/** Every ~ceil(n/max)-th point, evenly spaced, always keeping the first and last. */
export function downsample(values: readonly number[], max: number): number[] {
  if (values.length <= max) return [...values];
  const step = (values.length - 1) / (max - 1);
  return Array.from({ length: max }, (_, i) => values[Math.round(i * step)]);
}
