import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { DigestSection } from "@/components/settings/DigestSection";
import { ForwardingSection } from "@/components/settings/ForwardingSection";
import { RetentionSection } from "@/components/settings/RetentionSection";
import { SystemSection } from "@/components/settings/SystemSection";
import { ThresholdsSection } from "@/components/settings/ThresholdsSection";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { kidKeys, kidsApi } from "@/lib/kids";
import { settingsApi, settingsKeys } from "@/lib/settings";
import { usePageTitle } from "@/lib/usePageTitle";
import { PageHeader } from "./PlaceholderPage";

export function SettingsPage() {
  usePageTitle("Settings");
  const settings = useQuery({ queryKey: settingsKeys.all, queryFn: settingsApi.get, staleTime: Infinity });
  const kids = useQuery({ queryKey: kidKeys.list, queryFn: kidsApi.list });

  let body;
  if (settings.isPending) {
    body = (
      <div className="space-y-4" aria-busy="true" aria-label="Loading settings">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-48 rounded-xl" />
        ))}
      </div>
    );
  } else if (settings.isError) {
    body = (
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
          <p className="font-medium">Could not load settings</p>
          <p className="text-sm text-muted-foreground">{settings.error.message}</p>
          <Button variant="outline" onClick={() => settings.refetch()}>
            <RefreshCw /> Retry
          </Button>
        </CardContent>
      </Card>
    );
  } else {
    const s = settings.data;
    body = (
      <div className="space-y-4">
        <ForwardingSection saved={s.forward} kids={kids.data ?? []} />
        <DigestSection saved={s.digest} />
        <ThresholdsSection saved={s.thresholds} />
        <RetentionSection saved={s.retention} />
        <SystemSection data={s} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Settings" description="Forwarding, digest, thresholds, retention and model info." />
      {body}
    </div>
  );
}
