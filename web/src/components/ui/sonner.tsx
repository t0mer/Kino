import { Toaster as Sonner, toast } from "sonner";
import { useTheme } from "@/components/theme-provider";

export function Toaster() {
  const { resolved } = useTheme();
  return (
    <Sonner
      theme={resolved}
      richColors
      closeButton
      toastOptions={{ classNames: { toast: "font-sans" } }}
    />
  );
}

export { toast };
