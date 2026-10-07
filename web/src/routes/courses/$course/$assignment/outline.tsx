import { useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { useHotkeys } from "@tanstack/react-hotkeys"
import { useState } from "react"
import type { PointerEvent as ReactPointerEvent } from "react"

import { api } from "@/api/client"
import type { Box, QuestionInfo, TemplatePage } from "@/api/types"
import { ErrorText, Loading, PageTitle } from "@/components/page"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/courses/$course/$assignment/outline")({
  component: OutlinePage,
})

type Field = "name" | "sid"
type Target = { kind: "question"; id: number } | { kind: "field"; field: Field }
type Rect = { x0: number; y0: number; x1: number; y1: number }
type Edges = { left: boolean; right: boolean; top: boolean; bottom: boolean }
type Drag =
  | { kind: "draw"; page: TemplatePage; origin: Point; rect: Rect }
  | { kind: "move"; box: Box; page: TemplatePage; origin: Point; rect: Rect }
  | {
      kind: "resize"
      box: Box
      page: TemplatePage
      origin: Point
      edges: Edges
      rect: Rect
    }
type Point = { x: number; y: number }
type Hold = { id: number | null; page: number; rect: Rect }

const FIELD_LABELS: Record<Field, string> = { name: "Name", sid: "Student ID" }
const HANDLES: { id: string; edges: Edges; className: string }[] = [
  {
    id: "nw",
    edges: edges("lt"),
    className: "-top-1 -left-1 cursor-nwse-resize",
  },
  {
    id: "n",
    edges: edges("t"),
    className: "-top-1 left-1/2 -ml-1 cursor-ns-resize",
  },
  {
    id: "ne",
    edges: edges("rt"),
    className: "-top-1 -right-1 cursor-nesw-resize",
  },
  {
    id: "e",
    edges: edges("r"),
    className: "top-1/2 -right-1 -mt-1 cursor-ew-resize",
  },
  {
    id: "se",
    edges: edges("rb"),
    className: "-right-1 -bottom-1 cursor-nwse-resize",
  },
  {
    id: "s",
    edges: edges("b"),
    className: "-bottom-1 left-1/2 -ml-1 cursor-ns-resize",
  },
  {
    id: "sw",
    edges: edges("lb"),
    className: "-bottom-1 -left-1 cursor-nesw-resize",
  },
  {
    id: "w",
    edges: edges("l"),
    className: "top-1/2 -left-1 -mt-1 cursor-ew-resize",
  },
]
const MIN_SIZE = 6
const NUDGES = [
  { key: "ArrowLeft", x: -1, y: 0 },
  { key: "ArrowRight", x: 1, y: 0 },
  { key: "ArrowUp", x: 0, y: -1 },
  { key: "ArrowDown", x: 0, y: 1 },
] as const

function edges(spec: string): Edges {
  return {
    left: spec.includes("l"),
    right: spec.includes("r"),
    top: spec.includes("t"),
    bottom: spec.includes("b"),
  }
}

function OutlinePage() {
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
  const questions = api.useQuery(
    "get",
    "/api/courses/{course}/assignments/{slug}/questions",
    { params: { path } }
  )
  const [version, setVersion] = useState<string | null>(null)
  if (!info.data || !outline.data || !questions.data) {
    return (
      <>
        <Loading what="the outline" />
        <ErrorText error={info.error ?? outline.error ?? questions.error} />
      </>
    )
  }
  const current = version ?? info.data.versions[0] ?? "A"
  const pages = outline.data.pages.filter((page) => page.version === current)
  return (
    <div className="flex flex-1 flex-col gap-6">
      <PageTitle
        title="Outline"
        description="Each question's crop is whatever falls inside its boxes. Pick a question, then drag on a page to draw a box. Drag a box to move it, pull an edge or corner to resize it, use the arrow keys to nudge it (Shift for bigger steps), and press Delete to remove it."
      />
      {info.data.versions.length > 1 && (
        <div className="flex gap-1" role="tablist" aria-label="Versions">
          {info.data.versions.map((name) => (
            <Button
              key={name}
              role="tab"
              aria-selected={name === current}
              variant={name === current ? "default" : "outline"}
              size="sm"
              onClick={() => setVersion(name)}
            >
              Version {name}
            </Button>
          ))}
        </div>
      )}
      {pages.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Upload a template for this version first, on the Templates page.
        </p>
      ) : (
        <Editor
          key={current}
          pages={pages}
          boxes={outline.data.boxes.filter((box) =>
            pages.some((page) => page.id === box.template_page)
          )}
          questions={questions.data.filter(
            (question) =>
              question.version === current && question.kind !== "parts"
          )}
        />
      )}
    </div>
  )
}

function Editor({
  pages,
  boxes,
  questions,
}: {
  pages: TemplatePage[]
  boxes: Box[]
  questions: QuestionInfo[]
}) {
  const queryClient = useQueryClient()
  const [target, setTarget] = useState<Target>(
    questions[0]
      ? { kind: "question", id: questions[0].id }
      : { kind: "field", field: "name" }
  )
  const [selected, setSelected] = useState<number | null>(null)
  const [drag, setDrag] = useState<Drag | null>(null)
  const [holds, setHolds] = useState<Hold[]>([])
  const settle = {
    scope: { id: "outline" },
    onSettled: () => queryClient.invalidateQueries(),
  }
  const addBox = api.useMutation("post", "/api/boxes", settle)
  const updateBox = api.useMutation("put", "/api/boxes/{box}", settle)
  const deleteBox = api.useMutation("delete", "/api/boxes/{box}", settle)
  const error = addBox.error ?? updateBox.error ?? deleteBox.error

  const selectedBox = boxes.find((box) => box.id === selected) ?? null

  function hold(entry: Hold, save: Promise<unknown>) {
    setHolds((current) => [...current, entry])
    void save
      .catch(() => {})
      .then(() => setHolds((current) => current.filter((h) => h !== entry)))
  }

  function nudge(delta: Point) {
    if (!selectedBox) return
    const page = pages.find((p) => p.id === selectedBox.template_page)
    if (!page) return
    const rect = shift(shown(selectedBox), delta, page)
    hold(
      { id: selectedBox.id, page: page.id, rect },
      updateBox.mutateAsync({
        params: { path: { box: selectedBox.id } },
        body: rect,
      })
    )
  }

  function removeSelected() {
    if (!selectedBox) return
    deleteBox.mutate({ params: { path: { box: selectedBox.id } } })
    setSelected(null)
  }

  useHotkeys(
    [
      ...NUDGES.flatMap(({ key, x, y }) => [
        { hotkey: key, callback: () => nudge({ x, y }) },
        {
          hotkey: `Shift+${key}` as const,
          callback: () => nudge({ x: x * 10, y: y * 10 }),
        },
      ]),
      { hotkey: "Delete", callback: removeSelected },
      { hotkey: "Backspace", callback: removeSelected },
      { hotkey: "Escape", callback: () => setSelected(null) },
    ],
    { enabled: selectedBox !== null }
  )

  function belongs(box: Box): boolean {
    return target.kind === "question"
      ? box.question === target.id
      : box.field === target.field
  }

  function label(box: Box): string {
    if (box.field) return FIELD_LABELS[box.field]
    return (
      questions.find((question) => question.id === box.question)?.number ?? "?"
    )
  }

  function startDraw(
    event: ReactPointerEvent<HTMLDivElement>,
    page: TemplatePage
  ) {
    if (event.button !== 0) return
    event.currentTarget.setPointerCapture(event.pointerId)
    const origin = pointer(event, page, event.currentTarget)
    setSelected(null)
    setDrag({
      kind: "draw",
      page,
      origin,
      rect: { x0: origin.x, y0: origin.y, x1: origin.x, y1: origin.y },
    })
  }

  function startBoxDrag(
    event: ReactPointerEvent<HTMLElement>,
    box: Box,
    page: TemplatePage,
    handle: Edges | null
  ) {
    if (event.button !== 0) return
    event.stopPropagation()
    const surface = event.currentTarget.closest("[data-page]")
    if (!surface) return
    surface.setPointerCapture(event.pointerId)
    setSelected(box.id)
    if (box.question !== null) setTarget({ kind: "question", id: box.question })
    if (box.field !== null) setTarget({ kind: "field", field: box.field })
    const origin = pointer(event, page, surface)
    const rect = shown(box)
    const latest = { ...box, ...rect }
    setDrag(
      handle
        ? { kind: "resize", box: latest, page, origin, edges: handle, rect }
        : { kind: "move", box: latest, page, origin, rect }
    )
  }

  function continueDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (!drag) return
    const at = pointer(event, drag.page, event.currentTarget)
    const delta = { x: at.x - drag.origin.x, y: at.y - drag.origin.y }
    switch (drag.kind) {
      case "draw":
        setDrag({
          ...drag,
          rect: normalize({
            x0: drag.origin.x,
            y0: drag.origin.y,
            x1: at.x,
            y1: at.y,
          }),
        })
        break
      case "move":
        setDrag({ ...drag, rect: shift(drag.box, delta, drag.page) })
        break
      case "resize":
        setDrag({
          ...drag,
          rect: resize(drag.box, drag.edges, delta, drag.page),
        })
        break
      default: {
        const unhandled: never = drag
        void unhandled
      }
    }
  }

  function endDrag() {
    if (!drag) return
    setDrag(null)
    const { rect, page } = drag
    if (drag.kind === "draw") {
      if (rect.x1 - rect.x0 < MIN_SIZE || rect.y1 - rect.y0 < MIN_SIZE) return
      hold(
        { id: null, page: page.id, rect },
        addBox.mutateAsync({
          body: {
            template_page: page.id,
            question: target.kind === "question" ? target.id : null,
            field: target.kind === "field" ? target.field : null,
            ...rect,
          },
        })
      )
      return
    }
    const unchanged =
      rect.x0 === drag.box.x0 &&
      rect.y0 === drag.box.y0 &&
      rect.x1 === drag.box.x1 &&
      rect.y1 === drag.box.y1
    if (unchanged) return
    hold(
      { id: drag.box.id, page: page.id, rect },
      updateBox.mutateAsync({
        params: { path: { box: drag.box.id } },
        body: rect,
      })
    )
  }

  function shown(box: Box): Rect {
    if (drag && drag.kind !== "draw" && drag.box.id === box.id) return drag.rect
    return holds.findLast((h) => h.id === box.id)?.rect ?? box
  }

  const counts = new Map<string, number>()
  for (const box of boxes) {
    const key = box.field ?? `q${box.question}`
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  const targets: {
    target: Target
    key: string
    title: string
    detail: string
  }[] = [
    ...questions.map((question) => ({
      target: { kind: "question" as const, id: question.id },
      key: `q${question.id}`,
      title: question.number,
      detail: question.prompt,
    })),
    ...(["name", "sid"] as const).map((field) => ({
      target: { kind: "field" as const, field },
      key: field,
      title: FIELD_LABELS[field],
      detail: "Used to match names",
    })),
  ]
  const missing = questions.filter(
    (question) => !counts.has(`q${question.id}`)
  ).length

  return (
    <div className="flex flex-1 gap-6">
      <aside className="flex w-72 shrink-0 flex-col gap-2">
        <p className="text-xs text-muted-foreground" aria-live="polite">
          {missing === 0
            ? "Every question has a box."
            : `${missing} ${missing === 1 ? "question has" : "questions have"} no box yet.`}
        </p>
        <ul
          className="sticky top-4 flex flex-col border-t"
          aria-label="Questions"
        >
          {targets.map((entry) => {
            const count = counts.get(entry.key) ?? 0
            const active =
              entry.target.kind === target.kind &&
              (entry.target.kind === "question"
                ? target.kind === "question" && target.id === entry.target.id
                : target.kind === "field" &&
                  target.field === entry.target.field)
            return (
              <li key={entry.key} className="border-b">
                <button
                  type="button"
                  aria-pressed={active}
                  onClick={() => {
                    setTarget(entry.target)
                    setSelected(null)
                  }}
                  className={cn(
                    "flex w-full items-baseline gap-2 px-2 py-1.5 text-left text-xs hover:bg-muted",
                    active && "bg-muted"
                  )}
                >
                  <span className="w-16 shrink-0 font-medium">
                    {entry.title}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-muted-foreground">
                    {entry.detail}
                  </span>
                  <span
                    className={cn(
                      "shrink-0 tabular-nums",
                      count === 0 &&
                        entry.target.kind === "question" &&
                        "text-destructive"
                    )}
                  >
                    {count === 0
                      ? "No box"
                      : count === 1
                        ? "1 box"
                        : `${count} boxes`}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
        <ErrorText error={error} />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        {pages.map((page) => (
          <figure key={page.id} className="flex max-w-3xl flex-col gap-1">
            <figcaption className="text-xs text-muted-foreground">
              Page {page.page}
            </figcaption>
            <div
              data-page={page.id}
              aria-label={`Page ${page.page}`}
              className="relative cursor-crosshair touch-none border select-none"
              style={{ aspectRatio: `${page.width} / ${page.height}` }}
              onPointerDown={(event) => startDraw(event, page)}
              onPointerMove={continueDrag}
              onPointerUp={endDrag}
              onPointerCancel={() => setDrag(null)}
            >
              <img
                src={`/api/template-pages/${page.id}/image`}
                alt=""
                draggable={false}
                className="pointer-events-none absolute inset-0 size-full"
              />
              {boxes
                .filter((box) => box.template_page === page.id)
                .map((box) => {
                  const rect = shown(box)
                  const mine = belongs(box)
                  return (
                    <div
                      key={box.id}
                      role="button"
                      tabIndex={0}
                      aria-label={`Box for ${label(box)}`}
                      aria-pressed={box.id === selected}
                      onPointerDown={(event) =>
                        startBoxDrag(event, box, page, null)
                      }
                      onFocus={() => setSelected(box.id)}
                      className={cn(
                        "absolute cursor-move border-2 outline-none",
                        mine
                          ? "border-primary bg-primary/10"
                          : "border-muted-foreground/50 bg-muted-foreground/5",
                        box.id === selected && "ring-2 ring-ring"
                      )}
                      style={position(rect, page)}
                    >
                      <span
                        className={cn(
                          "absolute top-0 left-0 px-1 text-[10px] leading-4 font-medium",
                          mine
                            ? "bg-primary text-primary-foreground"
                            : "bg-muted-foreground/60 text-background"
                        )}
                      >
                        {label(box)}
                        {box.suggested && " · suggested"}
                      </span>
                      {box.id === selected &&
                        HANDLES.map((handle) => (
                          <span
                            key={handle.id}
                            onPointerDown={(event) =>
                              startBoxDrag(event, box, page, handle.edges)
                            }
                            className={cn(
                              "absolute size-2 border border-background bg-primary",
                              handle.className
                            )}
                          />
                        ))}
                    </div>
                  )
                })}
              {holds.map(
                (h, index) =>
                  h.id === null &&
                  h.page === page.id && (
                    <div
                      key={index}
                      className="absolute border-2 border-dashed border-primary"
                      style={position(h.rect, page)}
                    />
                  )
              )}
              {drag?.kind === "draw" && drag.page.id === page.id && (
                <div
                  className="absolute border-2 border-dashed border-primary bg-primary/10"
                  style={position(drag.rect, page)}
                />
              )}
            </div>
          </figure>
        ))}
      </div>
    </div>
  )
}

function pointer(
  event: ReactPointerEvent,
  page: TemplatePage,
  element: Element
): Point {
  const bounds = element.getBoundingClientRect()
  return {
    x: clamp(
      ((event.clientX - bounds.left) / bounds.width) * page.width,
      0,
      page.width
    ),
    y: clamp(
      ((event.clientY - bounds.top) / bounds.height) * page.height,
      0,
      page.height
    ),
  }
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(Math.max(value, low), high)
}

function normalize(rect: Rect): Rect {
  return {
    x0: Math.min(rect.x0, rect.x1),
    y0: Math.min(rect.y0, rect.y1),
    x1: Math.max(rect.x0, rect.x1),
    y1: Math.max(rect.y0, rect.y1),
  }
}

function shift(box: Rect, delta: Point, page: TemplatePage): Rect {
  const dx = clamp(delta.x, -box.x0, page.width - box.x1)
  const dy = clamp(delta.y, -box.y0, page.height - box.y1)
  return { x0: box.x0 + dx, y0: box.y0 + dy, x1: box.x1 + dx, y1: box.y1 + dy }
}

function resize(
  box: Rect,
  which: Edges,
  delta: Point,
  page: TemplatePage
): Rect {
  const rect = {
    x0: which.left ? clamp(box.x0 + delta.x, 0, page.width) : box.x0,
    y0: which.top ? clamp(box.y0 + delta.y, 0, page.height) : box.y0,
    x1: which.right ? clamp(box.x1 + delta.x, 0, page.width) : box.x1,
    y1: which.bottom ? clamp(box.y1 + delta.y, 0, page.height) : box.y1,
  }
  const fixed = normalize(rect)
  const [x0, x1] = widen(fixed.x0, fixed.x1, page.width)
  const [y0, y1] = widen(fixed.y0, fixed.y1, page.height)
  return { x0, y0, x1, y1 }
}

function widen(low: number, high: number, limit: number): [number, number] {
  if (high - low >= MIN_SIZE) return [low, high]
  const end = Math.min(low + MIN_SIZE, limit)
  return [end - MIN_SIZE, end]
}

function position(rect: Rect, page: TemplatePage) {
  return {
    left: `${(rect.x0 / page.width) * 100}%`,
    top: `${(rect.y0 / page.height) * 100}%`,
    width: `${((rect.x1 - rect.x0) / page.width) * 100}%`,
    height: `${((rect.y1 - rect.y0) / page.height) * 100}%`,
  }
}
