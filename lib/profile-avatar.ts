/** Select a usable profile photo from Auth metadata or the backend profile. */
export function resolveProfileAvatar(...candidates: unknown[]): string | null {
  for (const candidate of candidates) {
    if (typeof candidate !== "string" || !candidate.trim()) continue;
    try {
      const url = new URL(candidate.trim());
      if (url.protocol === "https:" && !url.username && !url.password) return url.href;
    } catch {
      // Invalid metadata must not prevent the account from rendering.
    }
  }
  return null;
}
