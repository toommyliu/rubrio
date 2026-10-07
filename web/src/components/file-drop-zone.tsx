import { useRef, useState } from "react"
import type { ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { useFileDrop } from "@/hooks/use-file-drop"
import { type Accept, rejection } from "@/lib/accept"
import { cn } from "@/lib/utils"

export function FileDropZone({
  prompt,
  inputLabel,
  accept,
  multiple = false,
  busy = false,
  status,
  onFiles,
}: {
  prompt: string
  inputLabel: string
  accept: Accept
  multiple?: boolean
  busy?: boolean
  status?: ReactNode
  onFiles: (files: File[]) => void
}) {
  const picker = useRef<HTMLInputElement>(null)
  const [rejected, setRejected] = useState<string | null>(null)

  function take(files: File[]) {
    if (files.length === 0 || busy) return
    const problem = rejection(files, accept)
    setRejected(problem)
    if (!problem) onFiles(multiple ? files : files.slice(0, 1))
  }

  const { dragging, handlers } = useFileDrop(take)
  return (
    <div className="flex flex-col gap-2">
      <div
        {...handlers}
        className={cn(
          "flex min-h-24 flex-col items-center justify-center gap-2 border border-dashed p-4 text-center text-sm text-muted-foreground transition-colors",
          dragging && "border-2 border-primary bg-primary/5 text-foreground",
          busy && "opacity-60"
        )}
      >
        {busy ? (
          (status ?? "Uploading…")
        ) : dragging ? (
          <span className="font-medium text-foreground">Drop to upload</span>
        ) : (
          <>
            <span className="font-medium text-foreground">{prompt}</span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => picker.current?.click()}
            >
              {multiple ? "Choose files" : "Choose a file"}
            </Button>
          </>
        )}
        <input
          ref={picker}
          type="file"
          hidden
          multiple={multiple}
          aria-label={inputLabel}
          accept={accept.extensions.join(",")}
          onChange={(event) => {
            take([...(event.target.files ?? [])])
            event.target.value = ""
          }}
        />
      </div>
      {rejected && (
        <p role="alert" className="text-xs text-destructive">
          {rejected}
        </p>
      )}
    </div>
  )
}
