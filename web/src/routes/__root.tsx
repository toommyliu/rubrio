import { createRootRoute, Outlet } from "@tanstack/react-router"

export const Route = createRootRoute({
  component: () => (
    <div className="min-h-svh p-6">
      <Outlet />
    </div>
  ),
})
