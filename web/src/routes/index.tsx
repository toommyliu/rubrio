import { createFileRoute } from "@tanstack/react-router"

import { api } from "@/api/client"

export const Route = createFileRoute("/")({
  component: Home,
})

function Home() {
  const about = api.useQuery("get", "/api/about")
  return (
    <div className="flex flex-col gap-1">
      <h1 className="text-lg font-medium">Rubricate</h1>
      <p className="text-sm text-muted-foreground">
        {about.isPending && "Loading…"}
        {about.isError && "Can't reach the Rubricate server."}
        {about.isSuccess && `Version ${about.data.version}`}
      </p>
    </div>
  )
}
