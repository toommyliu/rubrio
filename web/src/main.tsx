import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { createRouter, RouterProvider } from "@tanstack/react-router"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import { ConfirmProvider } from "@/components/confirm-provider"
import { ThemeProvider } from "@/components/theme-provider.tsx"
import { routeTree } from "./routeTree.gen"

const queryClient = new QueryClient()
const router = createRouter({
  routeTree,
  defaultRemountDeps: ({ params }) => params,
})

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <ConfirmProvider>
          <RouterProvider router={router} />
        </ConfirmProvider>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>
)
