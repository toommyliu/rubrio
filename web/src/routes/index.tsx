import { createFileRoute, Link } from "@tanstack/react-router"

import { api } from "@/api/client"
import { NewAssignmentDialog } from "@/components/new-assignment-dialog"
import { ErrorText, Loading, PageTitle } from "@/components/page"

export const Route = createFileRoute("/")({
  component: Home,
})

function Home() {
  const courses = api.useQuery("get", "/api/courses")
  return (
    <div className="flex max-w-2xl flex-col gap-8">
      <PageTitle
        title="Rubrio"
        description="Create an assignment to start grading, or open a course."
      />
      <div>
        <NewAssignmentDialog />
      </div>
      {courses.data?.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No courses yet. Creating an assignment starts one.
        </p>
      )}
      {courses.isPending && <Loading what="courses" />}
      <ErrorText error={courses.error} />
      {courses.data && courses.data.length > 0 && (
        <ul className="flex flex-col border-t">
          {courses.data.map((course) => (
            <li key={course.id} className="border-b">
              <Link
                to="/courses/$course"
                params={{ course: course.slug }}
                className="flex items-baseline justify-between gap-4 py-2 text-sm hover:bg-muted"
              >
                <span className="font-medium">{course.name}</span>
                <span className="text-xs text-muted-foreground">
                  {course.term} · {course.slug}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
