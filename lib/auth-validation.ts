const E164_PHONE_NUMBER = /^\+[1-9]\d{7,14}$/;

/**
 * Accept familiar visual separators, but store one unambiguous international
 * representation in Supabase.
 */
export function normalizePhoneNumber(value: string): string | null {
  const normalized = value.trim().replace(/[\s().-]/g, "");
  return E164_PHONE_NUMBER.test(normalized) ? normalized : null;
}
