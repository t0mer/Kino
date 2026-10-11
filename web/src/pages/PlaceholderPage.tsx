import { Construction } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import type { NavItem } from "@/lib/nav";
import { usePageTitle } from "@/lib/usePageTitle";

export function PageHeader({ title, description }: { title: string; description?: string }) {
  return (
    <div className="mb-6 space-y-1">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {description && <p className="text-sm text-muted-foreground">{description}</p>}
    </div>
  );
}

export function PlaceholderPage({ item }: { item: NavItem }) {
  usePageTitle(item.label);
  const Icon = item.icon;
  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title={item.label} description={item.blurb} />
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
            <Icon className="h-6 w-6" aria-hidden="true" />
          </div>
          <p className="font-medium">Coming soon</p>
          <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Construction className="h-4 w-4" aria-hidden="true" />
            This page is being built.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

export function NotFoundPage() {
  usePageTitle("Not found");
  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Page not found" description="That address does not exist in Kino." />
    </div>
  );
}
