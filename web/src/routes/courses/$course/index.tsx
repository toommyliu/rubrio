import { createFileRoute, Link } from "@tanstack/react-router"

import { api } from "@/api/client"
import type { Student } from "@/api/types"
import { NewAssignmentDialog } from "@/components/new-assignment-dialog"
import { ErrorText, Loading, PageTitle, Section } from "@/components/page"
import { Badge } from "@/components/ui/badge"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

export const Route = createFileRoute("/courses/$course/")({
  component: CoursePage,
})

function CoursePage() {
  const { course } = Route.useParams()
  const detail = api.useQuery("get", "/api/courses/{course}", {
    params: { path: { course } },
  })
  if (detail.isPending) return <Loading what="the course" />
  if (!detail.data) return <ErrorText error={detail.error} />
  const { assignments, roster } = detail.data
  return (
    <div className="flex max-w-3xl flex-col gap-10">
      <PageTitle
        title={detail.data.course.name}
        description={detail.data.course.term}
      />
      <Section title="Assignments">
        {assignments.length === 0 ? (
          <p className="text-sm text-muted-foreground">No assignments yet.</p>
        ) : (
          <ul className="flex flex-col border-t">
            {assignments.map((assignment) => (
              <li key={assignment.id} className="border-b">
                <Link
                  to="/courses/$course/$assignment"
                  params={{ course, assignment: assignment.slug }}
                  className="flex items-baseline justify-between gap-4 py-2 text-sm hover:bg-muted"
                >
                  <span className="font-medium">{assignment.title}</span>
                  <span className="text-xs text-muted-foreground">
                    {assignment.slug}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
        <div>
          <NewAssignmentDialog course={course} />
        </div>
      </Section>
      <Section
        title="Roster"
        description="Import the roster on an assignment's Names tab. One roster serves every assignment in this course."
      >
        <RosterTable roster={roster} />
      </Section>
    </div>
  )
}

function RosterTable({ roster }: { roster: Student[] }) {
  if (roster.length === 0) {
    return <p className="text-sm text-muted-foreground">No students yet.</p>
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>SID</TableHead>
          <TableHead>Email</TableHead>
          <TableHead>Section</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {roster.map((student) => (
          <TableRow key={student.sid}>
            <TableCell>
              {student.name}{" "}
              {student.dropped && <Badge variant="outline">Dropped</Badge>}
            </TableCell>
            <TableCell className="tabular-nums">{student.sid}</TableCell>
            <TableCell>{student.email}</TableCell>
            <TableCell>{student.section}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
