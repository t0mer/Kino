import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import {
  bestReference,
  clampOffset,
  coveragePercent,
  createBody,
  emptyForm,
  errorsFromApi,
  formatOffset,
  formFromKid,
  groupLabel,
  initials,
  type Kid,
  type Reference,
  updateBody,
  validateForm,
} from "./kids";

const kid: Kid = {
  id: 1,
  display_name: "Noa",
  slug: "noa",
  birth_year: 2018,
  active: true,
  auto_save_enabled: false,
  threshold_offset: 0,
  group_ids: [2, 1],
  reference_count: 3,
  today: 0,
  last_7_days: 4,
  created_at: "2026-10-01T10:00:00",
};

const ref = (id: number, quality: number | null, crop = true, status = "active"): Reference => ({
  id,
  kid_id: 1,
  source: "upload",
  status,
  quality,
  photo_date: null,
  crop_url: crop ? `/api/references/${id}/crop` : null,
});

describe("initials", () => {
  it("takes first and last word", () => expect(initials("noa levi cohen")).toBe("NC"));
  it("handles one word and Hebrew", () => {
    expect(initials("Itai")).toBe("I");
    expect(initials("נועה")).toBe("נ");
  });
  it("falls back for empty", () => expect(initials("  ")).toBe("?"));
});

describe("bestReference", () => {
  it("picks the highest-quality active reference with a crop", () => {
    expect(bestReference([ref(1, 0.5), ref(2, 0.9, false), ref(3, 0.95, true, "retired"), ref(4, 0.7)])?.id).toBe(4);
  });
  it("treats unknown quality as lowest and returns null when none qualify", () => {
    expect(bestReference([ref(1, null), ref(2, 0.1)])?.id).toBe(2);
    expect(bestReference([ref(1, 0.9, false)])).toBeNull();
  });
});

describe("coveragePercent", () => {
  it("clamps to 0..100", () => {
    expect(coveragePercent(3, 10)).toBe(30);
    expect(coveragePercent(25, 10)).toBe(100);
    expect(coveragePercent(0, 0)).toBe(100);
  });
});

describe("offset helpers", () => {
  it("formats with sign", () => {
    expect(formatOffset(0.05)).toBe("+0.05");
    expect(formatOffset(-0.03)).toBe("−0.03");
    expect(formatOffset(0)).toBe("0.00");
    expect(formatOffset(-0.0001)).toBe("0.00");
  });
  it("clamps to the backend bounds and the slider step", () => {
    expect(clampOffset(1)).toBeCloseTo(0.3);
    expect(clampOffset(-1)).toBeCloseTo(-0.1);
    expect(clampOffset(0.123)).toBeCloseTo(0.12);
    expect(clampOffset(Number.NaN)).toBe(0);
  });
});

describe("validateForm", () => {
  it("requires a name and a sane birth year", () => {
    expect(validateForm(emptyForm()).display_name).toBeTruthy();
    expect(validateForm({ ...emptyForm(), display_name: "Noa", birth_year: "1800" }).birth_year).toBeTruthy();
    expect(validateForm({ ...emptyForm(), display_name: "Noa", birth_year: "20.5" }).birth_year).toBeTruthy();
    expect(validateForm({ ...emptyForm(), display_name: "Noa", birth_year: "2019" })).toEqual({});
    expect(validateForm({ ...emptyForm(), display_name: "Noa", slug: "x".repeat(41) }).slug).toBeTruthy();
  });
});

describe("createBody", () => {
  it("leaves the slug to the backend when empty", () => {
    expect(createBody({ ...emptyForm(), display_name: " נועה ", group_ids: [3, 1] })).toEqual({
      display_name: "נועה",
      slug: null,
      birth_year: null,
      active: true,
      threshold_offset: 0,
      group_ids: [1, 3],
    });
  });
});

describe("updateBody", () => {
  it("is empty when nothing changed (group order ignored)", () => {
    expect(updateBody({ ...formFromKid(kid), group_ids: [1, 2] }, kid)).toEqual({});
  });
  it("sends only changed fields and null for a cleared birth year", () => {
    const f = { ...formFromKid(kid), slug: "noa-l", birth_year: "", threshold_offset: 0.05, group_ids: [1] };
    expect(updateBody(f, kid)).toEqual({ slug: "noa-l", birth_year: null, threshold_offset: 0.05, group_ids: [1] });
  });
  it("does not send an emptied slug", () => {
    expect(updateBody({ ...formFromKid(kid), slug: " " }, kid)).toEqual({});
  });
});

describe("errorsFromApi", () => {
  it("maps conflicts to fields", () => {
    expect(errorsFromApi(new ApiError(409, "slug 'noa' is already used by another kid"))).toEqual({
      slug: "slug 'noa' is already used by another kid",
    });
    expect(errorsFromApi(new ApiError(409, "a kid named 'Noa' already exists")).display_name).toBeTruthy();
    expect(errorsFromApi(new ApiError(422, "birth_year: Input should be less than or equal to 2100")).birth_year).toBeTruthy();
  });
  it("falls back to a form error", () => {
    expect(errorsFromApi(new ApiError(500, "boom"))).toEqual({ form: "boom" });
    expect(errorsFromApi(new ApiError(422, "unknown group id(s): 9"))).toEqual({ form: "unknown group id(s): 9" });
  });
});

it("groupLabel falls back to the chat id", () => {
  expect(groupLabel({ name: null, chat_id: "123@g.us" })).toBe("123@g.us");
  expect(groupLabel({ name: "Class", chat_id: "x" })).toBe("Class");
});
