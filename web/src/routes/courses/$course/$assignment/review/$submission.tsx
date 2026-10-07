import { createFileRoute, Link } from "@tanstack/react-router"
import { useState } from "react"

import { api } from "@/api/client"
import type { QuestionReview } from "@/api/types"
import { CodeText } from "@/components/code-text"
import { FullPages } from "@/components/full-pages"
import { ErrorText, Loading } from "@/components/page"
import { Badge } from "@/components/ui/badge"
import { buttonVariants } from "@/components/ui/button"
import { formatPoints, pointsLabel } from "@/lib/points"

export const Route = createFileRoute(
  "/courses/$course/$assignment/review/$submission"
)({
  component: SubmissionReviewPage,
})

function SubmissionReviewPage() {
  const params = Route.useParams()
  const submission = Number(params.submission)
  const [viewing, setViewing] = useState<number | null>(null)
  const review = api.useQuery("get", "/api/submissions/{submission}/review", {
    params: { path: { submission } },
  })
  const scores = api.useQuery(
    "get",
    "/api/courses/{course}/assignments/{slug}/scores",
    { params: { path: { course: params.course, slug: params.assignment } } }
  )
  if (!review.data || !scores.data) {
    return (
      <>
        <Loading what="the submission" />
        <ErrorText error={review.error ?? scores.error} />
      </>
    )
  }
  const order = scores.data.map((row) => row.submission)
  const position = order.indexOf(submission)
  const prev = order[position - 1]
  const next = order[position + 1]
  const data = review.data
  return (
    <div className="flex max-w-4xl flex-col gap-6">
      <header className="flex flex-wrap items-baseline justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-lg font-medium">
            {data.student_name ?? "Student not matched yet"}
          </h1>
          <p className="text-xs text-muted-foreground">
            {data.student && `${data.student} · `}
            {data.version && `Version ${data.version} · `}
            {data.total === null
              ? "Nothing graded yet"
              : `${data.total} / ${data.possible}`}
          </p>
        </div>
        <nav className="flex items-center gap-2" aria-label="Submissions">
          <SiblingLink submission={prev} label="← Previous" />
          <span className="text-xs text-muted-foreground tabular-nums">
            {position + 1} of {order.length}
          </span>
          <SiblingLink submission={next} label="Next →" />
        </nav>
      </header>
      <FullPages
        course={params.course}
        assignment={params.assignment}
        submission={submission}
        viewing={viewing}
        onViewingChange={setViewing}
      />
      <ol className="flex flex-col gap-6">
        {data.questions.map((question) => (
          <QuestionCard
            key={question.question}
            question={question}
            submission={submission}
          />
        ))}
      </ol>
    </div>
  )
}

function SiblingLink({
  submission,
  label,
}: {
  submission: number | undefined
  label: string
}) {
  const params = Route.useParams()
  if (submission === undefined) {
    return (
      <span
        aria-disabled
        className={buttonVariants({
          variant: "outline",
          size: "sm",
          className: "opacity-50",
        })}
      >
        {label}
      </span>
    )
  }
  return (
    <Link
      to="/courses/$course/$assignment/review/$submission"
      params={{ ...params, submission: String(submission) }}
      className={buttonVariants({ variant: "outline", size: "sm" })}
    >
      {label}
    </Link>
  )
}

function QuestionCard({
  question,
  submission,
}: {
  question: QuestionReview
  submission: number
}) {
  const params = Route.useParams()
  if (question.kind === "parts") {
    return (
      <li className="text-sm font-medium">
        {question.number}. <CodeText text={question.prompt} />
      </li>
    )
  }
  const grade = question.grade
  return (
    <li
      className="flex flex-col gap-3 border p-3"
      aria-label={`Question ${question.number}`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-sm font-medium">
          {question.number}. <CodeText text={question.prompt} />
        </h2>
        <div className="flex items-center gap-2 text-sm">
          {grade?.extra_credit && (
            <Badge variant="secondary">Extra credit</Badge>
          )}
          <span className="font-medium tabular-nums">
            {grade
              ? `${grade.score} / ${question.points}`
              : pointsLabel(question)}
          </span>
          {!grade && <Badge variant="outline">Ungraded</Badge>}
          <Link
            to="/courses/$course/$assignment/grade/$question"
            params={{ ...params, question: String(question.question) }}
            search={{ submission }}
            className={buttonVariants({ variant: "outline", size: "xs" })}
          >
            {grade ? "Regrade" : "Grade"}
          </Link>
        </div>
      </div>
      <img
        src={`/api/submissions/${submission}/questions/${question.question}/crop`}
        alt={`Response to question ${question.number}`}
        loading="lazy"
        className="w-full border bg-white"
      />
      {grade && (
        <ul className="flex flex-col gap-1 text-xs">
          {question.applied.map((item) => (
            <li key={item.id} className="flex gap-2">
              <span className="w-10 shrink-0 font-medium tabular-nums">
                {formatPoints(item.points)}
              </span>
              <CodeText text={item.description} />
            </li>
          ))}
          {grade.adjustment !== 0 && (
            <li className="flex gap-2">
              <span className="w-10 shrink-0 font-medium tabular-nums">
                {formatPoints(grade.adjustment)}
              </span>
              Adjustment
            </li>
          )}
          {grade.comment && (
            <li className="mt-1 border-l-2 pl-2 whitespace-pre-wrap">
              {grade.comment}
            </li>
          )}
        </ul>
      )}
    </li>
  )
}
