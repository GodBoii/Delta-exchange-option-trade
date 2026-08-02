import { describe, expect, it } from "vitest";
import { decryptSecret, encryptSecret, tokenHash } from "@/lib/crypto";

describe("credential protection", () => {
  it("round-trips AES-GCM encrypted secrets without storing plaintext", () => {
    const secret = "not-a-real-delta-secret";
    const encrypted = encryptSecret(secret);
    expect(encrypted.ciphertext).not.toContain(secret);
    expect(decryptSecret(encrypted)).toBe(secret);
  });

  it("hashes session tokens deterministically", () => {
    expect(tokenHash("session")).toBe(tokenHash("session"));
    expect(tokenHash("session")).not.toBe("session");
  });
});
