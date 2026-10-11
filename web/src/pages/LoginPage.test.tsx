import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { createQueryClient } from "@/lib/query";
import { LoginPage } from "./LoginPage";

type Handler = (init: RequestInit) => Response;
let routes: Record<string, Handler>;
const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });

beforeEach(() => {
  document.cookie = "kino_csrf=tok; path=/";
  routes = {
    "/api/auth/status": () => json(200, { setup_required: false }),
    "/api/auth/me": () => json(401, { detail: "not authenticated" }),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const h = routes[url.split("?")[0]!];
      return h ? h(init) : json(404, { detail: "nope" });
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function renderLogin(initial = "/login?next=%2Fkids") {
  const qc = createQueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <ThemeProvider>
        <TooltipProvider>
          <MemoryRouter initialEntries={[initial]}>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/setup" element={<p>setup page</p>} />
              <Route path="/kids" element={<p>kids page</p>} />
            </Routes>
          </MemoryRouter>
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

describe("LoginPage", () => {
  it("signs in and goes to the requested page", async () => {
    let sent: RequestInit | undefined;
    routes["/api/auth/login"] = (init) => {
      sent = init;
      return json(200, { method: "local", user: "admin" });
    };
    renderLogin();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("kids page")).toBeInTheDocument();
    expect(JSON.parse(String(sent?.body))).toEqual({ username: "admin", password: "correct horse battery" });
    expect((sent?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("tok");
  });

  it("shows an error for a wrong password", async () => {
    routes["/api/auth/login"] = () => json(401, { detail: "invalid username or password" });
    renderLogin();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "nope");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong username or password.");
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("shows the lockout wait on 429 and disables the button", async () => {
    routes["/api/auth/login"] = () =>
      json(429, { detail: "too many failed logins; try again later" }, { "Retry-After": "600" });
    renderLogin();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Username"), "admin");
    await user.type(screen.getByLabelText("Password"), "x");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Too many failed attempts\. Try again in (10m|9m \d+s)/);
    expect(screen.getByRole("button", { name: /Locked/ })).toBeDisabled();
  });

  it("redirects to setup on a fresh install", async () => {
    routes["/api/auth/status"] = () => json(200, { setup_required: true });
    renderLogin();
    expect(await screen.findByText("setup page")).toBeInTheDocument();
  });

  it("skips the form when already authenticated", async () => {
    routes["/api/auth/me"] = () => json(200, { method: "cloudflare", user: "a@example.com" });
    renderLogin();
    await waitFor(() => expect(screen.getByText("kids page")).toBeInTheDocument());
  });
});
