import { expect, test } from "vitest";
import { resolveProfileAvatar } from "@/lib/profile-avatar";

const googlePhoto = "https://lh3.googleusercontent.com/a/profile-photo";

test("uses Google's picture metadata when avatar_url is missing or invalid", () => {
  expect(resolveProfileAvatar(undefined, googlePhoto)).toBe(googlePhoto);
  expect(resolveProfileAvatar("invalid", googlePhoto)).toBe(googlePhoto);
});

test("keeps the current Auth photo ahead of a stale backend profile", () => {
  expect(resolveProfileAvatar(googlePhoto, "https://example.com/old-photo.jpg")).toBe(googlePhoto);
  expect(resolveProfileAvatar(null, googlePhoto)).toBe(googlePhoto);
});

test("missing or unsafe photos leave initials available", () => {
  for (const value of [null, undefined, "", "  ", 42, {}, "javascript:alert(1)", "data:image/png;base64,AA", "http://example.com/photo", "https://user:password@example.com/photo"]) {
    expect(resolveProfileAvatar(value)).toBeNull();
  }
});
