import { useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { useState } from "react"

import { api } from "@/api/client"
import type { NameRow, Student } from "@/api/types"
import { JobProgress } from "@/components/job-progress"
import { useJobs } from "@/hooks/use-jobs"
import { ErrorText, Loading, PageTitle } from "@/components/page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

export const Route = createFileRoute("/courses/$course/$assignment/names")({
  component: NamesPage,
})

function NamesPage() {
  const params = Route.useParams()
  const path = { course: params.course, slug: params.assignment }
  const rows = api.useQuery(
    "get",
    "/api/courses/{course}/assignments/{slug}/names",
    {
      params: { path },
    }
  )
  const reading = useJobs(path).active.filter((job) => job.kind === "names")
  const course = api.useQuery("get", "/api/courses/{course}", {
    params: { path: { course: params.course } },
  })
  if (!rows.data || !course.data) {
    return (
      <>
        <Loading what="names" />
        <ErrorText error={rows.error ?? course.error} />
      </>
    )
  }
  const roster = course.data.roster
  const confirmed = rows.data.filter((row) => row.student !== null).length
  const automatic = rows.data.filter(
    (row) => row.student !== null && row.automatic
  ).length
  const suggested = rows.data.filter(
    (row) => row.student === null && row.suggested
  ).length
  const open = rows.data.length - confirmed - suggested
  const takenBy = new Map<string, number>()
  rows.data.forEach((row, index) => {
    if (row.student) takenBy.set(row.student.sid, index + 1)
  })
  return (
    <div className="flex max-w-4xl flex-col gap-6">
      <PageTitle
        title="Names"
        description="Match each submission to a student. Rubrio reads the name and ID fields. When both agree with one student on the roster, it matches them for you. When only one does, it suggests a student for you to confirm. Check each automatic match, then accept it or change it."
      />
      {roster.length === 0 && (
        <p className="text-sm">
          This course has no roster yet.{" "}
          <Link
            to="/courses/$course"
            params={{ course: params.course }}
            className="underline"
          >
            Import one on the course page
          </Link>
          , and Rubrio will match names again.
        </p>
      )}
      {reading.map((job) => (
        <JobProgress key={job.id} job={job} />
      ))}
      <p className="text-xs text-muted-foreground" aria-live="polite">
        {confirmed} matched ({automatic} automatically), {suggested} suggested,{" "}
        {open} to pick.
      </p>
      <ol className="flex flex-col border-t">
        {rows.data.map((row, index) => (
          <NameMatch
            key={row.submission}
            row={row}
            index={index + 1}
            roster={roster}
            takenBy={takenBy}
          />
        ))}
      </ol>
    </div>
  )
}

function NameMatch({
  row,
  index,
  roster,
  takenBy,
}: {
  row: NameRow
  index: number
  roster: Student[]
  takenBy: Map<string, number>
}) {
  const queryClient = useQueryClient()
  const [choosing, setChoosing] = useState(false)
  const choose = api.useMutation(
    "post",
    "/api/submissions/{submission}/student",
    {
      onSuccess: async () => {
        setChoosing(false)
        await queryClient.invalidateQueries()
      },
    }
  )
  function pick(sid: string | null) {
    choose.mutate({
      params: { path: { submission: row.submission } },
      body: { sid },
    })
  }
  const picking = choosing || (row.student === null && row.suggested === null)
  return (
    <li className="flex gap-4 border-b py-3" aria-label={`Submission ${index}`}>
      <div className="flex w-80 shrink-0 flex-col gap-1">
        <span className="text-xs font-medium">Submission {index}</span>
        <img
          key={`name-${row.crop_key}`}
          src={`/api/submissions/${row.submission}/fields/name/image?crop=${row.crop_key}`}
          alt={
            row.name_read
              ? `Name field, read as ${row.name_read}`
              : "Name field"
          }
          className="max-h-16 w-full border object-contain object-left"
          onError={(event) => {
            event.currentTarget.hidden = true
          }}
        />
        <img
          key={`sid-${row.crop_key}`}
          src={`/api/submissions/${row.submission}/fields/sid/image?crop=${row.crop_key}`}
          alt={
            row.sid_read
              ? `Student ID field, read as ${row.sid_read}`
              : "Student ID field"
          }
          className="max-h-16 w-full border object-contain object-left"
          onError={(event) => {
            event.currentTarget.hidden = true
          }}
        />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2 text-sm">
        {row.student && !choosing && (
          <div className="flex items-center gap-3">
            <span>
              ✓ {row.student.name}{" "}
              <span className="text-xs text-muted-foreground tabular-nums">
                {row.student.sid}
              </span>
            </span>
            {row.automatic && (
              <>
                <Badge variant="outline">Matched automatically</Badge>
                <Button
                  size="sm"
                  disabled={choose.isPending}
                  onClick={() => row.student && pick(row.student.sid)}
                >
                  Accept
                </Button>
              </>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() => setChoosing(true)}
            >
              Change
            </Button>
          </div>
        )}
        {!row.student && row.suggested && !choosing && (
          <div className="flex items-center gap-3">
            <span>
              Suggested: {row.suggested.name}{" "}
              <span className="text-xs text-muted-foreground tabular-nums">
                {row.suggested.sid}
              </span>
            </span>
            <Button
              size="sm"
              disabled={choose.isPending}
              onClick={() => row.suggested && pick(row.suggested.sid)}
            >
              Confirm
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setChoosing(true)}
            >
              Someone else
            </Button>
          </div>
        )}
        {picking && (
          <Picker
            row={row}
            roster={roster}
            takenBy={takenBy}
            busy={choose.isPending}
            onPick={pick}
            onCancel={choosing ? () => setChoosing(false) : null}
          />
        )}
        <ErrorText error={choose.error} />
      </div>
    </li>
  )
}

function Picker({
  row,
  roster,
  takenBy,
  busy,
  onPick,
  onCancel,
}: {
  row: NameRow
  roster: Student[]
  takenBy: Map<string, number>
  busy: boolean
  onPick: (sid: string | null) => void
  onCancel: (() => void) | null
}) {
  const [query, setQuery] = useState("")
  const needle = query.trim().toLowerCase()
  const results = needle
    ? roster
        .filter(
          (student) =>
            student.name.toLowerCase().includes(needle) ||
            student.sid.includes(needle)
        )
        .slice(0, 6)
    : []
  return (
    <div className="flex flex-col gap-2">
      {row.candidates.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">Closest:</span>
          {row.candidates.map((candidate) => (
            <StudentButton
              key={candidate.sid}
              name={candidate.name}
              sid={candidate.sid}
              takenBy={takenBy.get(candidate.sid)}
              current={row.student?.sid === candidate.sid}
              busy={busy}
              onPick={onPick}
            />
          ))}
        </div>
      )}
      <Input
        aria-label="Search the roster"
        placeholder="Search the roster by name or ID"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        className="max-w-xs"
      />
      {results.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {results.map((student) => (
            <StudentButton
              key={student.sid}
              name={student.name}
              sid={student.sid}
              takenBy={takenBy.get(student.sid)}
              current={row.student?.sid === student.sid}
              busy={busy}
              onPick={onPick}
            />
          ))}
        </div>
      )}
      {needle && results.length === 0 && (
        <p className="text-xs text-muted-foreground">
          No student on the roster matches.
        </p>
      )}
      <div className="flex gap-2">
        {row.student && (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => onPick(null)}
          >
            Unmatch
          </Button>
        )}
        {onCancel && (
          <Button size="sm" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
    </div>
  )
}

function StudentButton({
  name,
  sid,
  takenBy,
  current,
  busy,
  onPick,
}: {
  name: string
  sid: string
  takenBy: number | undefined
  current: boolean
  busy: boolean
  onPick: (sid: string) => void
}) {
  const taken = takenBy !== undefined && !current
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={busy || taken || current}
      onClick={() => onPick(sid)}
      title={taken ? `Already matched to submission ${takenBy}` : undefined}
    >
      {name} <span className="text-muted-foreground tabular-nums">{sid}</span>
      {taken && (
        <span className="text-muted-foreground"> · submission {takenBy}</span>
      )}
    </Button>
  )
}
