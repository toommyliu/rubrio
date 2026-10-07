import { useQueryClient } from "@tanstack/react-query"

import { api } from "@/api/client"

export type ScanFixes = ReturnType<typeof useScanFixes>

export function useScanFixes() {
  const queryClient = useQueryClient()
  const options = { onSuccess: () => queryClient.invalidateQueries() }
  const move = api.useMutation("post", "/api/scan-pages/{page}/move", options)
  const extra = api.useMutation("post", "/api/scan-pages/{page}/extra", options)
  const split = api.useMutation(
    "post",
    "/api/submissions/{submission}/split",
    options
  )
  const merge = api.useMutation("post", "/api/submissions/merge", options)
  const reorder = api.useMutation(
    "post",
    "/api/submissions/{submission}/order",
    options
  )
  const deleteScan = api.useMutation("delete", "/api/scans/{scan}", options)
  const removeSubmission = api.useMutation(
    "delete",
    "/api/submissions/{submission}",
    options
  )
  const all = [move, extra, split, merge, reorder, deleteScan, removeSubmission]
  return {
    move,
    extra,
    split,
    merge,
    reorder,
    deleteScan,
    removeSubmission,
    busy: all.some((mutation) => mutation.isPending),
    error: all.find((mutation) => mutation.error)?.error ?? null,
  }
}
