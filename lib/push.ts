import { requestJson } from "@/lib/api";

/**
 * Phone notifications through Web Push. The service worker (public/sw.js)
 * shows each alert; this module only asks for permission and keeps the
 * browser's subscription registered with the backend (backend/app/push_api.py).
 */

export type PushState =
  | { kind: "unsupported" }
  | { kind: "blocked" }
  | { kind: "off" }
  | { kind: "on" };

type PushConfig = { enabled: boolean; publicKey: string | null };

/** VAPID keys travel as unpadded base64url; PushManager wants the raw bytes. */
export function base64UrlToBytes(value: string): Uint8Array<ArrayBuffer> {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(base64);
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

export function sameKey(current: ArrayBuffer | null, expected: Uint8Array): boolean {
  if (!current || current.byteLength !== expected.byteLength) return false;
  const view = new Uint8Array(current);
  return view.every((byte, index) => byte === expected[index]);
}

export function parsePushConfig(value: unknown): PushConfig {
  if (!value || typeof value !== "object" || !("result" in value)) throw new Error("Unexpected notification settings");
  const result: unknown = value.result;
  if (!result || typeof result !== "object" || !("enabled" in result) || typeof result.enabled !== "boolean") {
    throw new Error("Unexpected notification settings");
  }
  const publicKey = "publicKey" in result && typeof result.publicKey === "string" ? result.publicKey : null;
  return { enabled: result.enabled, publicKey };
}

/**
 * The worker registers in production builds only, so `getRegistration` rather
 * than `ready`: `ready` never settles when nothing is registered.
 */
async function registration(): Promise<ServiceWorkerRegistration | null> {
  if (typeof window === "undefined" || !("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    return null;
  }
  return (await navigator.serviceWorker.getRegistration("/")) ?? null;
}

export async function readPushState(): Promise<PushState> {
  const worker = await registration();
  if (!worker) return { kind: "unsupported" };
  if (Notification.permission === "denied") return { kind: "blocked" };
  const subscription = await worker.pushManager.getSubscription();
  return subscription && Notification.permission === "granted" ? { kind: "on" } : { kind: "off" };
}

async function register(subscription: PushSubscription) {
  await requestJson("/api/push/subscriptions", { method: "POST", body: JSON.stringify(subscription.toJSON()) });
}

/** Asks for permission (from a tap), subscribes this device and registers it. */
export async function enablePush(): Promise<PushState> {
  const worker = await registration();
  if (!worker) return { kind: "unsupported" };
  const permission = await Notification.requestPermission();
  if (permission === "denied") return { kind: "blocked" };
  if (permission !== "granted") return { kind: "off" };

  const config = parsePushConfig(await requestJson<unknown>("/api/push/config"));
  if (!config.enabled || !config.publicKey) throw new Error("Phone notifications are not set up on the server yet.");
  const key = base64UrlToBytes(config.publicKey);

  let subscription = await worker.pushManager.getSubscription();
  if (subscription && !sameKey(subscription.options.applicationServerKey, key)) {
    // The server key changed; the old subscription can no longer be used.
    await subscription.unsubscribe();
    subscription = null;
  }
  subscription ??= await worker.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  await register(subscription);
  return { kind: "on" };
}

export async function disablePush(): Promise<PushState> {
  const worker = await registration();
  if (!worker) return { kind: "unsupported" };
  const subscription = await worker.pushManager.getSubscription();
  if (subscription) {
    await requestJson("/api/push/subscriptions", {
      method: "DELETE",
      body: JSON.stringify({ endpoint: subscription.endpoint }),
    });
    await subscription.unsubscribe();
  }
  return { kind: "off" };
}

/**
 * Re-registers an existing subscription for whoever is signed in now, so a
 * phone that switched accounts gets the new account's alerts.
 */
export async function resyncPush(): Promise<void> {
  const worker = await registration();
  if (!worker || Notification.permission !== "granted") return;
  const subscription = await worker.pushManager.getSubscription();
  if (subscription) await register(subscription);
}

export async function sendTestPush(): Promise<void> {
  await requestJson("/api/push/test", { method: "POST" });
}
