const EDGE = 12;
const GAP = 6;
export const LISTBOX_MAX_HEIGHT = 288;

/** Keep the complete popup inside the viewport, including short landscape screens. */
export function listboxLayout(
  bounds: { top: number; bottom: number; left: number; width: number },
  viewport: { width: number; height: number },
  contentHeight: number,
) {
  const below = Math.max(0, viewport.height - bounds.bottom - GAP - EDGE);
  const above = Math.max(0, bounds.top - GAP - EDGE);
  const wanted = Math.min(LISTBOX_MAX_HEIGHT, contentHeight);
  const opensBelow = below >= wanted || below >= above;
  const maxHeight = Math.min(wanted, opensBelow ? below : above);
  const width = Math.min(bounds.width, Math.max(0, viewport.width - EDGE * 2));
  return {
    top: opensBelow ? bounds.bottom + GAP : bounds.top - GAP - maxHeight,
    left: Math.max(EDGE, Math.min(bounds.left, viewport.width - EDGE - width)),
    width,
    maxHeight,
    origin: opensBelow ? "top" as const : "bottom" as const,
  };
}
