import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"

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
  const data = jobs.data
  const active = data?.filter(isActive) ?? []
  const [run, setRun] = useState({ ids: [] as number[], open: false })
  const added = active
    .map((job) => job.id)
    .filter((id) => !run.ids.includes(id))
  if (added.length > 0) {
    setRun({ ids: run.open ? [...run.ids, ...added] : added, open: true })
  } else if (
    run.open &&
    data &&
    active.length === 0 &&
    run.ids.every((id) => data.some((job) => job.id >= id))
  ) {
    setRun({ ids: run.ids, open: false })
  }
  const wasActive = useRef(false)
  useEffect(() => {
    if (wasActive.current && active.length === 0) {
      void queryClient.invalidateQueries()
    }
    wasActive.current = active.length > 0
  }, [active.length, queryClient])
  const finished =
    (run.ids.length > 0
      ? data?.filter((job) => run.ids.includes(job.id))
      : data?.slice(0, 1)
    )?.filter((job) => !isActive(job)) ?? []
  function track(id: number) {
    setRun((current) =>
      current.ids.includes(id)
        ? current
        : { ids: current.open ? [...current.ids, id] : [id], open: true }
    )
  }
  return { active, finished, track }
}

function isActive(job: Job): boolean {
  return job.state === "queued" || job.state === "running"
}
