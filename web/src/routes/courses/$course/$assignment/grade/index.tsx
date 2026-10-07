import { createFileRoute, Link } from "@tanstack/react-router"

import { api } from "@/api/client"
import type { QuestionInfo } from "@/api/types"
import { ErrorText, Loading, PageTitle, Section } from "@/components/page"
import { Progress } from "@/components/ui/progress"
import { pointsLabel } from "@/lib/points"

export const Route = createFileRoute("/courses/$course/$assignment/grade/")({
  component: QuestionList,
})

function QuestionList() {
  const params = Route.useParams()
  const path = { course: params.course, slug: params.assignment }
  const info = api.useQuery("get", "/api/courses/{course}/assignments/{slug}", {
    params: { path },
  })
  const questions = api.useQuery(
    "get",
    "/api/courses/{course}/assignments/{slug}/questions",
    { params: { path } }
  )
  if (!info.data || !questions.data) {
    return (
      <>
        <Loading what="questions" />
        <ErrorText error={info.error ?? questions.error} />
      </>
    )
  }
  const single = info.data.versions.length === 1
  return (
    <div className="flex max-w-3xl flex-col gap-8">
      <PageTitle
        title="Grade"
        description="Grade one question at a time across every submission. Each version's questions are graded separately."
      />
      {info.data.versions.map((version) => (
        <Section
          key={version}
          title={single ? "Questions" : `Version ${version}`}
        >
          <ul className="flex flex-col border-t">
            {questions.data
              .filter((question) => question.version === version)
              .map((question) => (
                <QuestionRow key={question.id} question={question} />
              ))}
          </ul>
        </Section>
      ))}
    </div>
  )
}

function QuestionRow({ question }: { question: QuestionInfo }) {
  const params = Route.useParams()
  const indent = question.parent !== null ? "pl-6" : ""
  if (question.kind === "parts") {
    return (
      <li className={`border-b py-2 text-sm ${indent}`}>
        <span className="font-medium">{question.number}.</span>{" "}
        {question.prompt}{" "}
        <span className="text-xs text-muted-foreground">
          {pointsLabel(question)}
        </span>
      </li>
    )
  }
  const percent =
    question.total > 0 ? (question.graded / question.total) * 100 : 0
  return (
    <li className="border-b">
      <Link
        to="/courses/$course/$assignment/grade/$question"
        params={{ ...params, question: String(question.id) }}
        className={`flex items-center gap-4 py-2 text-sm hover:bg-muted ${indent}`}
      >
        <span className="w-8 shrink-0 font-medium">{question.number}</span>
        <span className="min-w-0 flex-1 truncate">{question.prompt}</span>
        <span className="shrink-0 text-xs text-muted-foreground">
          {pointsLabel(question)}
        </span>
        <span className="flex w-40 shrink-0 items-center gap-2 text-xs tabular-nums">
          <Progress
            value={percent}
            className="flex-1"
            aria-label={`${question.number} graded`}
          />
          {question.graded}/{question.total}
        </span>
      </Link>
    </li>
  )
}
