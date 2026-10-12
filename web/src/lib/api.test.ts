import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, buildUrl, errorDetail, setUnauthorizedHandler } from "./api";

type Call = { url: string; init: RequestInit };

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

let calls: Call[];
let responder: (url: string, init: RequestInit) => Response;

beforeEach(() => {
  calls = [];
  responder = () => jsonResponse(200, { ok: true });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push({ url, init });
      return responder(url, init);
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
});

const header = (c: Call, name: string) => (c.init.headers as Record<string, string>)[name];

describe("CSRF header", () => {
  it("is not sent on GET", async () => {
    document.cookie = "kino_csrf=tok123; path=/";
    await api.get("/api/kids");
    expect(calls).toHaveLength(1);
    expect(header(calls[0]!, "X-CSRF-Token")).toBeUndefined();
    expect(calls[0]!.init.credentials).toBe("same-origin");
  });

  it("is sent on POST/PUT/PATCH/DELETE from the kino_csrf cookie", async () => {
    document.cookie = "kino_csrf=tok%2B123; path=/";
    await api.post("/api/kids", { display_name: "נועה" });
    await api.put("/api/settings/forward", {});
    await api.patch("/api/kids/1", {});
    await api.delete("/api/kids/1");
    expect(calls.map((c) => header(c, "X-CSRF-Token"))).toEqual(Array(4).fill("tok+123"));
    expect(header(calls[0]!, "Content-Type")).toBe("application/json");
    expect(calls[0]!.init.body).toBe(JSON.stringify({ display_name: "נועה" }));
  });

  it("fetches /api/auth/status first when the cookie is missing", async () => {
    responder = (url) => {
      if (url === "/api/auth/status") {
        document.cookie = "kino_csrf=fresh; path=/";
        return jsonResponse(200, { setup_required: false });
      }
      return jsonResponse(200, {});
    };
    await api.post("/api/auth/login", { username: "a", password: "b" });
    expect(calls.map((c) => c.url)).toEqual(["/api/auth/status", "/api/auth/login"]);
    expect(header(calls[1]!, "X-CSRF-Token")).toBe("fresh");
  });

  it("uploads multipart without forcing a JSON content type", async () => {
    document.cookie = "kino_csrf=t; path=/";
    const file = new File(["x"], "a.jpg", { type: "image/jpeg" });
    await api.upload("/api/kids/1/uploads", [file]);
    const body = calls[0]!.init.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect(body.getAll("files")).toHaveLength(1);
    expect(header(calls[0]!, "Content-Type")).toBeUndefined();
    expect(header(calls[0]!, "X-CSRF-Token")).toBe("t");
  });
});

describe("401 handling", () => {
  it("calls the unauthorized handler and throws ApiError", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    responder = () => jsonResponse(401, { detail: "not authenticated" });
    await expect(api.get("/api/kids")).rejects.toMatchObject({ status: 401, detail: "not authenticated" });
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("does not call the handler when skipAuthRedirect is set", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    responder = () => jsonResponse(401, { detail: "invalid username or password" });
    await expect(api.get("/api/auth/me", { skipAuthRedirect: true })).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("does not call the handler for other errors", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    responder = () => jsonResponse(403, { detail: "missing or invalid CSRF token" });
    await expect(api.get("/api/x")).rejects.toMatchObject({ status: 403 });
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});

describe("error mapping", () => {
  it("maps string detail, Retry-After and status", async () => {
    responder = () => jsonResponse(429, { detail: "too many failed logins" }, { "Retry-After": "120" });
    const err = await api.get("/api/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 429, detail: "too many failed logins", retryAfter: 120 });
  });

  it("joins FastAPI validation errors", () => {
    expect(
      errorDetail(
        { detail: [{ loc: ["body", "password"], msg: "String should have at least 8 characters" }] },
        "x",
      ),
    ).toBe("password: String should have at least 8 characters");
  });

  it("falls back to the status text for non-JSON bodies", async () => {
    responder = () => new Response("<html>bad gateway</html>", { status: 502, statusText: "Bad Gateway" });
    await expect(api.get("/api/x")).rejects.toMatchObject({ status: 502, detail: "Bad Gateway" });
  });

  it("maps network failures to status 0", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(api.get("/api/x")).rejects.toMatchObject({ status: 0 });
  });

  it("returns undefined for 204", async () => {
    document.cookie = "kino_csrf=t; path=/";
    responder = () => new Response(null, { status: 204 });
    await expect(api.post("/api/auth/logout")).resolves.toBeUndefined();
  });
});

describe("buildUrl", () => {
  it("skips empty values and encodes the rest", () => {
    expect(buildUrl("/api/photos", { kid_id: 3, group_id: undefined, q: "נועה & co", x: "" })).toBe(
      "/api/photos?kid_id=3&q=%D7%A0%D7%95%D7%A2%D7%94+%26+co",
    );
    expect(buildUrl("/api/x")).toBe("/api/x");
  });

  it("blob() returns the body and a 401 triggers the global handler", async () => {
    responder = () => new Response("PNG", { status: 200, headers: { "Content-Type": "image/png" } });
    const b = await api.blob("/api/images/1/file");
    expect(b).toBeInstanceOf(Blob);
    expect(b.size).toBe(3);
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    responder = () => jsonResponse(401, { detail: "no" });
    await expect(api.blob("/api/images/1/file")).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });
});
