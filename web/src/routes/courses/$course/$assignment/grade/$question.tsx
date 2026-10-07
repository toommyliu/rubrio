import { useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { useHotkeys } from "@tanstack/react-hotkeys"
import { useEffect, useRef, useState } from "react"
import type { RefObject } from "react"

import { api, fetchClient } from "@/api/client"
import { isErrorBody } from "@/api/errors"
import type { Grade, QuestionInfo, ResponseInfo, RubricItem } from "@/api/types"
import { CodeText } from "@/components/code-text"
import { FullPages } from "@/components/full-pages"
import { ErrorText, Loading } from "@/components/page"
import { Badge } from "@/components/ui/badge"
import { Button, buttonVariants } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { confirmScoreChange, useConfirm } from "@/hooks/use-confirm"
import { cn } from "@/lib/utils"
import { formatPoints, pointsLabel } from "@/lib/points"

type Search = { submission?: number }

export const Route = createFileRoute(
  "/courses/$course/$assignment/grade/$question"
)({
  validateSearch: (search: Record<string, unknown>): Search => {
    const submission = Number(search.submission)
    return Number.isInteger(submission) && submission > 0 ? { submission } : {}
  },
  component: GradingPage,
})

const NUMBER_KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"] as const

type Draft = { applied: number[]; comment: string }
type Change = Draft & { score: number | "keep" | "clear" }

function GradingPage() {
  const params = Route.useParams()
  const search = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const [reloads, setReloads] = useState(0)
  const path = { course: params.course, slug: params.assignment }
  const questionId = Number(params.question)
  const questions = api.useQuery(
    "get",
    "/api/courses/{course}/assignments/{slug}/questions",
    { params: { path } }
  )
  const responses = api.useQuery("get", "/api/questions/{question}/responses", {
    params: { path: { question: questionId } },
  })
  const question = questions.data?.find((q) => q.id === questionId)
  useEffect(() => {
    if (search.submission !== undefined || !responses.data) return
    const first =
      responses.data.find((r) => r.grade === null) ?? responses.data[0]
    if (first) {
      void navigate({ search: { submission: first.submission }, replace: true })
    }
  }, [search.submission, responses.data, navigate])
  if (!questions.data || !responses.data) {
    return (
      <>
        <Loading what="responses" />
        <ErrorText error={questions.error ?? responses.error} />
      </>
    )
  }
  if (!question)
    return (
      <ErrorText
        error={{ kind: "not_found", message: "This question doesn't exist." }}
      />
    )
  if (responses.data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No submissions have this question yet. Upload scans first.
      </p>
    )
  }
  const list = responses.data
  const index = Math.max(
    0,
    search.submission !== undefined
      ? list.findIndex((r) => r.submission === search.submission)
      : list.findIndex((r) => r.grade === null)
  )
  const response = list[index] ?? list[0]
  if (!response) return null

  function go(submission: number) {
    void navigate({ search: { submission }, replace: true })
  }

  const siblings = questions.data.filter(
    (q) => q.version === question.version && q.kind !== "parts"
  )
  const position = siblings.findIndex((q) => q.id === question.id)
  const nextQuestion = siblings[position + 1]

  return (
    <div className="flex flex-1 flex-col gap-4">
      <header className="flex flex-wrap items-baseline justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-lg font-medium">
            {question.number}. <CodeText text={question.prompt} />
          </h1>
          <p className="text-xs text-muted-foreground">
            {pointsLabel(question)}
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className="tabular-nums">
            {question.graded} of {question.total} graded
          </span>
          {nextQuestion && (
            <Link
              to="/courses/$course/$assignment/grade/$question"
              params={{ ...params, question: String(nextQuestion.id) }}
              search={{}}
              className={buttonVariants({ variant: "outline", size: "sm" })}
            >
              Next question
            </Link>
          )}
        </div>
      </header>
      <Grader
        key={`${question.id}:${response.submission}:${reloads}`}
        question={question}
        response={response}
        index={index}
        responses={list}
        onGo={go}
        onStale={() => setReloads((count) => count + 1)}
      />
    </div>
  )
}

function Grader({
  question,
  response,
  index,
  responses,
  onGo,
  onStale,
}: {
  question: QuestionInfo
  response: ResponseInfo
  index: number
  responses: ResponseInfo[]
  onGo: (submission: number) => void
  onStale: () => void
}) {
  const params = Route.useParams()
  const queryClient = useQueryClient()
  const [grade, setGrade] = useState<Grade | null>(response.grade)
  const [draft, setDraft] = useState<Draft>({
    applied: response.grade?.applied ?? [],
    comment: response.grade?.comment ?? "",
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [viewing, setViewing] = useState<number | null>(null)
  const { open: confirming } = useConfirm()
  const scoreInput = useRef<HTMLInputElement>(null)
  const revision = useRef(response.grade?.revision ?? 0)
  const adjustment = useRef(response.grade?.adjustment ?? 0)
  const queue = useRef<Change[]>([])
  const running = useRef(false)
  const outdated = useRef(false)

  async function flush() {
    if (running.current) return
    running.current = true
    setSaving(true)
    let stale = false
    try {
      for (;;) {
        const change = outdated.current ? undefined : queue.current.shift()
        if (!change) {
          const reconciling = outdated.current
          outdated.current = false
          await queryClient.invalidateQueries()
          if (reconciling && !stale) reconcile()
          if (stale || (!outdated.current && queue.current.length === 0)) break
          continue
        }
        const rubric = currentRubric()
        const result = await fetchClient.PUT(
          "/api/submissions/{submission}/questions/{question}/grade",
          {
            params: {
              path: { submission: response.submission, question: question.id },
            },
            body: {
              applied: change.applied.filter((id) => rubric.has(id)),
              comment: change.comment,
              adjustment: change.score === "clear" ? 0 : adjustment.current,
              score: typeof change.score === "number" ? change.score : null,
              revision: revision.current,
            },
          }
        )
        if (result.error) {
          queue.current = []
          setError(result.error)
          stale = isErrorBody(result.error) && result.error.kind === "stale"
          outdated.current = true
          continue
        }
        revision.current = result.data.revision
        adjustment.current = result.data.adjustment
        setGrade(result.data)
        setError(null)
      }
    } finally {
      running.current = false
      setSaving(false)
    }
    if (stale) onStale()
  }

  function save(next: Draft, score: Change["score"] = "keep") {
    setDraft(next)
    queue.current.push({ ...next, score })
    void flush()
  }

  function currentRubric() {
    return new Set(
      (
        queryClient
          .getQueryData<QuestionInfo[]>(
            api.queryOptions(
              "get",
              "/api/courses/{course}/assignments/{slug}/questions",
              {
                params: {
                  path: { course: params.course, slug: params.assignment },
                },
              }
            ).queryKey
          )
          ?.find((q) => q.id === question.id) ?? question
      ).rubric.map((item) => item.id)
    )
  }

  function reconcile() {
    const { queryKey } = api.queryOptions(
      "get",
      "/api/questions/{question}/responses",
      { params: { path: { question: question.id } } }
    )
    const saved = queryClient
      .getQueryData<ResponseInfo[]>(queryKey)
      ?.find((r) => r.submission === response.submission)?.grade
    const newer = saved && saved.revision > revision.current ? saved : null
    const rubric = currentRubric()
    queue.current = queue.current.map((change) => ({
      ...change,
      applied: change.applied.filter((id) => rubric.has(id)),
    }))
    if (newer) {
      revision.current = newer.revision
      adjustment.current = newer.adjustment
      setGrade(newer)
    }
    setDraft((current) => ({
      ...current,
      applied:
        newer && queue.current.length === 0
          ? newer.applied
          : current.applied.filter((id) => rubric.has(id)),
    }))
  }

  function rubricChanged() {
    outdated.current = true
    return flush()
  }

  function toggle(item: RubricItem) {
    const whole = new Set(
      question.rubric.filter((i) => i.whole_answer).map((i) => i.id)
    )
    const applied = draft.applied.includes(item.id)
      ? draft.applied.filter((id) => id !== item.id)
      : item.whole_answer
        ? [item.id]
        : [...draft.applied.filter((id) => !whole.has(id)), item.id]
    save({ ...draft, applied })
  }

  const prev = responses[index - 1]
  const next = responses[index + 1]
  const nextUngraded = [
    ...responses.slice(index + 1),
    ...responses.slice(0, index),
  ].find((r) => r.grade === null)
  const items = question.rubric

  const keysOn = viewing === null && !confirming
  useHotkeys(
    [
      ...NUMBER_KEYS.flatMap((hotkey, position) => {
        const item = items[position]
        return item ? [{ hotkey, callback: () => toggle(item) }] : []
      }),
      { hotkey: "ArrowLeft", callback: () => prev && onGo(prev.submission) },
      { hotkey: "ArrowRight", callback: () => next && onGo(next.submission) },
      {
        hotkey: "N",
        callback: () => nextUngraded && onGo(nextUngraded.submission),
      },
      { hotkey: "S", callback: () => scoreInput.current?.select() },
    ],
    { enabled: keysOn, conflictBehavior: "allow" }
  )

  return (
    <div className="flex flex-1 gap-6">
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex items-center gap-2 text-xs">
          <Button
            size="sm"
            variant="outline"
            disabled={!prev}
            onClick={() => prev && onGo(prev.submission)}
          >
            ← Previous
          </Button>
          <span className="tabular-nums">
            Response {index + 1} of {responses.length}
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={!next}
            onClick={() => next && onGo(next.submission)}
          >
            Next →
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={!nextUngraded}
            onClick={() => nextUngraded && onGo(nextUngraded.submission)}
          >
            Next ungraded (N)
          </Button>
          <span className="ml-auto text-muted-foreground">
            {response.student_name ?? "Student not matched yet"}
          </span>
        </div>
        <img
          key={response.submission}
          src={`/api/submissions/${response.submission}/questions/${question.id}/crop`}
          alt={`Response ${index + 1}`}
          className="w-full max-w-4xl border bg-white"
        />
        <FullPages
          course={params.course}
          assignment={params.assignment}
          submission={response.submission}
          viewing={viewing}
          onViewingChange={setViewing}
        />
      </div>
      <aside className="flex w-80 shrink-0 flex-col gap-4">
        <AnswerKey question={question} />
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2 text-sm">
            <label htmlFor="score" className="font-medium">
              Score
            </label>
            <ScoreField
              inputRef={scoreInput}
              score={grade?.score ?? null}
              saving={saving}
              onCommit={(value) => save(draft, value)}
            />
            <span className="text-muted-foreground">/ {question.points}</span>
            {grade?.extra_credit && (
              <Badge variant="secondary">Extra credit</Badge>
            )}
            <span
              className="ml-auto text-xs text-muted-foreground"
              aria-live="polite"
            >
              {saving ? "Saving…" : grade ? "Saved" : "Ungraded"}
            </span>
          </div>
          {grade && grade.adjustment !== 0 && (
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              Includes a {formatPoints(grade.adjustment)} adjustment.
              <Button
                size="xs"
                variant="ghost"
                onClick={() => save(draft, "clear")}
              >
                Remove it
              </Button>
            </p>
          )}
        </div>
        <ErrorText error={error} />
        <ol className="flex flex-col gap-1" aria-label="Rubric">
          {items.map((item, position) => {
            const on = draft.applied.includes(item.id)
            return (
              <li key={item.id}>
                <button
                  type="button"
                  aria-pressed={on}
                  onClick={() => toggle(item)}
                  className={cn(
                    "flex w-full items-start gap-2 border px-2 py-1.5 text-left text-xs hover:bg-muted",
                    on && "border-primary bg-primary/10"
                  )}
                >
                  <kbd className="w-4 shrink-0 text-muted-foreground">
                    {position < 10 ? (position + 1) % 10 : ""}
                  </kbd>
                  <span className="w-10 shrink-0 font-medium tabular-nums">
                    {formatPoints(item.points)}
                  </span>
                  <span className="flex-1">
                    <CodeText text={item.description} />
                  </span>
                </button>
              </li>
            )
          })}
        </ol>
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-medium">Comment for the student</span>
          <Textarea
            value={draft.comment}
            onChange={(event) =>
              setDraft({ ...draft, comment: event.target.value })
            }
            onBlur={() => {
              if (draft.comment !== (grade?.comment ?? "")) save(draft)
            }}
            className="min-h-20"
          />
        </label>
        <p className="text-xs text-muted-foreground">
          Number keys toggle rubric items. S edits the score. ← and → move
          between responses. N jumps to the next ungraded one.
        </p>
        <RubricEditor question={question} onChanged={rubricChanged} />
      </aside>
    </div>
  )
}

function AnswerKey({ question }: { question: QuestionInfo }) {
  return (
    <section
      aria-label="Answer key"
      className="flex flex-col gap-1 border-l-2 border-primary bg-muted/50 px-3 py-2 text-xs"
    >
      <h2 className="font-medium">
        {question.kind === "choice" ? "Correct choice" : "Answer key"}
      </h2>
      {question.key.length === 0 ? (
        <p className="text-muted-foreground">
          The assignment file has no answer key for this question.
        </p>
      ) : (
        <ul className="flex flex-col gap-0.5">
          {question.key.map((answer) => (
            <li key={answer}>
              <CodeText text={answer} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function ScoreField({
  inputRef,
  score,
  saving,
  onCommit,
}: {
  inputRef: RefObject<HTMLInputElement | null>
  score: number | null
  saving: boolean
  onCommit: (score: number) => void
}) {
  const shown = score === null ? "" : String(score)
  const [typed, setTyped] = useState<string | null>(null)
  const canceled = useRef(false)
  function commit() {
    const text = typed?.trim() ?? ""
    const value = Number(text)
    setTyped(null)
    if (
      canceled.current ||
      text === "" ||
      Number.isNaN(value) ||
      (!saving && String(value) === shown)
    ) {
      canceled.current = false
      return
    }
    onCommit(value)
  }
  return (
    <Input
      id="score"
      ref={inputRef}
      inputMode="decimal"
      placeholder="–"
      value={typed ?? shown}
      onChange={(event) => setTyped(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Escape") canceled.current = true
        if (event.key === "Enter" || event.key === "Escape") {
          event.currentTarget.blur()
        }
      }}
      className="h-7 w-16 text-right tabular-nums"
    />
  )
}

function RubricEditor({
  question,
  onChanged,
}: {
  question: QuestionInfo
  onChanged: () => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const { confirm } = useConfirm()
  const refresh = () => void onChanged()
  const add = api.useMutation("post", "/api/questions/{question}/rubric", {
    onSuccess: refresh,
  })
  const update = api.useMutation("put", "/api/rubric-items/{item}", {
    onSuccess: refresh,
  })
  const remove = api.useMutation("delete", "/api/rubric-items/{item}", {
    onSuccess: refresh,
  })
  const [description, setDescription] = useState("")
  const [points, setPoints] = useState("")

  function saveItem(
    item: RubricItem,
    next: { description: string; points: number },
    confirmed = false
  ) {
    update.mutate(
      {
        params: { path: { item: item.id } },
        body: { ...next, confirm: confirmed },
      },
      {
        onError: async (error) => {
          if (await confirmScoreChange(confirm, error)) {
            saveItem(item, next, true)
          }
        },
      }
    )
  }

  function removeItem(item: RubricItem, confirmed = false) {
    remove.mutate(
      { params: { path: { item: item.id }, query: { confirm: confirmed } } },
      {
        onError: async (error) => {
          if (await confirmScoreChange(confirm, error)) removeItem(item, true)
        },
      }
    )
  }

  if (!open) {
    return (
      <div>
        <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
          Edit rubric
        </Button>
      </div>
    )
  }
  return (
    <section
      className="flex flex-col gap-2 border-t pt-3"
      aria-label="Edit rubric"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-medium">Edit rubric</h2>
        <Button size="xs" variant="ghost" onClick={() => setOpen(false)}>
          Done
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Changes apply to every grade. Use negative points to take points off or
        positive points to add them, not both.
      </p>
      {question.rubric.map((item) => (
        <ItemRow
          key={`${item.id}:${item.description}:${item.points}`}
          item={item}
          onSave={(next) => saveItem(item, next)}
          onDelete={() => removeItem(item)}
        />
      ))}
      <form
        className="flex gap-1"
        onSubmit={(event) => {
          event.preventDefault()
          add.mutate(
            {
              params: { path: { question: question.id } },
              body: { description, points: Number(points || "0") },
            },
            {
              onSuccess: () => {
                setDescription("")
                setPoints("")
              },
            }
          )
        }}
      >
        <Input
          aria-label="New item description"
          placeholder="New item"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          required
        />
        <Input
          aria-label="New item points"
          placeholder="-2"
          inputMode="decimal"
          value={points}
          onChange={(event) => setPoints(event.target.value)}
          className="w-16"
        />
        <Button type="submit" size="sm" disabled={add.isPending}>
          Add
        </Button>
      </form>
      <ErrorText error={add.error ?? update.error ?? remove.error} />
    </section>
  )
}

function ItemRow({
  item,
  onSave,
  onDelete,
}: {
  item: RubricItem
  onSave: (next: { description: string; points: number }) => void
  onDelete: () => void
}) {
  const [description, setDescription] = useState(item.description)
  const [points, setPoints] = useState(String(item.points))
  const changed =
    description !== item.description || Number(points) !== item.points
  return (
    <form
      className="flex gap-1"
      onSubmit={(event) => {
        event.preventDefault()
        onSave({ description, points: Number(points || "0") })
      }}
    >
      <Input
        aria-label="Item description"
        value={description}
        onChange={(event) => setDescription(event.target.value)}
      />
      <Input
        aria-label="Item points"
        inputMode="decimal"
        value={points}
        onChange={(event) => setPoints(event.target.value)}
        className="w-16"
      />
      {changed ? (
        <Button type="submit" size="sm">
          Save
        </Button>
      ) : (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onDelete}
          aria-label={`Delete ${item.description}`}
        >
          Delete
        </Button>
      )}
    </form>
  )
}
