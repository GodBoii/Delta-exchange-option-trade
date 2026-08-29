/**
 * Home-screen icon set.
 *
 * Android needs a 192px and a 512px icon before it will offer to install the
 * app, and it needs a *maskable* variant on top of that: launchers clip the
 * icon to whatever shape the device uses, so an icon with transparent edges
 * gets a launcher-chosen backdrop and an icon without a safe margin gets its
 * corners cut off. The brand mark ships transparent at 512px, which covers the
 * `any` purpose but not the masked one.
 *
 * The masked and iOS variants sit on the light-theme canvas rather than the
 * dark one. The mark's navy arms are #0a1068, which all but disappear against
 * the #080809 app background once the icon is 48px on a home screen.
 *
 * sharp arrives with Next's image optimiser, so this needs no extra install.
 * Run it after the mark changes:
 *
 *   node scripts/generate-pwa-icons.mjs
 */

import { mkdir } from "node:fs/promises";
import path from "node:path";
import sharp from "sharp";

const ROOT = path.resolve(import.meta.dirname, "..");
const SOURCE = path.join(ROOT, "public", "polycognition-mark.png");
const OUT_DIR = path.join(ROOT, "public", "icons");

/** The light theme's canvas, so a masked icon matches the app it opens. */
const BACKDROP = { r: 0xf4, g: 0xf2, b: 0xee, alpha: 1 };
const TRANSPARENT = { r: 0, g: 0, b: 0, alpha: 0 };

/**
 * A maskable icon's safe zone is the middle 80% of the canvas. 72% of the
 * canvas, minus the padding the source file already carries, puts the mark's
 * arms inside that circle; the corners it would otherwise overflow are empty
 * because the silhouette is three hexagons, not a square.
 */
const MASKABLE_SCALE = 0.72;

/** iOS applies a rounded-rectangle mask, which clips far less than a circle. */
const APPLE_SCALE = 0.8;

async function writeTransparent(size, file) {
  await sharp(SOURCE)
    .resize(size, size, { fit: "contain", background: TRANSPARENT })
    .png({ compressionLevel: 9 })
    .toFile(path.join(OUT_DIR, file));
}

async function writeOnBackdrop(size, scale, file) {
  const inner = Math.round(size * scale);
  const mark = await sharp(SOURCE)
    .resize(inner, inner, { fit: "contain", background: TRANSPARENT })
    .png()
    .toBuffer();

  await sharp({ create: { width: size, height: size, channels: 4, background: BACKDROP } })
    .composite([{ input: mark, gravity: "centre" }])
    .png({ compressionLevel: 9 })
    .toFile(path.join(OUT_DIR, file));
}

await mkdir(OUT_DIR, { recursive: true });
await writeTransparent(192, "icon-192.png");
await writeTransparent(512, "icon-512.png");
await writeOnBackdrop(192, MASKABLE_SCALE, "icon-maskable-192.png");
await writeOnBackdrop(512, MASKABLE_SCALE, "icon-maskable-512.png");
await writeOnBackdrop(180, APPLE_SCALE, "apple-touch-icon.png");

console.log(`wrote 5 icons to ${path.relative(ROOT, OUT_DIR)}`);
