import { QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router";
import { AppLayout } from "@/components/layout/AppLayout";
import { RequireAuth, RequireConfigured, UnauthorizedRedirect } from "@/components/layout/AuthGate";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { NAV } from "@/lib/nav";
import { createQueryClient } from "@/lib/query";
import { ActivityPage } from "@/pages/ActivityPage";
import { ClustersPage } from "@/pages/ClustersPage";
import { EnrollPage } from "@/pages/EnrollPage";
import { GalleryPage } from "@/pages/GalleryPage";
import { GroupsPage } from "@/pages/GroupsPage";
import { KidsPage } from "@/pages/KidsPage";
import { LoginPage } from "@/pages/LoginPage";
import { OpenWASetupPage } from "@/pages/OpenWASetupPage";
import { NotFoundPage, PlaceholderPage } from "@/pages/PlaceholderPage";
import { ReferencesPage } from "@/pages/ReferencesPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { ReviewPage } from "@/pages/ReviewPage";
import { SetupPage } from "@/pages/SetupPage";
import { TestPage } from "@/pages/TestPage";

/** Built pages by nav path; the rest show the "Coming soon" placeholder. */
const PAGES: Record<string, ReactNode> = {
  "/review": <ReviewPage />,
  "/kids": <KidsPage />,
  "/groups": <GroupsPage />,
  "/references": <ReferencesPage />,
  "/settings": <SettingsPage />,
  "/activity": <ActivityPage />,
  "/test": <TestPage />,
  "/gallery": <GalleryPage />,
  "/clusters": <ClustersPage />,
};

/** The old /photos page is now the Gallery; keep bookmarks and the query string working. */
function PhotosRedirect() {
  const { search } = useLocation();
  return <Navigate to={{ pathname: "/gallery", search }} replace />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/setup" element={<SetupPage />} />
      <Route element={<RequireAuth />}>
        <Route path="setup/openwa" element={<OpenWASetupPage />} />
        <Route element={<RequireConfigured />}>
          <Route element={<AppLayout />}>
            <Route index element={<Navigate to="/review" replace />} />
            {NAV.map((item) => (
              <Route key={item.to} path={item.to.slice(1)} element={PAGES[item.to] ?? <PlaceholderPage item={item} />} />
            ))}
            <Route path="photos" element={<PhotosRedirect />} />
            <Route path="kids/:id/enroll" element={<EnrollPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
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
