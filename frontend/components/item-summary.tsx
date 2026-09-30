"use client"

import { useQuery } from "@tanstack/react-query"

import { Skeleton } from "@/components/ui/skeleton"
import { api, itemStatuses } from "@/lib/api"
import { statusMeta } from "@/lib/item-status"

export function ItemSummary() {
  const { data, isPending, isError } = useQuery({
    queryKey: ["items"],
    queryFn: () => api.listItems({ limit: 100 }),
  })

  if (isPending) return <Skeleton className="h-11 w-40 rounded-md" />
  if (isError) return <p className="text-sm text-muted-foreground">Unavailable</p>

  return (
    <div className="flex items-baseline gap-8">
      {itemStatuses.map((status) => (
        <div key={status}>
          <p className="font-heading text-[2rem] leading-none font-bold tabular-nums">
            {data.items.filter((item) => item.status === status).length}
          </p>
          <p className="mt-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {statusMeta[status].label}
          </p>
        </div>
      ))}
    </div>
  )
}
