/** Versions belong in metadata, not the name users see in the catalog. */
export function cleanStrategyName(name: string): string {
  return name.trim().replace(/(?:\s*[-–—]?\s*[([]?v(?:ersion)?\s*\d+(?:\.\d+)*[)\]]?)+$/i, "").trim();
}
