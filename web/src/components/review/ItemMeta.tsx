import { Badge } from "@/components/ui/badge";
import { formatDateTime } from "@/lib/format";
import { formatScore, type ReviewItem } from "@/lib/review";

/** Score, tier, method, group and date chips for a review item. */
export function ItemMeta({ item }: { item: ReviewItem }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      <Badge variant="secondary" title="Match score">
        Score {formatScore(item.score)}
      </Badge>
      <Badge variant="outline" title="Face size tier">
        Tier {item.tier}
      </Badge>
      <Badge variant={item.method === "burst" ? "default" : "outline"} title="How it was matched">
        {item.method === "burst" ? "Burst" : "Face"}
      </Badge>
      {item.group_name && (
        <Badge variant="outline" className="max-w-[16rem] truncate" dir="auto">
          {item.group_name}
        </Badge>
      )}
      <span className="text-muted-foreground">{formatDateTime(item.sent_at)}</span>
    </div>
  );
}
