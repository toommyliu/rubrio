import { useQueryClient } from "@tanstack/react-query"
import { createFileRoute, useNavigate } from "@tanstack/react-router"
import { useState } from "react"

import { api } from "@/api/client"
import type { AssignmentInfo } from "@/api/types"
import { AssignmentFileEditor } from "@/components/assignment-file-editor"
import { confirmScoreChange, useConfirm } from "@/hooks/use-confirm"
import { ErrorText, Loading, PageTitle, Section } from "@/components/page"
import { Button } from "@/components/ui/button"

export const Route = createFileRoute("/courses/$course/$assignment/")({
  component: Overview,
})

function Overview() {
  const params = Route.useParams()
  const path = { course: params.course, slug: params.assignment }
  const info = api.useQuery("get", "/api/courses/{course}/assignments/{slug}", {
    params: { path },
  })
  if (info.isPending) return <Loading what="the assignment" />
  if (!info.data) return <ErrorText error={info.error} />
  return (
    <div className="flex max-w-3xl flex-col gap-10">
      <PageTitle title={info.data.title} />
      <Section
        title="Assignment file"
        description={
          info.data.has_scans
            ? "Scans are uploaded, so only answer keys, points and rubric items can change here, and rubric items only until a question's first grade. After that, edit the rubric on the Grade page."
            : "Any change is allowed until scans are uploaded."
        }
      >
        <EditFile
          key={`${info.data.id}:${info.data.source}`}
          info={info.data}
        />
      </Section>
      <DeleteAssignment info={info.data} />
    </div>
  )
}

function EditFile({ info }: { info: AssignmentInfo }) {
  const params = Route.useParams()
  const queryClient = useQueryClient()
  const [source, setSource] = useState(info.source)
  const { confirm } = useConfirm()
  const edit = api.useMutation(
    "put",
    "/api/courses/{course}/assignments/{slug}"
  )

  function save(confirmed: boolean) {
    edit.mutate(
      {
        params: { path: { course: params.course, slug: params.assignment } },
        body: { source, confirm: confirmed },
      },
      {
        onSuccess: () => void queryClient.invalidateQueries(),
        onError: async (error) => {
          if (await confirmScoreChange(confirm, error)) save(true)
        },
      }
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <AssignmentFileEditor source={source} onChange={setSource} />
      <ErrorText error={edit.error} />
      <div>
        <Button
          disabled={source === info.source || edit.isPending}
          onClick={() => save(false)}
        >
          Save changes
        </Button>
      </div>
    </div>
  )
}

function DeleteAssignment({ info }: { info: AssignmentInfo }) {
  const params = Route.useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { confirm } = useConfirm()
  const remove = api.useMutation(
    "delete",
    "/api/courses/{course}/assignments/{slug}",
    {
      onSuccess: async () => {
        await navigate({
          to: "/courses/$course",
          params: { course: params.course },
        })
        queryClient.removeQueries()
      },
    }
  )
  const loses = info.has_grades
    ? "its templates, outline, scans, name matches and every grade"
    : info.has_scans
      ? "its templates, outline, scans and name matches"
      : "its templates and outline"

  async function deleteAssignment() {
    const confirmed = await confirm({
      title: `Delete ${info.title}?`,
      description: `This deletes ${loses}. It can't be undone.`,
      action: "Delete assignment",
    })
    if (confirmed) {
      remove.mutate({
        params: { path: { course: params.course, slug: params.assignment } },
      })
    }
  }

  return (
    <Section
      title="Delete assignment"
      description="Removes the assignment and everything in it. Use it to start over. The roster stays."
    >
      <Button
        variant="destructive"
        className="w-fit"
        disabled={remove.isPending}
        onClick={() => void deleteAssignment()}
      >
        Delete assignment
      </Button>
      <ErrorText error={remove.error} />
    </Section>
  )
}
