import { QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { AppLayout } from "@/components/layout/AppLayout";
import { RequireAuth, UnauthorizedRedirect } from "@/components/layout/AuthGate";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { NAV } from "@/lib/nav";
import { createQueryClient } from "@/lib/query";
import { ActivityPage } from "@/pages/ActivityPage";
import { EnrollPage } from "@/pages/EnrollPage";
import { GalleryPage } from "@/pages/GalleryPage";
import { GroupsPage } from "@/pages/GroupsPage";
import { KidsPage } from "@/pages/KidsPage";
import { LoginPage } from "@/pages/LoginPage";
import { NotFoundPage, PlaceholderPage } from "@/pages/PlaceholderPage";
import { PhotosPage } from "@/pages/PhotosPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { ReviewPage } from "@/pages/ReviewPage";
import { SetupPage } from "@/pages/SetupPage";
import { TestPage } from "@/pages/TestPage";

/** Built pages by nav path; the rest show the "Coming soon" placeholder. */
const PAGES: Record<string, ReactNode> = {
  "/review": <ReviewPage />,
  "/kids": <KidsPage />,
  "/groups": <GroupsPage />,
  "/photos": <PhotosPage />,
  "/settings": <SettingsPage />,
  "/activity": <ActivityPage />,
  "/test": <TestPage />,
  "/gallery": <GalleryPage />,
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
          <Route path="kids/:id/enroll" element={<EnrollPage />} />
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
