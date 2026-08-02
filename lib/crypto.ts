import crypto from "node:crypto";
import { getEncryptionKey } from "@/lib/config";

export type EncryptedValue = { ciphertext: string; iv: string; tag: string };

export function encryptSecret(value: string): EncryptedValue {
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-256-gcm", getEncryptionKey(), iv);
  const ciphertext = Buffer.concat([cipher.update(value, "utf8"), cipher.final()]);
  return {
    ciphertext: ciphertext.toString("base64"),
    iv: iv.toString("base64"),
    tag: cipher.getAuthTag().toString("base64")
  };
}

export function decryptSecret(value: EncryptedValue): string {
  const decipher = crypto.createDecipheriv(
    "aes-256-gcm",
    getEncryptionKey(),
    Buffer.from(value.iv, "base64")
  );
  decipher.setAuthTag(Buffer.from(value.tag, "base64"));
  return Buffer.concat([
    decipher.update(Buffer.from(value.ciphertext, "base64")),
    decipher.final()
  ]).toString("utf8");
}

export function opaqueToken(bytes = 32) {
  return crypto.randomBytes(bytes).toString("base64url");
}

export function tokenHash(token: string) {
  return crypto.createHash("sha256").update(token).digest("hex");
}
