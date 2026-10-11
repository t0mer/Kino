import { QueryClientProvider } from "@tanstack/react-query";
import { ScanFace } from "lucide-react";
import { type ReactNode, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { AppLayout } from "@/components/layout/AppLayout";
import { RequireAuth, UnauthorizedRedirect } from "@/components/layout/AuthGate";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { NAV } from "@/lib/nav";
import { createQueryClient } from "@/lib/query";
import { KidsPage } from "@/pages/KidsPage";
import { LoginPage } from "@/pages/LoginPage";
import { NotFoundPage, PlaceholderPage } from "@/pages/PlaceholderPage";
import { SetupPage } from "@/pages/SetupPage";

/** Built pages by nav path; the rest show the "Coming soon" placeholder. */
const PAGES: Record<string, ReactNode> = {
  "/kids": <KidsPage />,
};

const ENROLL_ITEM = {
  to: "/kids",
  label: "Enroll faces",
  icon: ScanFace,
  blurb: "Pick this kid's face in a few photos to teach Kino.",
};

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/setup" element={<SetupPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppLayout />}>
          <Route index element={<Navigate to="/review" replace />} />
          {NAV.map((item) => (
            <Route key={item.to} path={item.to.slice(1)} element={PAGES[item.to] ?? <PlaceholderPage item={item} />} />
          ))}
          <Route path="kids/:id/enroll" element={<PlaceholderPage item={ENROLL_ITEM} />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
    </Routes>
  );
}

export default function App() {
  const [queryClient] = useState(createQueryClient);
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <TooltipProvider delayDuration={300}>
          <BrowserRouter>
            <UnauthorizedRedirect />
            <AppRoutes />
          </BrowserRouter>
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
