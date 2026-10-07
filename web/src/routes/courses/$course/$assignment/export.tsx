import { createFileRoute } from "@tanstack/react-router"

import { api } from "@/api/client"
import { ErrorText, Loading, PageTitle, Section } from "@/components/page"
import { buttonVariants } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

export const Route = createFileRoute("/courses/$course/$assignment/export")({
  component: ExportPage,
})

function ExportPage() {
  const params = Route.useParams()
  const path = { course: params.course, slug: params.assignment }
  const scores = api.useQuery(
    "get",
    "/api/courses/{course}/assignments/{slug}/scores",
    {
      params: { path },
    }
  )
  if (!scores.data) {
    return (
      <>
        <Loading what="scores" />
        <ErrorText error={scores.error} />
      </>
    )
  }
  const unmatched = scores.data.filter((row) => row.student === null).length
  const ungraded = scores.data.reduce(
    (sum, row) =>
      sum + Object.values(row.scores).filter((score) => score === null).length,
    0
  )
  const base = `/api/courses/${params.course}/assignments/${params.assignment}/export`
  return (
    <div className="flex max-w-3xl flex-col gap-8">
      <PageTitle
        title="Export"
        description="Download a CSV for your gradebook and a feedback PDF for each student."
      />
      <Section title="Before you export">
        <ul className="flex flex-col gap-1 text-sm">
          <li>
            {ungraded === 0
              ? "✓ Every response is graded."
              : `${ungraded} ${ungraded === 1 ? "response is" : "responses are"} ungraded. They export as blank cells.`}
          </li>
          <li>
            {unmatched === 0
              ? "✓ Every submission is matched to a student."
              : `${unmatched} ${unmatched === 1 ? "submission isn't" : "submissions aren't"} matched to a student, so ${unmatched === 1 ? "it's" : "they're"} left out. Match them on the Names page.`}
          </li>
        </ul>
        <div className="flex gap-2">
          <a
            href={`${base}/gradebook.csv`}
            className={buttonVariants()}
            download
          >
            Download gradebook CSV
          </a>
          <a
            href={`${base}/feedback.zip`}
            className={buttonVariants({ variant: "outline" })}
            download
          >
            Download feedback PDFs
          </a>
        </div>
      </Section>
      <Section title="Scores">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Student</TableHead>
              <TableHead>Version</TableHead>
              <TableHead className="text-right">Total</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {scores.data.map((row) => (
              <TableRow key={row.submission}>
                <TableCell>
                  {row.student_name ?? (
                    <span className="text-muted-foreground">Not matched</span>
                  )}
                </TableCell>
                <TableCell>{row.version ?? "—"}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {row.total === null ? "—" : `${row.total} / ${row.possible}`}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Section>
    </div>
  )
}
