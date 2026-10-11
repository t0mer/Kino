import { describe, expect, it } from "vitest";
import { setupPasswordError } from "./SetupPage";

describe("setupPasswordError", () => {
  it("requires 12+ characters and a matching confirmation", () => {
    expect(setupPasswordError("short", "short")).toMatch(/at least 12/);
    expect(setupPasswordError("long enough pass", "long enough pasS")).toMatch(/do not match/);
    expect(setupPasswordError("long enough pass", "long enough pass")).toBeNull();
  });
  it("counts characters, not UTF-16 units", () => {
    expect(setupPasswordError("סיסמהארוכה1", "סיסמהארוכה1")).toMatch(/at least 12/); // 11 chars
  });
});
