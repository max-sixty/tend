// Compact relative-time formatter for live-data timestamps ("12 min ago").
export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "";
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  const minutes = Math.round(seconds / 60);
  const hours = Math.round(minutes / 60);
  const days = Math.round(hours / 24);
  if (seconds < 90) return "just now";
  if (minutes < 90) return `${minutes} min ago`;
  if (hours < 36) return `${hours} h ago`;
  return `${days} d ago`;
}
