import {
  createRootRoute,
  Link,
  Outlet,
  useParams,
} from "@tanstack/react-router"

export const Route = createRootRoute({ component: Root })

function Root() {
  const { course, assignment } = useParams({ strict: false })
  return (
    <div className="flex min-h-svh flex-col">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b px-6 text-sm">
        <Link to="/" className="font-medium">
          Rubricate
        </Link>
        {course && (
          <>
            <span className="text-muted-foreground">/</span>
            <Link to="/courses/$course" params={{ course }}>
              {course}
            </Link>
          </>
        )}
        {course && assignment && (
          <>
            <span className="text-muted-foreground">/</span>
            <Link
              to="/courses/$course/$assignment"
              params={{ course, assignment }}
            >
              {assignment}
            </Link>
          </>
        )}
      </header>
      <main className="flex flex-1 flex-col p-6">
        <Outlet />
      </main>
    </div>
  )
}
