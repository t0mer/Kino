import { afterEach, describe, expect, it, vi } from "vitest";
import { THEME_KEY, applyTheme, getStoredTheme, nextTheme, resolveTheme, storeTheme } from "./theme";

afterEach(() => vi.restoreAllMocks());

describe("theme", () => {
  it("defaults to system and round-trips through storage", () => {
    expect(getStoredTheme()).toBe("system");
    storeTheme("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(getStoredTheme()).toBe("dark");
    storeTheme("system");
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
  });

  it("ignores garbage values", () => {
    localStorage.setItem(THEME_KEY, "purple");
    expect(getStoredTheme()).toBe("system");
  });

  it("survives storage that throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceeded");
    });
    expect(getStoredTheme()).toBe("system");
    expect(() => storeTheme("dark")).not.toThrow();
  });

  it("resolves system against the OS preference", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("applies the dark class", () => {
    const el = document.createElement("html");
    applyTheme("dark", el);
    expect(el.classList.contains("dark")).toBe(true);
    applyTheme("light", el);
    expect(el.classList.contains("dark")).toBe(false);
  });

  it("cycles system -> light -> dark -> system", () => {
    expect(nextTheme("system")).toBe("light");
    expect(nextTheme("light")).toBe("dark");
    expect(nextTheme("dark")).toBe("system");
  });
});
