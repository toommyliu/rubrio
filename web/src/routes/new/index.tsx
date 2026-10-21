import { useQueryClient } from "@tanstack/react-query"
import { createFileRoute, useNavigate } from "@tanstack/react-router"
import { useState } from "react"

import { api } from "@/api/client"
import { AssignmentFileEditor } from "@/components/assignment-file-editor"
import {
  CourseField,
  courseValue,
  newCourseDraft,
} from "@/components/course-field"
import { ErrorText, PageTitle } from "@/components/page"
import { Button } from "@/components/ui/button"
import { useConfirm } from "@/hooks/use-confirm"

export const Route = createFileRoute("/new/")({
  validateSearch: (search: Record<string, unknown>): { course?: string } =>
    typeof search.course === "string" ? { course: search.course } : {},
  component: NewAssignment,
})

function NewAssignment() {
  const { course } = Route.useSearch()
  const [courseDraft, setCourseDraft] = useState(() => newCourseDraft(course))
  const chosenCourse = courseValue(courseDraft)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { confirm } = useConfirm()
  const [source, setSource] = useState("")
  const create = api.useMutation("post", "/api/assignments", {
    onSuccess: async (assignment) => {
      await queryClient.invalidateQueries()
      await navigate({
        to: "/courses/$course/$assignment/templates",
        params: { course: assignment.course, assignment: assignment.slug },
      })
    },
  })
  return (
    <form
      className="flex max-w-3xl flex-col gap-6"
      onSubmit={(event) => {
        event.preventDefault()
        if (chosenCourse !== null)
          create.mutate({ body: { course: chosenCourse, source } })
      }}
    >
      <PageTitle
        title="New assignment"
        description="Paste the assignment file or load it from disk. Problems show up below as you type, with their line numbers."
      />
      <CourseField value={courseDraft} onChange={setCourseDraft} />
      <AssignmentFileEditor source={source} onChange={setSource} />
      <ErrorText error={create.error} />
      <div className="flex gap-2">
        <Button
          type="submit"
          disabled={create.isPending || chosenCourse === null}
        >
          Create assignment
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={create.isPending}
          onClick={async () => {
            if (
              source.trim() !== "" &&
              !(await confirm({
                title: "Discard this assignment?",
                description:
                  "Nothing is saved until you create the assignment, so the file you've entered here will be lost.",
                action: "Discard",
              }))
            ) {
              return
            }
            await navigate(
              course
                ? { to: "/courses/$course", params: { course } }
                : { to: "/" }
            )
          }}
        >
          Cancel
        </Button>
      </div>
    </form>
  )
}
