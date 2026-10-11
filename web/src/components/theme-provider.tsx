import * as React from "react";
import {
  applyTheme,
  getStoredTheme,
  resolveTheme,
  storeTheme,
  systemPrefersDark,
  type ThemePref,
  watchSystemTheme,
} from "@/lib/theme";

interface ThemeContextValue {
  pref: ThemePref;
  resolved: "light" | "dark";
  setPref: (pref: ThemePref) => void;
}

const ThemeContext = React.createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [pref, setPrefState] = React.useState<ThemePref>(getStoredTheme);
  const [prefersDark, setPrefersDark] = React.useState(systemPrefersDark);

  React.useEffect(() => watchSystemTheme(() => setPrefersDark(systemPrefersDark())), []);
  React.useEffect(() => applyTheme(pref), [pref, prefersDark]);

  const value = React.useMemo<ThemeContextValue>(
    () => ({
      pref,
      resolved: resolveTheme(pref, prefersDark),
      setPref: (p) => {
        storeTheme(p);
        setPrefState(p);
      },
    }),
    [pref, prefersDark],
  );
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = React.useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside <ThemeProvider>");
  return ctx;
}
