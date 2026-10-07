import { useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useState } from "react"

import { api } from "@/api/client"
import type { RosterChange, Student } from "@/api/types"
import { FileDropZone } from "@/components/file-drop-zone"
import { ErrorText, Loading, PageTitle, Section } from "@/components/page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

export const Route = createFileRoute("/courses/$course/")({
  component: CoursePage,
})

function CoursePage() {
  const { course } = Route.useParams()
  const detail = api.useQuery("get", "/api/courses/{course}", {
    params: { path: { course } },
  })
  if (detail.isPending) return <Loading what="the course" />
  if (!detail.data) return <ErrorText error={detail.error} />
  const { roster } = detail.data
  return (
    <div className="flex max-w-3xl flex-col gap-10">
      <PageTitle
        title={detail.data.course.name}
        description={detail.data.course.term}
      />
      <Section
        title="Roster"
        description="Import a CSV with a header row. Rubricate reads the sid (or ID, Student ID, SIS User ID), name (or First Name and Last Name), email and section columns, and ignores the rest. Importing again replaces the roster and keeps students who are already matched to a submission."
      >
        <RosterImport course={course} />
        <RosterTable roster={roster} />
      </Section>
    </div>
  )
}

function RosterImport({ course }: { course: string }) {
  const queryClient = useQueryClient()
  const [csv, setCsv] = useState<string | null>(null)
  const [preview, setPreview] = useState<RosterChange | null>(null)
  const importRoster = api.useMutation("post", "/api/courses/{course}/roster")

  async function choose(file: File | undefined) {
    setPreview(null)
    if (!file) return
    const text = await file.text()
    setCsv(text)
    importRoster.mutate(
      { params: { path: { course } }, body: { csv: text, dry_run: true } },
      { onSuccess: setPreview }
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <FileDropZone
        prompt="Drop the roster CSV here"
        inputLabel="Roster CSV"
        accept={{ extensions: [".csv"], kind: "a CSV file" }}
        busy={importRoster.isPending}
        status="Reading the roster…"
        onFiles={(files) => void choose(files[0])}
      />
      <ErrorText error={importRoster.error} />
      {preview && csv && (
        <div className="flex flex-col gap-2 border p-3 text-xs">
          <p>{describeChange(preview)}</p>
          {preview.dropped.length > 0 && (
            <p className="text-muted-foreground">
              Kept because they're matched to a submission:{" "}
              {preview.dropped.map((student) => student.name).join(", ")}
            </p>
          )}
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={importRoster.isPending}
              onClick={() =>
                importRoster.mutate(
                  {
                    params: { path: { course } },
                    body: { csv, dry_run: false },
                  },
                  {
                    onSuccess: async () => {
                      setPreview(null)
                      setCsv(null)
                      await queryClient.invalidateQueries()
                    },
                  }
                )
              }
            >
              Import roster
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setPreview(null)
                setCsv(null)
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

function describeChange(change: RosterChange): string {
  const parts = [
    `${change.added.length} to add`,
    `${change.changed.length} to update`,
    `${change.removed.length} to remove`,
  ]
  return `Importing this file: ${parts.join(", ")}.`
}

function RosterTable({ roster }: { roster: Student[] }) {
  if (roster.length === 0) {
    return <p className="text-sm text-muted-foreground">No students yet.</p>
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>SID</TableHead>
          <TableHead>Email</TableHead>
          <TableHead>Section</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {roster.map((student) => (
          <TableRow key={student.sid}>
            <TableCell>
              {student.name}{" "}
              {student.dropped && <Badge variant="outline">Dropped</Badge>}
            </TableCell>
            <TableCell className="tabular-nums">{student.sid}</TableCell>
            <TableCell>{student.email}</TableCell>
            <TableCell>{student.section}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
