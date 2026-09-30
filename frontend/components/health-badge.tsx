"use client"

import { useQuery } from "@tanstack/react-query"

import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { api } from "@/lib/api"

export function HealthBadge() {
  const { data, isPending, isError } = useQuery({
    queryKey: ["readiness"],
    queryFn: () => api.readiness(),
    refetchInterval: 15_000,
  })

  if (isPending) return <Skeleton className="h-6 w-24 rounded-md" />

  if (isError || data?.database !== "ok") {
    return (
      <Badge variant="destructive" className="gap-1.5 rounded-md">
        <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-current" />
        Unavailable
      </Badge>
    )
  }
  return (
    <Badge variant="success" className="gap-1.5">
      <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-current" />
      Connected
    </Badge>
  )
}
