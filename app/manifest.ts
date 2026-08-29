import type { MetadataRoute } from "next";

/**
 * Web app manifest, served at `/manifest.webmanifest`.
 *
 * Written as a route rather than a static file so the values are type-checked
 * against what browsers accept, and so the colours stay next to the code that
 * owns them instead of drifting in a hand-edited JSON file.
 *
 * `background_color` and `theme_color` are the dark canvas because the theme
 * boot script in `app/components/theme.tsx` defaults to dark. The splash screen
 * Android draws from these values is shown before any of the app's own CSS
 * loads, so matching that default is what stops the launch flashing pale.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "Trade Cognition",
    // Android truncates anything much past a dozen characters under the icon.
    short_name: "Cognition",
    description: "Configure, schedule, and review Delta Exchange India option strategies, with live market and news analysis.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "any",
    background_color: "#080809",
    theme_color: "#080809",
    categories: ["finance", "productivity"],
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      // Launchers clip to their own shape, so the masked pair carries the safe
      // margin and an opaque backdrop. Without these Android supplies a white
      // plate of its own and the mark floats inside it.
      { src: "/icons/icon-maskable-192.png", sizes: "192x192", type: "image/png", purpose: "maskable" },
      { src: "/icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" }
    ]
  };
}
