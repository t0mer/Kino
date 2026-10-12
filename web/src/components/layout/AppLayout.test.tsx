import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { createQueryClient } from "@/lib/query";
import { AppLayout } from "./AppLayout";

let total = 0;
beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const body = url.startsWith("/api/review/count") ? { total, by_kid: {} } : { detail: "x" };
      return new Response(JSON.stringify(body), { status: url.startsWith("/api/review/count") ? 200 : 404, headers: { "Content-Type": "application/json" } });
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function renderLayout() {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <ThemeProvider>
        <TooltipProvider>
          <MemoryRouter initialEntries={["/review"]}>
            <Routes>
              <Route element={<AppLayout />}>
                <Route path="review" element={<p>page</p>} />
              </Route>
            </Routes>
          </MemoryRouter>
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

describe("review nav badge", () => {
  it("shows the pending count next to Review", async () => {
    total = 7;
    renderLayout();
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0]!;
    expect(await within(nav).findByLabelText("7 waiting for review")).toHaveTextContent("7");
  });
  it("caps at 99+", async () => {
    total = 250;
    renderLayout();
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0]!;
    expect(await within(nav).findByLabelText("250 waiting for review")).toHaveTextContent("99+");
  });
  it("is hidden at zero", async () => {
    total = 0;
    renderLayout();
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0]!;
    await within(nav).findByText("Review");
    expect(within(nav).queryByLabelText(/waiting for review/)).toBeNull();
  });
});
