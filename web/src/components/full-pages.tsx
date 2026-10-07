import { api } from "@/api/client"
import { PageViewer } from "@/components/page-viewer"
import { Button } from "@/components/ui/button"

export function FullPages({
  course,
  assignment,
  submission,
  viewing,
  onViewingChange,
}: {
  course: string
  assignment: string
  submission: number
  viewing: number | null
  onViewingChange: (index: number | null) => void
}) {
  const scans = api.useQuery(
    "get",
    "/api/courses/{course}/assignments/{slug}/scans",
    { params: { path: { course, slug: assignment } } }
  )
  const pages = (
    scans.data?.submissions.find((s) => s.id === submission)?.pages ?? []
  ).map((page, position) => ({
    id: page.id,
    title: page.extra
      ? `Extra page ${position + 1}`
      : `Page ${page.page ?? position + 1}`,
    detail: `${page.scan_name}, page ${page.page_index + 1}`,
  }))
  if (pages.length === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-muted-foreground">Full pages:</span>
      {pages.map((page, position) => (
        <Button
          key={page.id}
          size="xs"
          variant="outline"
          onClick={() => onViewingChange(position)}
        >
          {page.title}
        </Button>
      ))}
      <PageViewer
        pages={pages}
        index={viewing}
        onIndexChange={onViewingChange}
      />
    </div>
  )
}
