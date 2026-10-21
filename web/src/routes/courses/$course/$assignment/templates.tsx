import { useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"

import { api } from "@/api/client"
import { upload } from "@/api/errors"
import type { TemplatePage } from "@/api/types"
import { FileDropZone } from "@/components/file-drop-zone"
import { Button, buttonVariants } from "@/components/ui/button"
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
            : "Generate the PDF you'll print for each version from the assignment file, or upload one you made elsewhere. Rubrio suggests a box for each question, which you can adjust on the Outline page."
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
  const generate = api.useMutation(
    "post",
    "/api/courses/{course}/assignments/{slug}/generated-templates/{version}"
  )
  const url = `/api/courses/${params.course}/assignments/${params.assignment}/templates/${encodeURIComponent(version)}`

  function generatePdf() {
    setError(null)
    generate.mutate(
      {
        params: {
          path: { course: params.course, slug: params.assignment, version },
        },
      },
      {
        onSuccess: () => void queryClient.invalidateQueries(),
        onError: setError,
      }
    )
  }

  async function choose(file: File | undefined) {
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      await upload(url, "file", [file])
      await queryClient.invalidateQueries()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title={single ? "Template" : `Version ${version}`}>
      <div className="flex flex-wrap gap-2">
        {!locked && (
          <Button
            disabled={busy || generate.isPending}
            aria-label={`Generate PDF for version ${version}`}
            onClick={generatePdf}
          >
            {generate.isPending ? "Generating…" : "Generate PDF"}
          </Button>
        )}
        {pages.length > 0 && (
          <a
            href={url}
            className={buttonVariants({ variant: "outline" })}
            download
            aria-label={`Download PDF for version ${version}`}
          >
            Download PDF
          </a>
        )}
      </div>
      {!locked && (
        <FileDropZone
          prompt={
            pages.length > 0
              ? `Or drop a PDF you made elsewhere to replace version ${version}'s template`
              : `Or drop a version ${version} PDF you made elsewhere`
          }
          inputLabel={`Template PDF for version ${version}`}
          accept={PDF}
          busy={busy || generate.isPending}
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
