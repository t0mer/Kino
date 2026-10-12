import { describe, expect, it } from "vitest";
import { connectBody, connectErrors, generateWebhookSecret, sessionReady } from "./openwaSetup";

const fresh = { env_overrides: [], has_api_key: false, url: null };
const blank = { url: "", apiKey: "", webhookSecret: "" };

describe("connectErrors", () => {
  it("requires the URL and API key on a fresh install", () => {
    expect(connectErrors(blank, fresh)).toEqual({
      url: "Enter the OpenWA URL.",
      apiKey: "Enter the OpenWA API key.",
    });
  });
  it("accepts blanks when the values are already saved", () => {
    expect(connectErrors(blank, { env_overrides: [], has_api_key: true, url: "http://openwa:2785" })).toEqual({});
  });
  it("checks the URL scheme and the webhook secret", () => {
    const e = connectErrors({ url: "ftp://x", apiKey: "k", webhookSecret: "short" }, fresh);
    expect(e.url).toMatch(/http:\/\/ or https:\/\//);
    expect(e.webhookSecret).toMatch(/at least 16/);
    expect(connectErrors({ url: "http://o:1", apiKey: "k", webhookSecret: "has a space in it!!" }, fresh).webhookSecret).toMatch(/no spaces/);
  });
  it("requires the API key again whenever a URL is typed", () => {
    const saved = { env_overrides: [], has_api_key: true, url: "http://openwa:2785" };
    expect(connectErrors({ ...blank, url: "http://other:2785" }, saved)).toEqual({
      apiKey: "Enter the API key for the new URL.",
    });
    expect(connectErrors({ ...blank, url: "http://other:2785", apiKey: "k" }, saved)).toEqual({});
    const envKey = { env_overrides: ["api_key"] as const, has_api_key: true, url: "http://openwa:2785" };
    expect(connectErrors({ ...blank, url: "http://other:2785" }, envKey)).toEqual({});
  });
  it("skips env-locked fields", () => {
    const locked = { env_overrides: ["url", "api_key", "webhook_secret"] as const, has_api_key: false, url: null };
    expect(connectErrors({ ...blank, webhookSecret: "x" }, locked)).toEqual({});
  });
});

describe("connectBody", () => {
  it("trims and drops blank and env-locked fields", () => {
    expect(connectBody({ url: " http://o:1 ", apiKey: " k ", webhookSecret: "" }, [])).toEqual({ url: "http://o:1", api_key: "k" });
    expect(connectBody({ url: "http://o:1", apiKey: "k", webhookSecret: "s".repeat(20) }, ["url", "webhook_secret"])).toEqual({ api_key: "k" });
  });
});

describe("helpers", () => {
  it("generates 64 hex characters, different each time", () => {
    const a = generateWebhookSecret();
    expect(a).toMatch(/^[0-9a-f]{64}$/);
    expect(generateWebhookSecret()).not.toBe(a);
  });
  it("treats only a ready session as ready", () => {
    const s = { id: "x", name: null, phone_masked: null };
    expect(sessionReady({ ...s, status: "READY" })).toBe(true);
    expect(sessionReady({ ...s, status: "qr" })).toBe(false);
  });
});
