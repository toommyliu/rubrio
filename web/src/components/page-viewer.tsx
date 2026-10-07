import { useHotkeys } from "@tanstack/react-hotkeys"
import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"

export type ViewedPage = { id: number; title: string; detail: string }

export function PageViewer({
  pages,
  index,
  onIndexChange,
}: {
  pages: ViewedPage[]
  index: number | null
  onIndexChange: (index: number | null) => void
}) {
  const [popup, setPopup] = useState<HTMLElement | null>(null)
  const page = index === null ? undefined : pages[index]
  const prev = index !== null && index > 0 ? index - 1 : null
  const next = index !== null && index < pages.length - 1 ? index + 1 : null
  function go(target: number | null) {
    if (target === null) return
    onIndexChange(target)
    popup?.focus()
  }

  const keys = [
    { hotkey: "ArrowLeft" as const, callback: () => go(prev) },
    { hotkey: "ArrowRight" as const, callback: () => go(next) },
  ]
  const open = page !== undefined
  useHotkeys(keys, { enabled: open, target: popup })
  useHotkeys(keys, { enabled: open, conflictBehavior: "allow" })
  return (
    <Dialog
      open={open}
      onOpenChange={(opened) => {
        if (!opened) onIndexChange(null)
      }}
    >
      <DialogContent
        ref={setPopup}
        className="flex max-h-[calc(100svh-2rem)] flex-col gap-3 sm:max-w-4xl"
      >
        {page && (
          <>
            <div className="flex items-center gap-2 pr-8">
              <Button
                size="icon-sm"
                variant="outline"
                aria-label="Previous page"
                disabled={prev === null}
                onClick={() => go(prev)}
              >
                <ChevronLeftIcon />
              </Button>
              <Button
                size="icon-sm"
                variant="outline"
                aria-label="Next page"
                disabled={next === null}
                onClick={() => go(next)}
              >
                <ChevronRightIcon />
              </Button>
              <DialogTitle className="text-sm">{page.title}</DialogTitle>
              <DialogDescription className="text-xs">
                {page.detail} · {(index ?? 0) + 1} of {pages.length}
              </DialogDescription>
            </div>
            <div className="flex justify-center bg-muted p-4">
              <img
                src={`/api/scan-pages/${page.id}/image`}
                alt={`${page.title}, ${page.detail}`}
                className="block h-auto max-h-[calc(100svh-9rem)] w-auto max-w-full bg-white shadow-md ring-1 ring-foreground/15"
              />
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
