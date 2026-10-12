import { describe, expect, it } from "vitest";
import {
  boxStyle,
  checkFile,
  defaultPhotoDate,
  groupSelectionByDate,
  pruneSelection,
  scaleBox,
  soleSelectable,
  toggleFace,
  type UploadFace,
} from "./enroll";

const face = (id: string, selectable = true): UploadFace => ({
  temp_face_id: id,
  bbox: { x: 10, y: 20, w: 30, h: 40 },
  quality: 0.8,
  tier: "L",
  selectable,
  reason: selectable ? null : "too small",
  crop_url: `/api/temp-crops/${id}`,
});

describe("box helpers", () => {
  it("scales pixel boxes to the displayed size", () => {
    expect(scaleBox({ x: 100, y: 50, w: 20, h: 10 }, 1000, 500, 500, 250)).toEqual({ x: 50, y: 25, w: 10, h: 5 });
  });
  it("expresses boxes as percentages of the image", () => {
    expect(boxStyle({ x: 100, y: 50, w: 200, h: 100 }, 1000, 500)).toEqual({
      left: "10.000%",
      top: "10.000%",
      width: "20.000%",
      height: "20.000%",
    });
  });
});

describe("selection", () => {
  it("toggles selectable faces and ignores unselectable ones", () => {
    let s = toggleFace(new Set(), face("a"));
    expect([...s]).toEqual(["a"]);
    s = toggleFace(s, face("a"));
    expect(s.size).toBe(0);
    expect(toggleFace(new Set(), face("b", false)).size).toBe(0);
  });
  it("finds the sole selectable face", () => {
    expect(soleSelectable({ faces: [face("a"), face("b", false)] })?.temp_face_id).toBe("a");
    expect(soleSelectable({ faces: [face("a"), face("b")] })).toBeNull();
    expect(soleSelectable({ faces: [] })).toBeNull();
  });
  it("prunes ids of removed photos", () => {
    expect([...pruneSelection(new Set(["a", "gone"]), [{ faces: [face("a")] }])]).toEqual(["a"]);
  });
  it("groups the selection by photo date", () => {
    const results = [
      { upload_id: "u1", faces: [face("a"), face("b")] },
      { upload_id: "u2", faces: [face("c")] },
      { upload_id: "u3", faces: [face("d")] },
    ];
    const groups = groupSelectionByDate(results, new Set(["a", "c", "d"]), { u1: "2026-01-02", u2: "2026-01-02" }, "2026-10-12");
    expect(groups).toEqual([
      { date: "2026-01-02", tempFaceIds: ["a", "c"] },
      { date: "2026-10-12", tempFaceIds: ["d"] },
    ]);
  });
});

describe("dates and files", () => {
  it("uses the EXIF date when present, else today", () => {
    expect(defaultPhotoDate({ photo_date: "2025-06-01T08:30:00" })).toBe("2025-06-01");
    expect(defaultPhotoDate({ photo_date: null }, new Date(2026, 9, 5))).toBe("2026-10-05");
  });
  it("rejects non-images and oversize files", () => {
    expect(checkFile({ name: "a.jpg", type: "image/jpeg", size: 10 })).toBeNull();
    expect(checkFile({ name: "a.heic", type: "", size: 10 })).toBeNull();
    expect(checkFile({ name: "a.pdf", type: "application/pdf", size: 10 })).toMatch(/Not an image/);
    expect(checkFile({ name: "a.jpg", type: "image/jpeg", size: 21 * 1024 * 1024 })).toMatch(/20 MB/);
  });
});
