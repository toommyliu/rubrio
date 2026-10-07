import { useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import {
  closestCenter,
  type CollisionDetection,
  DndContext,
  type DragEndEvent,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  pointerWithin,
  type UniqueIdentifier,
  useDroppable,
  useSensor,
  useSensors,
} from "@dnd-kit/core"
import {
  arrayMove,
  rectSortingStrategy,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"
import { GripVerticalIcon } from "lucide-react"
import { useState } from "react"
import type { CSSProperties, ReactNode } from "react"

import { api } from "@/api/client"
import { upload } from "@/api/errors"
import type { ScanPage, SubmissionInfo } from "@/api/types"

import { FileDropZone } from "@/components/file-drop-zone"
import { JobProgress } from "@/components/job-progress"
import { ErrorText, Loading, PageTitle, Section } from "@/components/page"
import { PageViewer, type ViewedPage } from "@/components/page-viewer"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { type ConfirmRequest, useConfirm } from "@/hooks/use-confirm"
import { useJobs } from "@/hooks/use-jobs"
import { type ScanFixes, useScanFixes } from "@/hooks/use-scan-fixes"
import { PDF } from "@/lib/accept"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/courses/$course/$assignment/scans")({
  component: ScansPage,
})

type Container = number | "unassigned"
type Layout = Map<Container, number[]>

function containerId(container: Container): string {
  return `container:${container}`
}

function parseContainer(id: UniqueIdentifier): Container | null {
  if (typeof id !== "string" || !id.startsWith("container:")) return null
  const rest = id.slice("container:".length)
  return rest === "unassigned" ? "unassigned" : Number(rest)
}

const collision: CollisionDetection = (args) => {
  const within = pointerWithin(args)
  const pages = within.filter((hit) => parseContainer(hit.id) === null)
  if (pages.length > 0) return pages
  if (within.length > 0) return within
  return closestCenter(args)
}

function ScansPage() {
  const params = Route.useParams()
  const path = { course: params.course, slug: params.assignment }
  const overview = api.useQuery(
    "get",
    "/api/courses/{course}/assignments/{slug}/scans",
    { params: { path } }
  )
  const [flaggedOnly, setFlaggedOnly] = useState(false)
  const [merging, setMerging] = useState<number[]>([])
  const [viewing, setViewing] = useState<number | null>(null)
  const [pending, setPending] = useState<Layout | null>(null)
  const [dragged, setDragged] = useState<number | null>(null)
  const [overContainer, setOverContainer] = useState<Container | null>(null)
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  )
  const fix = useScanFixes()

  if (!overview.data) {
    return (
      <>
        <Loading what="scans" />
        <ErrorText error={overview.error} />
      </>
    )
  }
  const { submissions, unassigned, scans } = overview.data
  const pageById = new Map(
    [...submissions.flatMap((s) => s.pages), ...unassigned].map((page) => [
      page.id,
      page,
    ])
  )
  const layout: Layout =
    pending ??
    new Map<Container, number[]>([
      ...submissions.map(
        (s) => [s.id, s.pages.map((page) => page.id)] as const
      ),
      ["unassigned", unassigned.map((page) => page.id)],
    ])
  const pagesIn = (container: Container) =>
    (layout.get(container) ?? []).flatMap((id) => pageById.get(id) ?? [])

  function containerOf(id: UniqueIdentifier): Container | null {
    const parsed = parseContainer(id)
    if (parsed !== null) return parsed
    for (const [container, ids] of layout) {
      if (ids.includes(Number(id))) return container
    }
    return null
  }

  function onDragEnd({ active, over }: DragEndEvent) {
    setDragged(null)
    setOverContainer(null)
    if (!over) return
    const page = Number(active.id)
    const from = containerOf(active.id)
    const to = containerOf(over.id)
    if (from === null || to === null || to === "unassigned") return
    const target = [...(layout.get(to) ?? [])]
    const index =
      parseContainer(over.id) === null
        ? target.indexOf(Number(over.id))
        : target.length
    const settle = { onSettled: () => setPending(null) }
    if (from === to) {
      if (active.id === over.id) return
      const next = arrayMove(target, target.indexOf(page), index)
      setPending(new Map(layout).set(to, next))
      fix.reorder.mutate(
        { params: { path: { submission: to } }, body: { scan_pages: next } },
        settle
      )
      return
    }
    target.splice(index, 0, page)
    setPending(
      new Map(layout)
        .set(
          from,
          (layout.get(from) ?? []).filter((id) => id !== page)
        )
        .set(to, target)
    )
    fix.move.mutate(
      {
        params: { path: { page } },
        body: { submission: to, position: index },
      },
      settle
    )
  }

  const flagged = submissions.filter(
    (submission) => submission.flags.length > 0
  )
  const shown = flaggedOnly ? flagged : submissions
  const draggedPage = dragged === null ? undefined : pageById.get(dragged)
  const looseOnes = pagesIn("unassigned")
  return (
    <div className="flex flex-col gap-8">
      <PageTitle
        title="Scans"
        description="Upload the scanned PDFs. Rubricate matches each page to its template page, groups pages into submissions and flags anything that needs a look. Uploading the same PDF twice changes nothing."
      />
      <UploadScans />
      {scans.length > 0 && (
        <Section title="Uploaded scans">
          <ul className="flex flex-col border-t text-xs">
            {scans.map((scan) => {
              const removed = submissions.filter((submission) =>
                submission.pages.every((page) => page.scan === scan.id)
              )
              const grades = removed.reduce((sum, s) => sum + s.grades, 0)
              return (
                <li
                  key={scan.id}
                  className="flex items-center gap-3 border-b py-1.5"
                >
                  <span className="font-medium">{scan.name}</span>
                  <span className="text-muted-foreground">
                    {plural(scan.pages, "page")}
                  </span>
                  <ConfirmDelete
                    trigger="Delete scan"
                    title={`Delete ${scan.name}?`}
                    description={`This removes its ${plural(scan.pages, "page")}${removed.length > 0 ? ` and ${plural(removed.length, "submission")}${grades > 0 ? `, including ${plural(grades, "grade")}` : ""}` : ""}.`}
                    action="Delete scan"
                    onConfirm={() =>
                      fix.deleteScan.mutate({
                        params: { path: { scan: scan.id } },
                      })
                    }
                  />
                </li>
              )
            })}
          </ul>
        </Section>
      )}
      <DndContext
        sensors={sensors}
        collisionDetection={collision}
        onDragStart={({ active }) => setDragged(Number(active.id))}
        onDragOver={({ over }) =>
          setOverContainer(over ? containerOf(over.id) : null)
        }
        onDragCancel={() => {
          setDragged(null)
          setOverContainer(null)
        }}
        onDragEnd={onDragEnd}
      >
        {scans.length > 0 && (
          <Section
            title="Submissions"
            description={`${submissions.length} submissions from ${scans.length} ${scans.length === 1 ? "scan" : "scans"}. ${flagged.length === 0 ? "Nothing is flagged." : `${flagged.length} flagged.`} Drag a page by its handle to reorder it or to move it to another submission.`}
          >
            <div className="flex items-center gap-4 text-xs">
              <label className="flex items-center gap-2">
                <Checkbox
                  checked={flaggedOnly}
                  onCheckedChange={(checked) => setFlaggedOnly(checked)}
                />
                Show flagged only
              </label>
              {merging.length > 0 && (
                <>
                  <span className="text-muted-foreground">
                    {merging.length} selected
                  </span>
                  <Button
                    size="sm"
                    disabled={merging.length < 2 || fix.busy}
                    onClick={() =>
                      fix.merge.mutate(
                        { body: { submissions: merging } },
                        { onSuccess: () => setMerging([]) }
                      )
                    }
                  >
                    Merge selected
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setMerging([])}
                  >
                    Clear selection
                  </Button>
                </>
              )}
            </div>
            <ErrorText error={fix.error} />
            <ol className="flex flex-col gap-4">
              {shown.map((submission) => (
                <SubmissionCard
                  key={submission.id}
                  submission={submission}
                  pages={pagesIn(submission.id)}
                  index={submissions.indexOf(submission) + 1}
                  submissions={submissions}
                  target={
                    dragged !== null &&
                    overContainer === submission.id &&
                    containerOf(dragged) !== submission.id
                  }
                  selected={merging.includes(submission.id)}
                  onSelect={(checked) =>
                    setMerging((current) =>
                      checked
                        ? [...current, submission.id]
                        : current.filter((id) => id !== submission.id)
                    )
                  }
                  fix={fix}
                />
              ))}
            </ol>
          </Section>
        )}
        {looseOnes.length > 0 && (
          <Section
            title="Pages in no submission"
            description="These pages aren't in any submission, such as scratch paper at the start of a scan or pages of a removed submission. Drag one onto a submission, or use Move to."
          >
            <SortableContext
              id={containerId("unassigned")}
              items={looseOnes.map((page) => page.id)}
              strategy={rectSortingStrategy}
            >
              <ul className="flex flex-wrap gap-3">
                {looseOnes.map((page, position) => (
                  <SortablePageTile
                    key={page.id}
                    page={page}
                    position={position}
                    currentSubmission={null}
                    submissions={submissions}
                    fix={fix}
                    onView={() => setViewing(position)}
                  />
                ))}
              </ul>
            </SortableContext>
            <PageViewer
              pages={looseOnes.map(viewed)}
              index={viewing}
              onIndexChange={setViewing}
            />
          </Section>
        )}
        <DragOverlay>
          {draggedPage && (
            <img
              src={`/api/scan-pages/${draggedPage.id}/image`}
              alt=""
              className="w-32 border bg-white shadow-lg"
            />
          )}
        </DragOverlay>
      </DndContext>
    </div>
  )
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`
}

function ConfirmDelete({
  trigger,
  onConfirm,
  ...request
}: ConfirmRequest & { trigger: string; onConfirm: () => void }) {
  const { confirm } = useConfirm()
  return (
    <Button
      size="xs"
      variant="ghost"
      className="ml-auto text-destructive"
      onClick={async () => {
        if (await confirm(request)) onConfirm()
      }}
    >
      {trigger}
    </Button>
  )
}

function UploadScans() {
  const params = Route.useParams()
  const path = { course: params.course, slug: params.assignment }
  const queryClient = useQueryClient()
  const [error, setError] = useState<unknown>(null)
  const [sending, setSending] = useState(false)
  const { active, finished, track } = useJobs(path)

  async function send(files: File[]) {
    if (files.length === 0) return
    setError(null)
    setSending(true)
    try {
      const job = await upload(
        `/api/courses/${params.course}/assignments/${params.assignment}/scans`,
        "files",
        files
      )
      if (
        typeof job === "object" &&
        job !== null &&
        "id" in job &&
        typeof job.id === "number"
      ) {
        track(job.id)
      }
      await queryClient.invalidateQueries()
    } catch (e) {
      setError(e)
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <FileDropZone
        prompt="Drop scanned PDFs here"
        inputLabel="Scanned PDFs"
        accept={PDF}
        multiple
        busy={sending}
        onFiles={(files) => void send(files)}
      />
      <ErrorText error={error} />
      {active.map((job) => (
        <JobProgress key={job.id} job={job} />
      ))}
      {finished.map((job) =>
        job.state === "failed" ? (
          <p key={job.id} role="alert" className="text-xs text-destructive">
            A run failed: {job.error}
          </p>
        ) : (
          job.message && (
            <p key={job.id} className="text-xs text-muted-foreground">
              {job.message}
            </p>
          )
        )
      )}
    </div>
  )
}

function SubmissionCard({
  submission,
  pages,
  index,
  submissions,
  target,
  selected,
  onSelect,
  fix,
}: {
  submission: SubmissionInfo
  pages: ScanPage[]
  index: number
  submissions: SubmissionInfo[]
  target: boolean
  selected: boolean
  onSelect: (checked: boolean) => void
  fix: ScanFixes
}) {
  const [viewing, setViewing] = useState<number | null>(null)
  const { setNodeRef } = useDroppable({ id: containerId(submission.id) })
  return (
    <li
      ref={setNodeRef}
      className={cn(
        "flex flex-col gap-2 border p-3 transition-colors",
        target && "border-primary bg-primary/5"
      )}
      aria-label={`Submission ${index}`}
    >
      <div className="flex items-center gap-3 text-xs">
        <Checkbox
          checked={selected}
          onCheckedChange={onSelect}
          aria-label={`Select submission ${index} to merge`}
        />
        <span className="font-medium">Submission {index}</span>
        {submission.version && (
          <span className="text-muted-foreground">
            Version {submission.version}
          </span>
        )}
        {submission.student_name && <span>{submission.student_name}</span>}
        {submission.flags.length === 0 && (
          <span className="text-muted-foreground">No flags</span>
        )}
        <ConfirmDelete
          trigger="Remove submission"
          title={`Remove submission ${index}?`}
          description={`Its ${plural(submission.pages.length, "page")} move to Pages in no submission, where you can drag them somewhere else${submission.grades > 0 ? `. Its ${plural(submission.grades, "grade")} are deleted` : ""}.`}
          action="Remove submission"
          onConfirm={() =>
            fix.removeSubmission.mutate({
              params: { path: { submission: submission.id } },
            })
          }
        />
      </div>
      {submission.flags.length > 0 && (
        <ul className="flex flex-col gap-1">
          {submission.flags.map((flag) => (
            <li
              key={`${flag.kind}:${flag.scan_pages.join(",")}`}
              className="flex items-center gap-2 text-xs"
            >
              <Badge variant="destructive">{FLAG_LABELS[flag.kind]}</Badge>
              <span>{flag.message}</span>
            </li>
          ))}
        </ul>
      )}
      <SortableContext
        id={containerId(submission.id)}
        items={pages.map((page) => page.id)}
        strategy={rectSortingStrategy}
      >
        <ol className="flex min-h-12 flex-wrap gap-3">
          {pages.map((page, position) => (
            <SortablePageTile
              key={page.id}
              page={page}
              position={position}
              currentSubmission={submission}
              submissions={submissions}
              fix={fix}
              onView={() => setViewing(position)}
            />
          ))}
        </ol>
      </SortableContext>
      <PageViewer
        pages={pages.map(viewed)}
        index={viewing}
        onIndexChange={setViewing}
      />
    </li>
  )
}

const FLAG_LABELS: Record<SubmissionInfo["flags"][number]["kind"], string> = {
  missing_page: "Missing page",
  repeated_page: "Repeated page",
  out_of_order: "Out of order",
  mixed_versions: "Mixed versions",
  extra_page: "Extra page",
}

function identity(page: ScanPage): string {
  if (page.extra) return "Extra page"
  if (page.page !== null) return `Version ${page.version}, page ${page.page}`
  return "Matched no template page"
}

function where(page: ScanPage): string {
  return `${page.scan_name}, page ${page.page_index + 1}`
}

function viewed(page: ScanPage): ViewedPage {
  return { id: page.id, title: identity(page), detail: where(page) }
}

type PageTileProps = {
  page: ScanPage
  position: number
  currentSubmission: SubmissionInfo | null
  submissions: SubmissionInfo[]
  fix: ScanFixes
  onView: () => void
}

function SortablePageTile(props: PageTileProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: props.page.id })
  return (
    <PageTile
      {...props}
      drag={{
        ref: setNodeRef,
        style: { transform: CSS.Transform.toString(transform), transition },
        dragging: isDragging,
        handle: (
          <button
            type="button"
            ref={setActivatorNodeRef}
            {...attributes}
            {...listeners}
            aria-label={`Reorder ${where(props.page)}`}
            className="flex cursor-grab items-center gap-1 text-muted-foreground hover:text-foreground active:cursor-grabbing"
          >
            <GripVerticalIcon className="size-3.5" />
            {props.position + 1}
          </button>
        ),
      }}
    />
  )
}

function PageTile({
  page,
  position,
  currentSubmission,
  submissions,
  fix,
  onView,
  drag,
}: PageTileProps & {
  drag?: {
    ref: (element: HTMLElement | null) => void
    style: CSSProperties
    dragging: boolean
    handle: ReactNode
  }
}) {
  const label = where(page)
  const targets = submissions.filter(
    (submission) => submission.id !== currentSubmission?.id
  )
  return (
    <li
      ref={drag?.ref}
      style={drag?.style}
      className={cn(
        "flex w-40 flex-col gap-1 bg-background text-xs",
        drag?.dragging && "opacity-30"
      )}
      aria-label={label}
    >
      {drag?.handle}
      <button
        type="button"
        onClick={onView}
        aria-label={`View ${label}`}
        className="cursor-zoom-in"
      >
        <img
          src={`/api/scan-pages/${page.id}/image`}
          alt=""
          loading="lazy"
          className="w-40 border"
        />
      </button>
      <span className="font-medium">{identity(page)}</span>
      <span className="text-muted-foreground">{label}</span>
      <Select
        value={null}
        disabled={fix.busy}
        onValueChange={(value) => {
          if (value === null) return
          fix.move.mutate({
            params: { path: { page: page.id } },
            body: { submission: value === "new" ? null : Number(value) },
          })
        }}
      >
        <SelectTrigger
          size="sm"
          className="w-full"
          aria-label={`Move ${label}`}
        >
          <SelectValue placeholder="Move to…" />
        </SelectTrigger>
        <SelectContent>
          {targets.map((submission) => (
            <SelectItem key={submission.id} value={String(submission.id)}>
              Submission {submissions.indexOf(submission) + 1}
              {submission.student_name ? ` (${submission.student_name})` : ""}
            </SelectItem>
          ))}
          <SelectItem value="new">A new submission</SelectItem>
        </SelectContent>
      </Select>
      {currentSubmission && (
        <div className="flex flex-wrap gap-1">
          <Button
            size="xs"
            variant="outline"
            disabled={fix.busy}
            onClick={() =>
              fix.extra.mutate({
                params: { path: { page: page.id } },
                body: { extra: !page.extra || !page.by_hand },
              })
            }
          >
            {!page.extra
              ? "Mark as extra page"
              : page.by_hand
                ? "Not an extra page"
                : "Keep as extra page"}
          </Button>
          {position > 0 && (
            <Button
              size="xs"
              variant="outline"
              disabled={fix.busy}
              onClick={() =>
                fix.split.mutate({
                  params: { path: { submission: currentSubmission.id } },
                  body: { first_scan_page: page.id },
                })
              }
            >
              Split from here
            </Button>
          )}
        </div>
      )}
    </li>
  )
}
