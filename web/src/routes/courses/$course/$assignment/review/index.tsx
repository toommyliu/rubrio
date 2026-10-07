import {
  createColumnHelper,
  createSortedRowModel,
  rowSortingFeature,
  sortFn_basic,
  sortFn_text,
  tableFeatures,
  useTable,
} from "@tanstack/react-table"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { ArrowDownIcon, ArrowUpIcon } from "lucide-react"
import { useMemo, useState } from "react"

import { api } from "@/api/client"
import type { QuestionInfo, SubmissionScores } from "@/api/types"
import { ErrorText, Loading, PageTitle } from "@/components/page"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/courses/$course/$assignment/review/")({
  component: ReviewList,
})

const features = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns: { basic: sortFn_basic, text: sortFn_text },
})
const helper = createColumnHelper<typeof features, SubmissionScores>()
const NO_ROWS: SubmissionScores[] = []
const NO_QUESTIONS: QuestionInfo[] = []

function ReviewList() {
  const params = Route.useParams()
  const navigate = useNavigate()
  const path = { course: params.course, slug: params.assignment }
  const info = api.useQuery("get", "/api/courses/{course}/assignments/{slug}", {
    params: { path },
  })
  const scores = api.useQuery(
    "get",
    "/api/courses/{course}/assignments/{slug}/scores",
    { params: { path } }
  )
  const questions = api.useQuery(
    "get",
    "/api/courses/{course}/assignments/{slug}/questions",
    { params: { path } }
  )
  const versions = info.data?.versions ?? []
  const [chosen, setChosen] = useState<string | null>(null)
  const version = chosen ?? versions[0] ?? null
  const [search, setSearch] = useState("")

  const leaves = useMemo(
    () =>
      (questions.data ?? NO_QUESTIONS).filter(
        (q) => q.version === version && q.kind !== "parts"
      ),
    [questions.data, version]
  )
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return (scores.data ?? NO_ROWS).filter(
      (row) =>
        row.version === version &&
        (needle === "" ||
          (row.student_name ?? "").toLowerCase().includes(needle) ||
          (row.student ?? "").toLowerCase().includes(needle))
    )
  }, [scores.data, version, search])
  const columns = useMemo(() => buildColumns(leaves), [leaves])
  const table = useTable({ features, columns, data: rows })

  if (!info.data || !scores.data || !questions.data) {
    return (
      <>
        <Loading what="grades" />
        <ErrorText error={info.error ?? scores.error ?? questions.error} />
      </>
    )
  }
  return (
    <div className="flex flex-col gap-6">
      <PageTitle
        title="Review"
        description="Every submission's grades. Click a row to see that submission's responses and rubric items, and to regrade."
      />
      <div className="flex flex-wrap items-center gap-2">
        {versions.length > 1 &&
          versions.map((name) => (
            <Button
              key={name}
              size="sm"
              variant={name === version ? "default" : "outline"}
              aria-pressed={name === version}
              onClick={() => setChosen(name)}
            >
              Version {name}
            </Button>
          ))}
        <Input
          aria-label="Search students"
          placeholder="Search by name or ID"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="ml-auto max-w-xs"
        />
      </div>
      <Table aria-label="Grades">
        <TableHeader>
          {table.getHeaderGroups().map((group) => (
            <TableRow key={group.id}>
              {group.headers.map((header) => {
                const sorted = header.column.getIsSorted()
                return (
                  <TableHead
                    key={header.id}
                    aria-sort={
                      sorted === "asc"
                        ? "ascending"
                        : sorted === "desc"
                          ? "descending"
                          : undefined
                    }
                    className={cn(
                      header.column.id !== "student" && "text-right"
                    )}
                  >
                    <button
                      type="button"
                      onClick={header.column.getToggleSortingHandler()}
                      className="inline-flex items-center gap-1 hover:text-foreground"
                    >
                      <table.FlexRender header={header} />
                      {sorted === "asc" && <ArrowUpIcon className="size-3" />}
                      {sorted === "desc" && (
                        <ArrowDownIcon className="size-3" />
                      )}
                    </button>
                  </TableHead>
                )
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.map((row) => (
            <TableRow
              key={row.id}
              className="cursor-pointer"
              onClick={() =>
                void navigate({
                  to: "/courses/$course/$assignment/review/$submission",
                  params: {
                    ...params,
                    submission: String(row.original.submission),
                  },
                })
              }
            >
              {row.getAllCells().map((cell) => (
                <TableCell
                  key={cell.id}
                  className={cn(
                    "tabular-nums",
                    cell.column.id !== "student" && "text-right"
                  )}
                >
                  <table.FlexRender cell={cell} />
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {rows.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {search ? "No student matches that search." : "No submissions yet."}
        </p>
      )}
    </div>
  )
}

function buildColumns(leaves: QuestionInfo[]) {
  return helper.columns([
    helper.accessor((row) => row.student_name ?? "", {
      id: "student",
      header: "Student",
      sortFn: "text",
      cell: (context) => <StudentLink row={context.row.original} />,
    }),
    helper.accessor((row) => row.total ?? undefined, {
      id: "total",
      header: "Total",
      sortFn: "basic",
      sortUndefined: "last",
      cell: (context) => {
        const total = context.getValue()
        return total === undefined ? (
          <Ungraded />
        ) : (
          `${total} / ${context.row.original.possible}`
        )
      },
    }),
    ...leaves.map((question) =>
      helper.accessor((row) => row.scores[String(question.id)] ?? undefined, {
        id: `q${question.id}`,
        header: question.number,
        sortFn: "basic",
        sortUndefined: "last",
        cell: (context) => {
          const score = context.getValue()
          return score === undefined ? <Ungraded /> : score
        },
      })
    ),
  ])
}

function StudentLink({ row }: { row: SubmissionScores }) {
  const params = Route.useParams()
  return (
    <Link
      to="/courses/$course/$assignment/review/$submission"
      params={{ ...params, submission: String(row.submission) }}
      onClick={(event) => event.stopPropagation()}
      className="underline-offset-2 hover:underline"
    >
      {row.student_name ?? (
        <span className="text-muted-foreground">Not matched</span>
      )}
    </Link>
  )
}

function Ungraded() {
  return <span className="text-muted-foreground">—</span>
}
