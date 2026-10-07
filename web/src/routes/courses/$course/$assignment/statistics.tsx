import { barX, binX, defineChart, rect } from "@tanstack/charts"
import { Chart } from "@tanstack/charts/react"
import { scaleBand } from "@tanstack/charts/scales/band"
import { scaleLinear } from "@tanstack/charts/scales/linear"
import { tooltip } from "@tanstack/charts/tooltip"
import { createFileRoute } from "@tanstack/react-router"
import { ChevronDownIcon, ChevronRightIcon } from "lucide-react"
import { Fragment, useMemo, useState } from "react"

import { api } from "@/api/client"
import type { QuestionStatistics, Summary } from "@/api/types"
import { CodeText } from "@/components/code-text"
import { ErrorText, Loading, PageTitle, Section } from "@/components/page"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

export const Route = createFileRoute("/courses/$course/$assignment/statistics")(
  {
    component: StatisticsPage,
  }
)

const BINS = 10

function StatisticsPage() {
  const params = Route.useParams()
  const path = { course: params.course, slug: params.assignment }
  const statistics = api.useQuery(
    "get",
    "/api/courses/{course}/assignments/{slug}/statistics",
    { params: { path } }
  )
  const [version, setVersion] = useState<string | null>(null)

  if (!statistics.data) {
    return (
      <>
        <Loading what="statistics" />
        <ErrorText error={statistics.error} />
      </>
    )
  }
  const data = statistics.data
  const scope = data.versions.find((v) => v.version === version)
  const summary = scope?.summary ?? data.summary
  const totals = scope?.totals ?? data.totals
  const questions = data.questions.filter(
    (q) => version === null || q.version === version
  )
  return (
    <div className="flex max-w-5xl flex-col gap-8">
      <PageTitle
        title="Statistics"
        description="Totals count only complete submissions, where every question that isn't bonus is graded."
      />
      {data.versions.length > 1 && (
        <div className="flex gap-1" role="group" aria-label="Versions">
          <Button
            size="sm"
            variant={version === null ? "default" : "outline"}
            aria-pressed={version === null}
            onClick={() => setVersion(null)}
          >
            All versions
          </Button>
          {data.versions.map((v) => (
            <Button
              key={v.version}
              size="sm"
              variant={v.version === version ? "default" : "outline"}
              aria-pressed={v.version === version}
              onClick={() => setVersion(v.version)}
            >
              Version {v.version}
            </Button>
          ))}
        </div>
      )}
      <Tiles summary={summary} />
      <Section title="Distribution of totals">
        {totals.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No submission is fully graded yet.
          </p>
        ) : (
          <Histogram totals={totals} possible={summary.possible} />
        )}
      </Section>
      <Section
        title="Questions"
        description="Expand a question to see how often each rubric item was used."
      >
        <QuestionTable
          questions={questions}
          showVersion={version === null && data.versions.length > 1}
        />
      </Section>
    </div>
  )
}

function Tiles({ summary }: { summary: Summary }) {
  const tiles = [
    { label: "Mean", value: summary.mean },
    { label: "Median", value: summary.median },
    { label: "Standard deviation", value: summary.stdev },
  ]
  return (
    <dl className="grid grid-cols-2 gap-px border bg-border sm:grid-cols-5">
      {tiles.map((tile) => (
        <Tile
          key={tile.label}
          label={tile.label}
          value={tile.value === null ? "—" : String(tile.value)}
          detail={
            tile.label === "Standard deviation"
              ? undefined
              : `out of ${summary.possible}`
          }
        />
      ))}
      <Tile
        label="Range"
        value={summary.low === null ? "—" : `${summary.low}–${summary.high}`}
      />
      <Tile
        label="Complete"
        value={`${summary.complete} of ${summary.submissions}`}
        detail="submissions"
      />
    </dl>
  )
}

function Tile({
  label,
  value,
  detail,
}: {
  label: string
  value: string
  detail?: string
}) {
  return (
    <div className="flex flex-col gap-1 bg-background p-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-xl font-semibold">{value}</dd>
      {detail && <dd className="text-xs text-muted-foreground">{detail}</dd>}
    </div>
  )
}

function Histogram({
  totals,
  possible,
}: {
  totals: number[]
  possible: number
}) {
  const bins = useMemo(() => {
    const top = Math.max(possible, ...totals)
    const step = Math.max(1, Math.ceil(top / BINS))
    const thresholds = Array.from(
      { length: Math.ceil(top / step) + 1 },
      (_, i) => i * step
    )
    return binX(totals, {
      value: (total) => total,
      thresholds,
      outputs: { count: { reduce: "count" } },
    })
  }, [totals, possible])
  const definition = useMemo(
    () =>
      defineChart({
        marks: [
          rect(bins, {
            x1: "x1",
            x2: "x2",
            y1: () => 0,
            y2: "count",
            inset: 1,
            radius: [4, 4, 0, 0],
          }),
        ],
        scales: {
          x: { scale: scaleLinear, axis: { label: "Total" } },
          y: {
            scale: scaleLinear,
            nice: true,
            grid: true,
            axis: {
              label: "Submissions",
              ticks: {
                format: (value: number) =>
                  Number.isInteger(value) ? String(value) : "",
              },
            },
          },
        },
        tooltip,
      }),
    [bins]
  )
  return (
    <div className="flex flex-col gap-2">
      <div className="chart">
        <Chart
          definition={definition}
          height={240}
          ariaLabel="Distribution of submission totals"
        />
      </div>
      <details className="text-xs">
        <summary className="cursor-pointer text-muted-foreground">
          Show as a table
        </summary>
        <Table className="mt-2 max-w-xs">
          <TableHeader>
            <TableRow>
              <TableHead>Total</TableHead>
              <TableHead className="text-right">Submissions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {bins.map((bin) => (
              <TableRow key={bin.x1}>
                <TableCell className="tabular-nums">
                  {bin.x1}–{bin.x2}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {bin.count}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </details>
    </div>
  )
}

function QuestionTable({
  questions,
  showVersion,
}: {
  questions: QuestionStatistics[]
  showVersion: boolean
}) {
  const [open, setOpen] = useState<number | null>(null)
  return (
    <Table aria-label="Question statistics">
      <TableHeader>
        <TableRow>
          <TableHead>Question</TableHead>
          <TableHead className="w-48">Average</TableHead>
          <TableHead className="text-right">Median</TableHead>
          <TableHead className="text-right">Full credit</TableHead>
          <TableHead className="text-right">Zero</TableHead>
          <TableHead className="text-right">Graded</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {questions.map((question) => {
          const expanded = open === question.question
          return (
            <Fragment key={question.question}>
              <TableRow>
                <TableCell>
                  <button
                    type="button"
                    aria-expanded={expanded}
                    onClick={() => setOpen(expanded ? null : question.question)}
                    className="flex items-baseline gap-1 text-left"
                  >
                    {expanded ? (
                      <ChevronDownIcon className="size-3 shrink-0 self-center" />
                    ) : (
                      <ChevronRightIcon className="size-3 shrink-0 self-center" />
                    )}
                    <span className="font-medium">
                      {showVersion && `${question.version}:`}
                      {question.number}
                    </span>
                    <span className="line-clamp-1 text-muted-foreground">
                      <CodeText text={question.prompt} />
                    </span>
                    {question.bonus && (
                      <span className="text-xs text-muted-foreground">
                        (bonus)
                      </span>
                    )}
                  </button>
                </TableCell>
                <TableCell>
                  <Meter value={question.mean} points={question.points} />
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {question.median ?? "—"}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {question.full}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {question.zero}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {question.graded} / {question.total}
                </TableCell>
              </TableRow>
              {expanded && (
                <TableRow>
                  <TableCell colSpan={6} className="bg-muted/30">
                    <ItemUsage question={question} />
                  </TableCell>
                </TableRow>
              )}
            </Fragment>
          )
        })}
      </TableBody>
    </Table>
  )
}

function Meter({ value, points }: { value: number | null; points: number }) {
  if (value === null) return <span className="text-muted-foreground">—</span>
  const share = points > 0 ? Math.min(1, value / points) : 0
  return (
    <div className="flex items-center gap-2">
      <div
        className="h-2 flex-1 bg-series-1/15"
        role="meter"
        aria-valuemin={0}
        aria-valuemax={points}
        aria-valuenow={value}
        aria-label={`Average ${value} of ${points}`}
      >
        <div
          className="h-full bg-series-1"
          style={{ width: `${share * 100}%` }}
        />
      </div>
      <span className="w-16 text-right text-xs tabular-nums">
        {value} / {points}
      </span>
    </div>
  )
}

function ItemUsage({ question }: { question: QuestionStatistics }) {
  const items = question.items
  const definition = useMemo(
    () =>
      defineChart({
        marks: [
          barX(items, {
            x: "count",
            y: "description",
            maxThickness: 18,
            radius: { end: 4 },
          }),
        ],
        scales: {
          x: {
            scale: scaleLinear,
            nice: true,
            grid: true,
            axis: {
              label: "Responses",
              ticks: {
                format: (value: number) =>
                  Number.isInteger(value) ? String(value) : "",
              },
            },
          },
          y: {
            scale: () => scaleBand().padding(0.3),
            axis: { label: "Rubric item" },
          },
        },
        tooltip,
      }),
    [items]
  )
  if (question.graded === 0) {
    return (
      <p className="py-2 text-xs text-muted-foreground">Nothing graded yet.</p>
    )
  }
  return (
    <div className="chart py-2">
      <Chart
        definition={definition}
        height={items.length * 32 + 48}
        ariaLabel={`Rubric item usage for question ${question.number}`}
      />
    </div>
  )
}
