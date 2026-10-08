import { useQueryClient } from "@tanstack/react-query"
import { createFileRoute, useNavigate } from "@tanstack/react-router"
import { Trash2 } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import type { ReactNode } from "react"

import { fetchClient } from "@/api/client"
import type { FoundTemplate } from "@/api/types"
import { FileDropZone } from "@/components/file-drop-zone"
import { ErrorText, PageTitle, Section } from "@/components/page"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useConfirm } from "@/hooks/use-confirm"
import { PDF } from "@/lib/accept"

export const Route = createFileRoute("/courses/$course/new/pdf")({
  component: NewFromPdf,
})

type Part = {
  key: string
  prompt: string
  points: string
  label: number | null
}
type Question = Part & { page: number; parts: Part[] }
type Version = {
  file: File
  pages: number
  hasText: boolean
  questions: Question[]
}

let lastKey = 0
function newKey() {
  lastKey += 1
  return `row-${lastKey}`
}

function pointsText(points: number | null) {
  return points === null ? "" : String(points)
}

function parsePoints(text: string): number | null {
  if (text.trim() === "") return null
  const value = Number(text)
  return Number.isFinite(value) && value >= 0 ? value : null
}

function fromFound(file: File, found: FoundTemplate): Version {
  return {
    file,
    pages: found.pages,
    hasText: found.has_text,
    questions: found.questions.map((question) => ({
      key: newKey(),
      prompt: question.prompt,
      points: pointsText(question.points),
      label: question.label,
      page: question.page,
      parts: question.parts.map((part) => ({
        key: newKey(),
        prompt: part.prompt,
        points: pointsText(part.points),
        label: part.label,
      })),
    })),
  }
}

function partLetter(index: number) {
  return String.fromCharCode(97 + index)
}

function NewFromPdf() {
  const { course } = Route.useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { confirm } = useConfirm()
  const pending = useRef<AbortController | null>(null)
  const [versions, setVersions] = useState<Version[] | null>(null)
  const [title, setTitle] = useState("")
  const [edited, setEdited] = useState(false)
  const [finding, setFinding] = useState(false)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => () => pending.current?.abort(), [])

  async function find(files: File[]) {
    const sorted = files.toSorted((a, b) =>
      a.name.localeCompare(b.name, undefined, { numeric: true })
    )
    const controller = new AbortController()
    pending.current = controller
    setError(null)
    setFinding(true)
    try {
      const found = await Promise.all(
        sorted.map(async (file) => {
          const form = new FormData()
          form.append("file", file)
          const { data, error: failure } = await fetchClient.POST(
            "/api/templates/questions",
            {
              body: { file: file.name },
              bodySerializer: () => form,
              signal: controller.signal,
            }
          )
          if (failure !== undefined) throw failure
          return data
        })
      )
      setVersions(sorted.map((file, index) => fromFound(file, found[index])))
      setTitle(found[0].title)
      setEdited(false)
    } catch (e) {
      if (!controller.signal.aborted) setError(e)
    } finally {
      if (pending.current === controller) {
        pending.current = null
        setFinding(false)
      }
    }
  }

  function change(index: number, questions: Question[]) {
    setVersions(
      (current) =>
        current?.map((version, i) =>
          i === index ? { ...version, questions } : version
        ) ?? null
    )
    setEdited(true)
  }

  async function leave() {
    if (
      edited &&
      !(await confirm({
        title: "Discard this assignment?",
        description:
          "Nothing is saved until you create the assignment, so your changes to these questions will be lost.",
        action: "Discard",
      }))
    ) {
      return
    }
    pending.current?.abort()
    await navigate({ to: "/courses/$course", params: { course } })
  }

  async function startOver() {
    if (
      edited &&
      !(await confirm({
        title: "Choose different PDFs?",
        description: "Your changes to these questions will be lost.",
        action: "Choose different PDFs",
      }))
    ) {
      return
    }
    setVersions(null)
    setError(null)
  }

  async function create() {
    if (!versions) return
    setCreating(true)
    setError(null)
    try {
      const plan = {
        title,
        versions: versions.map((version) => ({
          questions: version.questions.map((question) => ({
            prompt: question.prompt,
            points: question.parts.length ? null : parsePoints(question.points),
            label: question.label,
            parts: question.parts.map((part) => ({
              prompt: part.prompt,
              points: parsePoints(part.points),
              label: part.label,
            })),
          })),
        })),
      }
      const form = new FormData()
      for (const version of versions) form.append("files", version.file)
      form.append("assignment", JSON.stringify(plan))
      const { data: info, error: failure } = await fetchClient.POST(
        "/api/courses/{course}/assignments/from-templates",
        {
          params: { path: { course } },
          body: { files: [], assignment: "" },
          bodySerializer: () => form,
        }
      )
      if (failure !== undefined) throw failure
      await queryClient.invalidateQueries()
      await navigate({
        to: "/courses/$course/$assignment/outline",
        params: { course, assignment: info.slug },
      })
    } catch (e) {
      setError(e)
    } finally {
      setCreating(false)
    }
  }

  if (!versions) {
    return (
      <div className="flex max-w-3xl flex-col gap-6">
        <PageTitle
          title="New assignment from a PDF"
          description="Upload the template you'll print. For several versions, upload one PDF per version. They become versions A, B and so on, in file name order."
        />
        <FileDropZone
          prompt="Drop the template PDF here"
          inputLabel="Template PDFs"
          accept={PDF}
          multiple
          busy={finding}
          status="Finding questions…"
          onFiles={(files) => void find(files)}
        />
        <ErrorText error={error} />
        <div>
          <Button variant="outline" onClick={() => void leave()}>
            Cancel
          </Button>
        </div>
      </div>
    )
  }

  const missing = versions.reduce(
    (count, version) =>
      count +
      version.questions
        .flatMap((question) =>
          question.parts.length ? question.parts : [question]
        )
        .filter((row) => parsePoints(row.points) === null).length,
    0
  )
  const empty = versions.findIndex((version) => version.questions.length === 0)
  const single = versions.length === 1
  const blocker =
    title.trim() === ""
      ? "Give the assignment a title."
      : empty >= 0
        ? `${single ? "The template" : `Version ${String.fromCharCode(65 + empty)}`} has no questions. Add one to continue.`
        : missing > 0
          ? `Fill in points for ${missing} ${missing === 1 ? "question" : "questions"} to continue.`
          : null

  return (
    <div className="flex max-w-3xl flex-col gap-8">
      <PageTitle
        title="New assignment from a PDF"
        description="Check the questions Rubrio found. Fix their prompts, fill in missing points, and add or remove questions. Nothing is saved until you create the assignment."
      />
      <fieldset disabled={creating} className="flex min-w-0 flex-col gap-8">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="assignment-title">Title</Label>
          <Input
            id="assignment-title"
            value={title}
            onChange={(event) => {
              setTitle(event.target.value)
              setEdited(true)
            }}
          />
        </div>
        {versions.map((version, index) => (
          <Section
            key={index}
            title={
              single
                ? "Questions"
                : `Version ${String.fromCharCode(65 + index)}`
            }
            description={describe(version)}
          >
            <QuestionsEditor
              name={
                single ? "" : `version ${String.fromCharCode(65 + index)}, `
              }
              questions={version.questions}
              onChange={(questions) => change(index, questions)}
            />
          </Section>
        ))}
      </fieldset>
      <div className="flex flex-col gap-2">
        {blocker && (
          <p className="text-xs text-muted-foreground" aria-live="polite">
            {blocker}
          </p>
        )}
        <ErrorText error={error} />
        <div className="flex gap-2">
          <Button
            disabled={blocker !== null || creating}
            onClick={() => void create()}
          >
            {creating ? "Creating…" : "Create assignment"}
          </Button>
          <Button
            variant="outline"
            disabled={creating}
            onClick={() => void startOver()}
          >
            Choose different PDFs
          </Button>
          <Button
            variant="outline"
            disabled={creating}
            onClick={() => void leave()}
          >
            Cancel
          </Button>
        </div>
      </div>
    </div>
  )
}

function describe(version: Version): string {
  const pages = `${version.pages} ${version.pages === 1 ? "page" : "pages"}`
  if (!version.hasText) {
    return `${version.file.name}, ${pages}. This PDF has no text Rubrio can read, such as a template that was itself scanned, so add the questions yourself. You'll draw their boxes on the Outline page.`
  }
  if (version.questions.length === 0) {
    return `${version.file.name}, ${pages}. Rubrio found no question labels like "1." or "Question 1", so add the questions yourself. You'll draw their boxes on the Outline page.`
  }
  return `${version.file.name}, ${pages}. Questions you add here get no box, so you'll draw theirs on the Outline page.`
}

function QuestionsEditor({
  name,
  questions,
  onChange,
}: {
  name: string
  questions: Question[]
  onChange: (questions: Question[]) => void
}) {
  function update(index: number, change: Partial<Question>) {
    onChange(
      questions.map((question, i) =>
        i === index ? { ...question, ...change } : question
      )
    )
  }

  function updatePart(index: number, partIndex: number, change: Partial<Part>) {
    update(index, {
      parts: questions[index].parts.map((part, i) =>
        i === partIndex ? { ...part, ...change } : part
      ),
    })
  }

  return (
    <div className="flex flex-col gap-2">
      {questions.length > 0 && (
        <ol
          className="flex flex-col border-t"
          aria-label={name ? `Questions, ${name.slice(0, -2)}` : "Questions"}
        >
          {questions.map((question, index) => {
            const number = String(index + 1)
            return (
              <li key={question.key} className="flex flex-col border-b py-1.5">
                <Row
                  number={number}
                  where={name}
                  row={question}
                  page={question.label === null ? null : question.page}
                  sum={question.parts.length > 0}
                  onChange={(change) => update(index, change)}
                  onRemove={() =>
                    onChange(questions.filter((_, i) => i !== index))
                  }
                  extra={
                    <Button
                      size="xs"
                      variant="ghost"
                      aria-label={`Add a part to ${name}question ${number}`}
                      onClick={() =>
                        update(index, {
                          parts: [
                            ...question.parts,
                            {
                              key: newKey(),
                              prompt: "",
                              points: "",
                              label: null,
                            },
                          ],
                        })
                      }
                    >
                      Add part
                    </Button>
                  }
                />
                {question.parts.map((part, partIndex) => (
                  <div key={part.key} className="pl-8">
                    <Row
                      number={`${number}${partLetter(partIndex)}`}
                      where={name}
                      row={part}
                      page={null}
                      sum={false}
                      onChange={(change) =>
                        updatePart(index, partIndex, change)
                      }
                      onRemove={() =>
                        update(index, {
                          parts: question.parts.filter(
                            (_, i) => i !== partIndex
                          ),
                        })
                      }
                    />
                  </div>
                ))}
              </li>
            )
          })}
        </ol>
      )}
      <div>
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            onChange([
              ...questions,
              {
                key: newKey(),
                prompt: "",
                points: "",
                label: null,
                page: questions.at(-1)?.page ?? 1,
                parts: [],
              },
            ])
          }
        >
          Add question
        </Button>
      </div>
    </div>
  )
}

function Row({
  number,
  where,
  row,
  page,
  sum,
  onChange,
  onRemove,
  extra,
}: {
  number: string
  where: string
  row: Part
  page: number | null
  sum: boolean
  onChange: (change: Partial<Part>) => void
  onRemove: () => void
  extra?: ReactNode
}) {
  const invalid = !sum && parsePoints(row.points) === null
  const label = `${where}question ${number}`
  const capitalized = label.charAt(0).toUpperCase() + label.slice(1)
  return (
    <div className="flex items-center gap-2">
      <span className="w-8 shrink-0 text-xs font-medium tabular-nums">
        {number}
      </span>
      <Input
        aria-label={`${capitalized} prompt`}
        placeholder={`Question ${number}`}
        value={row.prompt}
        onChange={(event) => onChange({ prompt: event.target.value })}
      />
      {sum ? (
        <span className="w-24 shrink-0 text-xs text-muted-foreground">
          Sum of parts
        </span>
      ) : (
        <Input
          aria-label={`${capitalized} points`}
          aria-invalid={invalid && row.points !== ""}
          placeholder="Points"
          type="number"
          min={0}
          step="any"
          inputMode="decimal"
          className="w-24 shrink-0"
          value={row.points}
          onChange={(event) => onChange({ points: event.target.value })}
        />
      )}
      <span className="w-14 shrink-0 text-xs text-muted-foreground">
        {page === null ? "" : `Page ${page}`}
      </span>
      <div className="flex w-24 shrink-0 justify-end gap-1">
        {extra}
        <Button
          size="icon-xs"
          variant="ghost"
          aria-label={`Remove ${label}`}
          onClick={onRemove}
        >
          <Trash2 />
        </Button>
      </div>
    </div>
  )
}
