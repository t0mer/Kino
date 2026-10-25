import { describe, expect, it } from "vitest";
import {
  DEFAULT_THRESHOLDS,
  digestPayload,
  digestToForm,
  forwardDefault,
  forwardPayload,
  forwardPerKid,
  fieldError,
  isDirty,
  isEqual,
  parseFieldErrors,
  previewCaption,
  sampleCaption,
  unknownPlaceholders,
  validateRetention,
  validateThresholds,
} from "./settings";

describe("caption preview", () => {
  const s = sampleCaption([]);
  it("fills known placeholders and keeps unknown ones literal", () => {
    expect(previewCaption("{kids} · {group} · {date} {time} {nope}", s)).toBe("Noa, Itai · Class 3B · 2026-10-12 16:30 {nope}");
    expect(previewCaption("{sender}", s)).toBe("Dana");
  });
  it("uses real kid names when given (max two)", () => {
    expect(previewCaption("{kids}", sampleCaption(["א", "ב", "ג"]))).toBe("א, ב");
  });
  it("lists unknown placeholders once", () => {
    expect(unknownPlaceholders("{kids} {foo} {foo} {bar}")).toEqual(["{foo}", "{bar}"]);
  });
});

describe("dirty detection", () => {
  it("is not dirty without a draft or with an equal draft", () => {
    expect(isDirty(null, { a: 1 })).toBe(false);
    expect(isDirty({ a: 1, b: [1, 2] }, { b: [1, 2], a: 1 })).toBe(false);
  });
  it("is dirty on any difference", () => {
    expect(isDirty({ a: 1, b: [1, 2] }, { a: 1, b: [1, 3] })).toBe(true);
    expect(isEqual({ a: 1 }, { a: 1, b: 2 })).toBe(false);
  });
});

describe("field errors", () => {
  it("maps FastAPI details to fields", () => {
    const e = parseFieldErrors("caption_template: String should have at least 1 character; per_kid.3: bad; oops");
    expect(e.fields.caption_template).toMatch(/at least 1/);
    expect(fieldError(e, "per_kid.3")).toBe("bad");
    expect(fieldError(e, "per_kid")).toBe("bad");
    expect(e.general).toEqual(["oops"]);
  });
  it("treats plain text as general and strips 'Value error'", () => {
    expect(parseFieldErrors("pick a destination chat before enabling forwarding").general).toHaveLength(1);
    expect(parseFieldErrors("L: Value error, auto must be at least review").fields.L).toBe("auto must be at least review");
  });
});

describe("validation", () => {
  it("accepts the defaults", () => {
    expect(validateThresholds(DEFAULT_THRESHOLDS, 0.3)).toEqual({});
  });
  it("rejects auto below review, below floor, and non-numbers", () => {
    const t = structuredClone(DEFAULT_THRESHOLDS);
    t.L.auto = 0.2;
    expect(validateThresholds(t, 0.3)["L.auto"]).toMatch(/at least the review/);
    t.L = { auto: 0.31, review: 0.1 };
    t.M.auto = 0.3;
    t.M.review = 0.2;
    expect(validateThresholds({ ...t, M: { auto: 0.29, review: 0.2 } }, 0.3)["M.auto"]).toMatch(/floor/);
    t.S.review = NaN;
    expect(validateThresholds(t, 0.3)["S.review"]).toBeDefined();
  });
  it("validates retention days", () => {
    expect(validateRetention({ raw_days: 7, face_days: 30 })).toEqual({});
    expect(Object.keys(validateRetention({ raw_days: 0, face_days: 1.5 }))).toEqual(["raw_days", "face_days"]);
  });
});

describe("destination helpers", () => {
  const wa = { channel: "whatsapp", chat_id: "1@g.us" } as const;
  const base = { enabled: true, default_chat_id: null, per_kid: {}, caption_template: "x", include_review_confirmed: true, send_caption: true };
  it("forward helpers prefer new fields and fall back to legacy", () => {
    expect(forwardDefault({ ...base, default_chat_id: "1@g.us" })).toEqual(wa);
    expect(forwardDefault({ ...base, default_chat_id: "1@g.us", default: null })).toBeNull();
    expect(forwardPerKid({ ...base, per_kid: { "2": "1@g.us" } })).toEqual({ "2": wa });
    const tg = { channel: "telegram", chat_id: "-5" } as const;
    expect(forwardPerKid({ ...base, per_kid: { "2": "1@g.us" }, per_kid_destinations: { "3": tg } })).toEqual({ "3": tg });
  });
  it("forwardPayload sends only the new fields", () => {
    const out = forwardPayload({ ...base, default_chat_id: "1@g.us", per_kid: { "2": "1@g.us" } });
    expect(out).toEqual({
      enabled: true, caption_template: "x", include_review_confirmed: true, send_caption: true,
      rate_per_min: 6, default: wa, per_kid_destinations: { "2": wa },
    });
    expect(forwardPayload({ ...base, rate_per_min: 12 }).rate_per_min).toBe(12);
  });
  it("digestToForm falls back to legacy chat_id; null destination wins", () => {
    const d = { chat_id: "1@g.us", cron: "", apprise_urls: [], public_base_url: null };
    expect(digestToForm(d).destination).toEqual(wa);
    expect(digestToForm({ ...d, destination: null }).destination).toBeNull();
  });
  it("fieldError covers per_kid_destinations paths", () => {
    const e = parseFieldErrors("per_kid_destinations.3: bad");
    expect(fieldError(e, "per_kid.3")).toBe("bad");
  });
});

describe("digestPayload", () => {
  it("trims, drops blanks and nulls empty base url", () => {
    expect(digestPayload({ destination: null, cron: " 0 20 * * * ", apprise_urls: ["tgram://****", " "], public_base_url: " " })).toEqual({
      destination: null,
      cron: "0 20 * * *",
      apprise_urls: ["tgram://****"],
      public_base_url: null,
    });
  });
});
