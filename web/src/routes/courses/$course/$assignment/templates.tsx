import { useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"

import { api } from "@/api/client"
import { upload } from "@/api/errors"
import type { TemplatePage } from "@/api/types"
import { FileDropZone } from "@/components/file-drop-zone"
import { PDF } from "@/lib/accept"
import { ErrorText, Loading, PageTitle, Section } from "@/components/page"

export const Route = createFileRoute("/courses/$course/$assignment/templates")({
  component: Templates,
})

function Templates() {
  const params = Route.useParams()
  const path = { course: params.course, slug: params.assignment }
  const info = api.useQuery("get", "/api/courses/{course}/assignments/{slug}", {
    params: { path },
  })
  const outline = api.useQuery(
    "get",
    "/api/courses/{course}/assignments/{slug}/outline",
    {
      params: { path },
    }
  )
  if (!info.data || !outline.data) {
    return (
      <>
        <Loading what="templates" />
        <ErrorText error={info.error ?? outline.error} />
      </>
    )
  }
  const locked = info.data.has_scans
  return (
    <div className="flex flex-col gap-8">
      <PageTitle
        title="Templates"
        description={
          locked
            ? "Scans are matched against these pages, so they can't change now that scans are uploaded."
            : "Upload the blank PDF you'll print for each version. Rubricate suggests a box for each question from the printed labels, which you can adjust on the Outline page."
        }
      />
      {info.data.versions.map((version) => (
        <VersionTemplate
          key={version}
          version={version}
          single={info.data.versions.length === 1}
          locked={locked}
          pages={outline.data.pages.filter((page) => page.version === version)}
        />
      ))}
    </div>
  )
}

function VersionTemplate({
  version,
  single,
  locked,
  pages,
}: {
  version: string
  single: boolean
  locked: boolean
  pages: TemplatePage[]
}) {
  const params = Route.useParams()
  const queryClient = useQueryClient()
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  async function choose(file: File | undefined) {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      await upload(
        `/api/courses/${params.course}/assignments/${params.assignment}/templates/${version}`,
        "file",
        [file]
      )
      await queryClient.invalidateQueries()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title={single ? "Template" : `Version ${version}`}>
      {!locked && (
        <FileDropZone
          prompt={
            pages.length > 0
              ? `Drop a new PDF here to replace version ${version}'s template`
              : `Drop the version ${version} template PDF here`
          }
          inputLabel={`Template PDF for version ${version}`}
          accept={PDF}
          busy={busy}
          onFiles={(files) => void choose(files[0])}
        />
      )}
      <ErrorText error={error} />
      {pages.length > 0 && (
        <ul className="flex flex-wrap gap-3">
          {pages.map((page) => (
            <li key={page.id} className="flex flex-col gap-1 text-xs">
              <img
                src={`/api/template-pages/${page.id}/image`}
                alt={`Version ${version}, page ${page.page}`}
                className="w-48 border"
                loading="lazy"
              />
              <span className="text-muted-foreground">Page {page.page}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}
