import { useState } from "react";
import type { ReviewItem } from "@/lib/review";

/** The full photo with the face box drawn over it (percent-positioned from the natural size). */
export function FaceBoxImage({ item }: { item: ReviewItem }) {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  if (!item.image_url) {
    return (
      <div className="flex aspect-[4/3] items-center justify-center rounded-lg border border-dashed bg-muted text-sm text-muted-foreground">
        Photo no longer available
      </div>
    );
  }
  const { x, y, w, h } = item.bbox;
  return (
    <div className="relative inline-block max-w-full overflow-hidden rounded-lg border bg-muted leading-none">
      <img
        key={item.image_id}
        src={item.image_url}
        alt="Full photo"
        className="block max-h-[60vh] w-auto max-w-full"
        onLoad={(e) => setSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
      />
      {size && size.w > 0 && size.h > 0 && (
        <div
          data-testid="face-box"
          aria-hidden="true"
          className="pointer-events-none absolute rounded-sm border-2 border-primary shadow-[0_0_0_1px_rgba(255,255,255,0.8)]"
          style={{
            left: `${(x / size.w) * 100}%`,
            top: `${(y / size.h) * 100}%`,
            width: `${(w / size.w) * 100}%`,
            height: `${(h / size.h) * 100}%`,
          }}
        />
      )}
    </div>
  );
}
