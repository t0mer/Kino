import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { createQueryClient } from "@/lib/query";
import { UnauthorizedRedirect } from "./AuthGate";

function Where() {
  const l = useLocation();
  return <p data-testid="where">{l.pathname + l.search}</p>;
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.pushState({}, "", "/");
});

describe("UnauthorizedRedirect", () => {
  it("still sends a 401 on the OpenWA wizard to the login page", async () => {
    window.history.pushState({}, "", "/setup/openwa");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 401, headers: { "Content-Type": "application/json" } })),
    );
    render(
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter initialEntries={["/setup/openwa"]}>
          <UnauthorizedRedirect />
          <Where />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await expect(api.get("/api/openwa/setup/status")).rejects.toThrow();
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/login?next=%2Fsetup%2Fopenwa"));
  });
});
