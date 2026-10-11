import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "@/components/theme-provider";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { nextTheme } from "@/lib/theme";

const LABEL = { system: "System theme", light: "Light theme", dark: "Dark theme" } as const;

export function ThemeToggle() {
  const { pref, setPref } = useTheme();
  const Icon = pref === "system" ? Monitor : pref === "light" ? Sun : Moon;
  const label = `${LABEL[pref]} (switch to ${LABEL[nextTheme(pref)].toLowerCase()})`;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={label} onClick={() => setPref(nextTheme(pref))}>
          <Icon />
        </Button>
      </TooltipTrigger>
      <TooltipContent>{LABEL[pref]}</TooltipContent>
    </Tooltip>
  );
}
