import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { HealthBadge } from "@/components/health-badge";
import { ItemSummary } from "@/components/item-summary";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function DashboardPage() {
  return (
    <div className="grid gap-10">
      <PageHeader
        icon="🍑"
        title="Dashboard"
        description="Placeholder screens. They exist to prove the browser, API and database talk to each other."
      />

      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>API</CardTitle>
            <CardDescription>
              Readiness of the backend and its database
            </CardDescription>
            <CardAction>
              <HealthBadge />
            </CardAction>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Polled every 15 seconds from <code>/api/v1/health/ready</code>.
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Tasks</CardTitle>
            <CardDescription>Cards on the board, by status</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-5">
            <ItemSummary />
            <Button
              asChild
              variant="outline"
              size="lg"
              className="justify-self-start"
            >
              <Link href="/items">
                Open board
                <ArrowRight data-icon="inline-end" />
              </Link>
            </Button>
          </CardContent>
        </Card>
      </div>

      <section className="flex gap-3 rounded-xl bg-tint-blue p-5">
        <span aria-hidden className="mt-0.5 text-xl leading-none">
          💡
        </span>
        <div>
          <h2 className="font-heading text-base font-semibold text-tint-blue-foreground">
            How the pieces fit
          </h2>
          <p className="mt-1.5 max-w-prose text-sm text-tint-blue-foreground/85">
          The browser talks to Next.js, Next.js talks to FastAPI over the
          published port, and FastAPI keeps its rows in Postgres. Every card
            above is a round trip through all three.
          </p>
        </div>
      </section>
    </div>
  );
}
