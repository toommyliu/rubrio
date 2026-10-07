import type { ReactNode } from "react"

import { errorMessage, isErrorBody } from "@/api/errors"

export function PageTitle({
  title,
  description,
  actions,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="flex flex-col gap-1">
        <h1 className="text-lg font-medium">{title}</h1>
        {description && (
          <p className="max-w-prose text-sm text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex shrink-0 gap-2">{actions}</div>}
    </div>
  )
}

export function Section({
  title,
  description,
  children,
}: {
  title: string
  description?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 className="text-sm font-medium">{title}</h2>
        {description && (
          <p className="max-w-prose text-xs text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {children}
    </section>
  )
}

export function ErrorText({ error }: { error: unknown }) {
  if (error === null || error === undefined) return null
  if (isErrorBody(error) && error.kind === "needs_confirmation") return null
  return (
    <p role="alert" className="text-xs text-destructive">
      {errorMessage(error)}
    </p>
  )
}

export function Loading({ what }: { what: string }) {
  return <p className="text-sm text-muted-foreground">Loading {what}…</p>
}
