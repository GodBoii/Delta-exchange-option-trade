export type ThemeChoice = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "trade-cognition-theme";
const SCHEME_QUERY = "(prefers-color-scheme: light)";

export function isThemeChoice(value: unknown): value is ThemeChoice {
  return value === "system" || value === "light" || value === "dark";
}

function preference(value: string | null): { choice: ThemeChoice; savedAt: number } | null {
  if (isThemeChoice(value)) return { choice: value, savedAt: 0 };
  try {
    const parsed: unknown = JSON.parse(value ?? "null");
    if (parsed && typeof parsed === "object" && "choice" in parsed && isThemeChoice(parsed.choice)
      && "savedAt" in parsed && typeof parsed.savedAt === "number" && Number.isFinite(parsed.savedAt)
      && parsed.savedAt >= 0 && parsed.savedAt <= Date.now() + 60_000) return { choice: parsed.choice, savedAt: parsed.savedAt };
  } catch { /* Ignore an invalid stored preference. */ }
  return null;
}

function cookieValue(): string | null {
  const raw = document.cookie.split(";").map(value => value.trim()).find(value => value.startsWith(`${STORAGE_KEY}=`));
  return raw ? decodeURIComponent(raw.slice(STORAGE_KEY.length + 1)) : null;
}

function storedPreference() {
  let local: string | null = null;
  let cookie: string | null = null;
  try { local = window.localStorage.getItem(STORAGE_KEY); } catch { /* Cookies may still work. */ }
  try { cookie = cookieValue(); } catch { /* localStorage may still work. */ }
  const first = preference(local), second = preference(cookie);
  return second && (!first || second.savedAt >= first.savedAt) ? second : first;
}

export function storedThemeChoice(): ThemeChoice {
  return storedPreference()?.choice ?? "dark";
}

/** Each store is atomic; timestamps prevent an older fallback from overriding a new choice. */
export function saveThemeChoice(choice: ThemeChoice): boolean {
  const value = JSON.stringify({ choice, savedAt: Math.max(Date.now(), (storedPreference()?.savedAt ?? 0) + 1) });
  let localSaved = false, cookieSaved = false;
  try {
    window.localStorage.setItem(STORAGE_KEY, value);
    localSaved = window.localStorage.getItem(STORAGE_KEY) === value;
  } catch { /* Try the independent cookie store. */ }
  try {
    document.cookie = `${STORAGE_KEY}=${encodeURIComponent(value)}; Path=/; Max-Age=34560000; SameSite=Lax${window.location.protocol === "https:" ? "; Secure" : ""}`;
    cookieSaved = cookieValue() === value;
  } catch { /* The provider reports a temporary choice if both stores fail. */ }
  return localSaved || cookieSaved;
}

export const THEME_STORAGE_KEY = STORAGE_KEY;
export const THEME_SCHEME_QUERY = SCHEME_QUERY;

// Keep this before-paint parser aligned with preference(); tests run both paths.
export const THEME_BOOT_SCRIPT = `(function(){
var k=${JSON.stringify(STORAGE_KEY)},l=null,c=null,n=Date.now();
function p(v){if(v==="dark"||v==="light"||v==="system")return {choice:v,savedAt:0};try{var x=JSON.parse(v);if(x&&(x.choice==="dark"||x.choice==="light"||x.choice==="system")&&typeof x.savedAt==="number"&&isFinite(x.savedAt)&&x.savedAt>=0&&x.savedAt<=n+60000)return x;}catch(_){}return null;}
try{l=p(localStorage.getItem(k));}catch(_){}
try{var a=document.cookie.split(";");for(var i=0;i<a.length;i++){var s=a[i].trim();if(s.indexOf(k+"=")===0)c=p(decodeURIComponent(s.slice(k.length+1)));}}catch(_){}
var q=c&&(!l||c.savedAt>=l.savedAt)?c.choice:l?l.choice:"dark";
var r=q==="system"?(matchMedia(${JSON.stringify(SCHEME_QUERY)}).matches?"light":"dark"):q;
var e=document.documentElement;e.dataset.theme=r;e.dataset.themeChoice=q;e.style.colorScheme=r;
})();`;
