import { createContext, useContext } from "react"
import type { ReactNode } from "react"

import { isErrorBody } from "@/api/errors"

export type ConfirmRequest = {
  title: string
  description: ReactNode
  action: string
}

export type Confirm = (request: ConfirmRequest) => Promise<boolean>

export const ConfirmContext = createContext<{
  confirm: Confirm
  open: boolean
} | null>(null)

export function useConfirm() {
  const value = useContext(ConfirmContext)
  if (!value) throw new Error("useConfirm needs a ConfirmProvider above it.")
  return value
}

export function confirmScoreChange(
  confirm: Confirm,
  error: unknown
): Promise<boolean> {
  if (!isErrorBody(error) || error.kind !== "needs_confirmation") {
    return Promise.resolve(false)
  }
  return confirm({
    title: "Change grades you've already given?",
    description: error.message,
    action: `Change ${error.affected} ${error.affected === 1 ? "grade" : "grades"}`,
  })
}
