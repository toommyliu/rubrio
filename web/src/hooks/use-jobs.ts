import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef } from "react"

import { api } from "@/api/client"
import type { Job } from "@/api/types"

export function useJobs(path: { course: string; slug: string }) {
  const queryClient = useQueryClient()
  const jobs = api.useQuery(
    "get",
    "/api/courses/{course}/assignments/{slug}/jobs",
    { params: { path } },
    {
      refetchInterval: (query) =>
        query.state.data?.some(isActive) ? 500 : false,
    }
  )
  const active = jobs.data?.filter(isActive) ?? []
  const wasActive = useRef(false)
  useEffect(() => {
    if (wasActive.current && active.length === 0) {
      void queryClient.invalidateQueries()
    }
    wasActive.current = active.length > 0
  }, [active.length, queryClient])
  return { active, latest: jobs.data?.[0] }
}

function isActive(job: Job): boolean {
  return job.state === "queued" || job.state === "running"
}
