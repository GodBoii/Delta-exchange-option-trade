import { expect, test, vi } from "vitest";

vi.mock("@/lib/api", () => ({ requestJson: vi.fn() }));

const { base64UrlToBytes, parsePushConfig, sameKey } = await import("../lib/push");

test("base64url keys decode without padding", () => {
  expect(Array.from(base64UrlToBytes("AQID_-8"))).toEqual([1, 2, 3, 255, 239]);
  expect(base64UrlToBytes("").byteLength).toBe(0);
});

test("sameKey compares raw key bytes", () => {
  const key = base64UrlToBytes("AQID");
  expect(sameKey(new Uint8Array([1, 2, 3]).buffer, key)).toBe(true);
  expect(sameKey(new Uint8Array([1, 2, 4]).buffer, key)).toBe(false);
  expect(sameKey(new Uint8Array([1, 2]).buffer, key)).toBe(false);
  expect(sameKey(null, key)).toBe(false);
});

test("parsePushConfig accepts the backend shape and rejects others", () => {
  expect(parsePushConfig({ success: true, result: { enabled: true, publicKey: "abc" } })).toEqual({ enabled: true, publicKey: "abc" });
  expect(parsePushConfig({ result: { enabled: false, publicKey: null } })).toEqual({ enabled: false, publicKey: null });
  expect(() => parsePushConfig({ result: { publicKey: "abc" } })).toThrow();
  expect(() => parsePushConfig(null)).toThrow();
});
