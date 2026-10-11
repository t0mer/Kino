/** Light / dark / system theme. `public/theme-init.js` applies the same rule before first paint. */

export type ThemePref = "light" | "dark" | "system";
export const THEME_KEY = "kino.theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

export function isThemePref(v: unknown): v is ThemePref {
  return v === "light" || v === "dark" || v === "system";
}

export function getStoredTheme(): ThemePref {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return isThemePref(v) ? v : "system";
  } catch {
    return "system"; // private mode / blocked storage
  }
}

export function storeTheme(pref: ThemePref): void {
  try {
    if (pref === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, pref);
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
}

export function systemPrefersDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches;
}

export function resolveTheme(pref: ThemePref, prefersDark: boolean): "light" | "dark" {
  return pref === "system" ? (prefersDark ? "dark" : "light") : pref;
}

export function applyTheme(pref: ThemePref, root: HTMLElement = document.documentElement): void {
  const dark = resolveTheme(pref, systemPrefersDark()) === "dark";
  root.classList.toggle("dark", dark);
  root.style.colorScheme = dark ? "dark" : "light";
}

/** Toggle order for the navbar button. */
export function nextTheme(pref: ThemePref): ThemePref {
  return pref === "system" ? "light" : pref === "light" ? "dark" : "system";
}

/** Re-apply when the OS preference changes while following the system. Returns unsubscribe. */
export function watchSystemTheme(onChange: () => void): () => void {
  if (typeof window.matchMedia !== "function") return () => {};
  const mql = window.matchMedia(DARK_QUERY);
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}
