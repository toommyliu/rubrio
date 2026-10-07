import { useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"
import { api } from "@/api/client"
import { upload } from "@/api/errors"
import { FileDropZone } from "@/components/file-drop-zone"
import { JobProgress } from "@/components/job-progress"
import { ErrorText, Loading, PageTitle, Section } from "@/components/page"
import { useJobs } from "@/hooks/use-jobs"
import { PDF } from "@/lib/accept"

export const Route = createFileRoute("/courses/$course/$assignment/scans")({
  component: ScansPage,
})

function ScansPage() {
  const params = Route.useParams()
  const overview = api.useQuery(
    "get",
    "/api/courses/{course}/assignments/{slug}/scans",
    { params: { path: { course: params.course, slug: params.assignment } } }
  )
  return (
    <div className="flex flex-col gap-8">
      <PageTitle
        title="Scans"
        description="Upload scanned PDFs to match pages, group submissions and find flags."
      />
      <UploadScans />
      <ErrorText error={overview.error} />
      {!overview.data ? (
        <Loading what="scans" />
      ) : (
        <>
          <Section title="Uploaded scans">
            <ul>
              {overview.data.scans.map((scan) => (
                <li key={scan.id}>
                  {scan.name}: {scan.pages} pages
                </li>
              ))}
            </ul>
          </Section>
          <Section title="Submissions">
            <ul>
              {overview.data.submissions.map((submission, index) => (
                <li key={submission.id} aria-label={`Submission ${index + 1}`}>
                  <h3>
                    Submission {index + 1}, version{" "}
                    {submission.version ?? "unknown"}
                  </h3>
                  <ul>
                    {submission.flags.map((flag) => (
                      <li key={flag.kind}>{flag.kind}</li>
                    ))}
                  </ul>
                  <div className="flex gap-2">
                    {submission.pages.map((page) => (
                      <img
                        key={page.id}
                        src={`/api/scan-pages/${page.id}/image`}
                        alt={`${page.scan_name}, page ${page.page_index + 1}`}
                        className="w-32"
                      />
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          </Section>
        </>
      )}
    </div>
  )
}

function UploadScans() {
  const params = Route.useParams()
  const path = { course: params.course, slug: params.assignment }
  const queryClient = useQueryClient()
  const [error, setError] = useState<unknown>(null)
  const [sending, setSending] = useState(false)
  const { active, finished } = useJobs(path)

  async function send(files: File[]) {
    if (files.length === 0) return
    setError(null)
    setSending(true)
    try {
      await upload(
        `/api/courses/${params.course}/assignments/${params.assignment}/scans`,
        "files",
        files
      )
      await queryClient.invalidateQueries()
    } catch (e) {
      setError(e)
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <FileDropZone
        prompt="Drop scanned PDFs here"
        inputLabel="Scanned PDFs"
        accept={PDF}
        multiple
        busy={sending}
        onFiles={(files) => void send(files)}
      />
      <ErrorText error={error} />
      {active.map((job) => (
        <JobProgress key={job.id} job={job} />
      ))}
      {finished.map((job) =>
        job.state === "failed" ? (
          <p key={job.id} role="alert" className="text-xs text-destructive">
            A run failed: {job.error}
          </p>
        ) : (
          job.message && (
            <p key={job.id} className="text-xs text-muted-foreground">
              {job.message}
            </p>
          )
        )
      )}
    </div>
  )
}
