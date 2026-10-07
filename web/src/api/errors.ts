import type { ErrorBody } from "./types"

const KINDS = new Set<string>([
  "invalid",
  "not_found",
  "stale",
  "needs_confirmation",
  "file",
] satisfies ErrorBody["kind"][])

export function isErrorBody(value: unknown): value is ErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    "kind" in value &&
    typeof value.kind === "string" &&
    KINDS.has(value.kind) &&
    "message" in value &&
    typeof value.message === "string"
  )
}

export function errorMessage(error: unknown): string {
  if (isErrorBody(error)) {
    return error.message
  }
  return "Rubrio couldn't reach its server. Check that it's still running, then try again."
}

export async function upload(
  url: string,
  field: string,
  files: File[]
): Promise<unknown> {
  const form = new FormData()
  for (const file of files) {
    form.append(field, file)
  }
  const response = await fetch(url, { method: "POST", body: form })
  if (!response.ok) {
    throw await response.json()
  }
  return response.json()
}
