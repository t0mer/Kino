import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { NAV } from "@/lib/nav";
import { createQueryClient } from "@/lib/query";
import { AppRoutes } from "./App";

let setupStatus: unknown = null;
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  setupStatus = null;
  vi.stubGlobal("IntersectionObserver", class { observe() {} disconnect() {} unobserve() {} });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const path = url.split("?")[0];
      if (path === "/api/auth/me") return json(200, { method: "local", user: "admin" });
      if (path === "/api/photos") return json(200, { items: [], next_cursor: null });
      if (path === "/api/photos/counts") return json(200, { total: 0, kids: [] });
      if (path === "/api/review/count") return json(200, { total: 0, by_kid: {} });
      if (path === "/api/kids" || path === "/api/groups") return json(200, []);
      if (path === "/api/openwa/setup/status") return setupStatus ? json(200, setupStatus) : json(404, { detail: "x" });
      return json(404, { detail: "x" });
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function Where() {
  const l = useLocation();
  return <p data-testid="where">{l.pathname + l.search}</p>;
}

function renderAt(path: string) {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <ThemeProvider>
        <TooltipProvider>
          <MemoryRouter initialEntries={[path]}>
            <AppRoutes />
            <Where />
          </MemoryRouter>
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

describe("routes and nav", () => {
  it("names the reference page References and the photo page Gallery", () => {
    const by = Object.fromEntries(NAV.map((n) => [n.label, n.to]));
    expect(by["References"]).toBe("/references");
    expect(by["Gallery"]).toBe("/gallery");
    expect(by["Photos"]).toBeUndefined();
  });

  it("redirects /photos to /gallery, keeping the query string", async () => {
    renderAt("/photos?kid=2");
    expect(await screen.findByRole("heading", { name: "Gallery" })).toBeInTheDocument();
    expect(screen.getByTestId("where")).toHaveTextContent("/gallery?kid=2");
  });

  it("serves the references page at /references", async () => {
    renderAt("/references");
    expect(await screen.findByRole("heading", { name: "References" })).toBeInTheDocument();
  });
});

describe("OpenWA setup gate", () => {
  const base = {
    url: null, session_id: null, session_name: null, has_api_key: false, has_webhook_secret: false, webhook_secret_pending: false,
    webhook_url: "http://kino-api:8080/api/webhook/openwa", env_overrides: [],
  };

  it("sends a signed-in user to the wizard until OpenWA is configured", async () => {
    setupStatus = { ...base, configured: false };
    renderAt("/references");
    expect(await screen.findByRole("heading", { name: "Connect Kino to OpenWA" })).toBeInTheDocument();
    expect(screen.getByTestId("where")).toHaveTextContent("/setup/openwa");
  });

  it("lets the app through once configured", async () => {
    setupStatus = { ...base, configured: true };
    renderAt("/references");
    expect(await screen.findByRole("heading", { name: "References" })).toBeInTheDocument();
  });
});
