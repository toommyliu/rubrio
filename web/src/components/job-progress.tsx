import type { Job } from "@/api/types"
import { Progress } from "@/components/ui/progress"

export function JobProgress({ job }: { job: Job }) {
  const percent = job.total > 0 ? Math.round((job.done / job.total) * 100) : 0
  return (
    <div className="flex max-w-md flex-col gap-1 text-xs" aria-live="polite">
      <span>
        {job.state === "queued"
          ? "Waiting to start…"
          : job.message || "Working…"}
        {job.total > 0 && ` (${job.done} of ${job.total})`}
      </span>
      <Progress value={percent} aria-label="Progress" />
    </div>
  )
}
