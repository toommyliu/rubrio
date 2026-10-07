import { createFileRoute, Link, Outlet } from "@tanstack/react-router"

const STEPS = [
  { to: "/courses/$course/$assignment", label: "Overview", exact: true },
  { to: "/courses/$course/$assignment/templates", label: "Templates" },
  { to: "/courses/$course/$assignment/outline", label: "Outline" },
  { to: "/courses/$course/$assignment/scans", label: "Scans" },
  { to: "/courses/$course/$assignment/names", label: "Names" },
] as const

export const Route = createFileRoute("/courses/$course/$assignment")({
  component: AssignmentLayout,
})

function AssignmentLayout() {
  const params = Route.useParams()
  return (
    <div className="flex flex-1 flex-col gap-6">
      <nav aria-label="Steps" className="-mt-2 flex gap-1 border-b">
        {STEPS.map((step) => (
          <Link
            key={step.to}
            to={step.to}
            params={params}
            activeOptions={{ exact: "exact" in step }}
            className="-mb-px border-b-2 border-transparent px-3 py-2 text-xs text-muted-foreground hover:text-foreground data-[status=active]:border-foreground data-[status=active]:text-foreground"
          >
            {step.label}
          </Link>
        ))}
      </nav>
      <Outlet />
    </div>
  )
}
