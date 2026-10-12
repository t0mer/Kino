import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Kid } from "@/lib/kids";
import { createQueryClient } from "@/lib/query";
import { EnrollPage } from "./EnrollPage";

type Call = { method: string; url: string; body: unknown };
let routes: Record<string, (c: Call) => Response>;
let calls: Call[];
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const noa: Kid = {
  id: 1, display_name: "נועה", slug: "noa", birth_year: 2018, active: true, auto_save_enabled: false,
  threshold_offset: 0, group_ids: [], reference_count: 2, today: 0, last_7_days: 0, created_at: "2026-10-01T10:00:00",
};
const face = (id: string, selectable = true, reason: string | null = null) => ({
  temp_face_id: id, bbox: { x: 100, y: 100, w: 200, h: 200 }, quality: 0.85, tier: selectable ? "L" : "S",
  selectable, reason, crop_url: `/api/temp-crops/${id}`,
});
const upload = (name: string, faces: ReturnType<typeof face>[], photo_date: string | null = null) => ({
  upload_id: `up-${name}`, filename: name, width: 1000, height: 800, photo_date, faces, error: null, status: 200,
});
const cov = (count: number) => ({
  kid_id: 1, count, recommended: 10, yaw_known: count, yaw_min: null, yaw_max: null, left: 0, frontal: count, right: 0,
  dated: count, older_than_year: 0, oldest: null, newest: null, hints: count < 10 ? ["Add side angles"] : [],
});

let coverageCount: number;

beforeEach(() => {
  document.cookie = "kino_csrf=tok; path=/";
  calls = [];
  coverageCount = 2;
  routes = {
    "GET /api/kids/1": () => json(200, noa),
    "GET /api/kids/1/coverage": () => json(200, cov(coverageCount)),
    "GET /api/groups": () => json(200, [{ id: 10, chat_id: "10@g.us", name: "Class", watched: true, kid_ids: [], is_forward_destination: false }]),
    "GET /api/images": () =>
      json(200, {
        items: [{ id: 7, group_id: 10, group_name: "Class", sender: null, sent_at: null, received_at: "2026-10-10T10:00:00", status: "done", face_count: 1, has_file: true }],
        next_cursor: null,
      }),
    "GET /api/images/7/file": () => new Response(new Blob(["x"], { type: "image/jpeg" }), { status: 200 }),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = (init.method ?? "GET").toUpperCase();
      const path = url.split("?")[0]!;
      const body = init.body instanceof FormData ? init.body : init.body ? JSON.parse(String(init.body)) : undefined;
      const call = { method, url, body };
      calls.push(call);
      const h = routes[`${method} ${path}`];
      return h ? h(call) : json(404, { detail: "not found" });
    }),
  );
  let n = 0;
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => `blob:test/${++n}`, revokeObjectURL: () => {} }));
  if (!("ResizeObserver" in window)) {
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  }
});
afterEach(() => vi.unstubAllGlobals());

function renderPage(path = "/kids/1/enroll") {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ThemeProvider>
        <TooltipProvider>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route path="/kids/:id/enroll" element={<EnrollPage />} />
              <Route path="/kids" element={<p>kids list</p>} />
            </Routes>
          </MemoryRouter>
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

const pick = async (...names: string[]) => {
  const input = await screen.findByTestId("file-input");
  await userEvent.upload(input, names.map((n) => new File(["img"], n, { type: "image/jpeg" })));
};
const sent = (method: string, path: string) => calls.filter((c) => c.method === method && c.url.split("?")[0] === path);

describe("EnrollPage", () => {
  it("uploads, selects a face, saves with the EXIF date and refreshes coverage", async () => {
    routes["POST /api/kids/1/uploads"] = () => json(200, [upload("a.jpg", [face("t1")], "2025-06-01T08:00:00")]);
    routes["POST /api/kids/1/references"] = () => {
      coverageCount = 3;
      return json(201, { created: [{ id: 1 }], duplicates: 0 });
    };
    renderPage();
    expect(await screen.findByRole("heading", { name: /Enroll faces for/ })).toBeInTheDocument();
    expect(await screen.findByText("2 / 10 references")).toBeInTheDocument();
    await pick("a.jpg");

    const box = await screen.findByRole("button", { name: /^Face 1, quality 85%/ });
    expect(screen.getByLabelText(/Photo date \(from EXIF\)/)).toHaveValue("2025-06-01");
    await userEvent.click(box);
    expect(box).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("1 face selected")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Save as references/ }));
    await waitFor(() => expect(sent("POST", "/api/kids/1/references")).toHaveLength(1));
    expect(sent("POST", "/api/kids/1/references")[0]!.body).toEqual({
      temp_face_ids: ["t1"], source: "upload", photo_date: "2025-06-01T12:00:00",
    });
    expect(await screen.findByText("Added 1 reference")).toBeInTheDocument();
    expect(await screen.findByText("3 / 10 references")).toBeInTheDocument();
    expect(screen.queryByTestId("photo-a.jpg")).not.toBeInTheDocument();
  });

  it("offers one-click 'Use this face' for a photo with a single selectable face", async () => {
    routes["POST /api/kids/1/uploads"] = () => json(200, [upload("b.jpg", [face("t1"), face("t2", false, "too small")])]);
    renderPage();
    await pick("b.jpg");
    await userEvent.click(await screen.findByRole("button", { name: "Use this face" }));
    expect(screen.getByRole("button", { name: "Selected" })).toBeInTheDocument();
    expect(screen.getByText("1 face selected")).toBeInTheDocument();
  });

  it("cannot select an unselectable face and explains why in a tooltip", async () => {
    routes["POST /api/kids/1/uploads"] = () => json(200, [upload("c.jpg", [face("t9", false, "profile too extreme")])]);
    renderPage();
    await pick("c.jpg");
    const box = await screen.findByRole("button", { name: /cannot be used: profile too extreme/ });
    await userEvent.click(box);
    expect(box).not.toHaveAttribute("aria-pressed");
    expect(screen.getByText("0 faces selected")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Save as references/ })).toBeDisabled();
    await userEvent.unhover(box);
    await userEvent.hover(box);
    expect((await screen.findAllByText("profile too extreme")).length).toBeGreaterThan(0);
  });

  it("shows a clear message for a photo with no faces", async () => {
    routes["POST /api/kids/1/uploads"] = () => json(200, [upload("d.jpg", [])]);
    renderPage();
    await pick("d.jpg");
    expect(await screen.findByText("No faces found in this photo.")).toBeInTheDocument();
  });

  it("keeps good files when one upload fails or is not an image", async () => {
    routes["POST /api/kids/1/uploads"] = (c) => {
      const file = (c.body as FormData).get("files") as File;
      return file.name === "big.jpg"
        ? json(413, { detail: "big.jpg: file is larger than 20 MB" })
        : json(200, [upload(file.name, [face("t1")])]);
    };
    renderPage();
    const input = await screen.findByTestId("file-input");
    await userEvent.upload(input, [
      new File(["x"], "big.jpg", { type: "image/jpeg" }),
      new File(["x"], "ok.jpg", { type: "image/jpeg" }),
      new File(["x"], "notes.pdf", { type: "application/pdf" }),
    ], { applyAccept: false });
    expect(await screen.findByText("big.jpg: file is larger than 20 MB")).toBeInTheDocument();
    expect(await screen.findByText(/Not an image/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /^Face 1/ })).toBeInTheDocument();
    expect(sent("POST", "/api/kids/1/uploads")).toHaveLength(2);
  });

  it("enrolls a received photo by fetching its file and uploading it", async () => {
    routes["POST /api/kids/1/uploads"] = (c) => json(200, [upload(((c.body as FormData).get("files") as File).name, [face("t1")])]);
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Pick from received photos/ }));
    const dlg = await screen.findByRole("dialog");
    await userEvent.click(await within(dlg).findByRole("button", { name: /Photo 7 from Class/ }));
    await userEvent.click(within(dlg).getByRole("button", { name: /Add 1 photo/ }));
    expect(await screen.findByTestId("photo-received-7.jpg")).toBeInTheDocument();
    expect(sent("GET", "/api/images/7/file")).toHaveLength(1);
    expect(sent("POST", "/api/kids/1/uploads")).toHaveLength(1);
  });

  it("shows a not-found state for a missing kid", async () => {
    routes["GET /api/kids/1"] = () => json(404, { detail: "kid 1 not found" });
    renderPage();
    expect(await screen.findByText("Kid not found")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Back to kids/ })).toHaveAttribute("href", "/kids");
  });
});
