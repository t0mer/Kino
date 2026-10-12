import { describe, expect, it } from "vitest";
import {
  DEFAULT_THRESHOLDS,
  digestPayload,
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

describe("digestPayload", () => {
  it("trims, drops blanks and nulls empty base url", () => {
    expect(digestPayload({ chat_id: null, cron: " 0 20 * * * ", apprise_urls: ["tgram://****", " "], public_base_url: " " })).toEqual({
      chat_id: null,
      cron: "0 20 * * *",
      apprise_urls: ["tgram://****"],
      public_base_url: null,
    });
  });
});
