// Compact relative-time formatter for live-data timestamps ("12 min ago").
export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "";
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  const minutes = Math.round(seconds / 60);
  const hours = Math.round(minutes / 60);
  const days = Math.round(hours / 24);
  if (seconds < 90) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (hours < 36) return `${hours} h ago`;
  return `${days} d ago`;
}

// Live elapsed timer for in-flight runs — "m:ss", or "h:mm:ss" past an hour.
// Returns "" when startedAt isn't a parseable timestamp.
export function elapsedTime(startedAt: string): string {
  const startedMs = Date.parse(startedAt);
  if (Number.isNaN(startedMs)) return "";
  const s = Math.max(0, Math.floor((Date.now() - startedMs) / 1000));
  const hh = Math.floor(s / 3600);
  const mm = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  if (hh > 0) return `${hh}:${pad(mm)}:${pad(ss)}`;
  return `${mm}:${pad(ss)}`;
}
