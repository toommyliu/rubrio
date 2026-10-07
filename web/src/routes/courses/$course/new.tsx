import { useQueryClient } from "@tanstack/react-query"
import { createFileRoute, useNavigate } from "@tanstack/react-router"
import { useState } from "react"

import { api } from "@/api/client"
import { AssignmentFileEditor } from "@/components/assignment-file-editor"
import { ErrorText, PageTitle } from "@/components/page"
import { Button } from "@/components/ui/button"

export const Route = createFileRoute("/courses/$course/new")({
  component: NewAssignment,
})

function NewAssignment() {
  const { course } = Route.useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [source, setSource] = useState("")
  const create = api.useMutation("post", "/api/courses/{course}/assignments", {
    onSuccess: async (assignment) => {
      await queryClient.invalidateQueries()
      await navigate({
        to: "/courses/$course/$assignment",
        params: { course, assignment: assignment.slug },
      })
    },
  })
  return (
    <form
      className="flex max-w-3xl flex-col gap-6"
      onSubmit={(event) => {
        event.preventDefault()
        create.mutate({ params: { path: { course } }, body: { source } })
      }}
    >
      <PageTitle
        title="New assignment"
        description="Paste the assignment file or load it from disk. Problems show up below as you type, with their line numbers."
      />
      <AssignmentFileEditor source={source} onChange={setSource} />
      <ErrorText error={create.error} />
      <div>
        <Button type="submit" disabled={create.isPending}>
          Create assignment
        </Button>
      </div>
    </form>
  )
}
