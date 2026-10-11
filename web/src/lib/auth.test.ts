import { describe, expect, it } from "vitest";
import { safeNext } from "./auth";

describe("safeNext", () => {
  it("keeps in-app paths", () => {
    expect(safeNext("/kids?x=1")).toBe("/kids?x=1");
  });
  it("rejects open redirects and auth pages", () => {
    for (const bad of [null, "", "https://evil.example", "//evil.example", "/\\evil.example", "/login", "/setup"]) {
      expect(safeNext(bad)).toBe("/");
    }
  });
});
