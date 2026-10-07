import { useRef, useState } from "react"

import { api } from "@/api/client"
import { isErrorBody } from "@/api/errors"

import { ErrorText } from "@/components/page"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { useDebounced } from "@/hooks/use-debounced"
import { useFileDrop } from "@/hooks/use-file-drop"
import { type Accept, rejection } from "@/lib/accept"
import { cn } from "@/lib/utils"

const ASSIGNMENT_FILE: Accept = {
  extensions: [".md", ".markdown", ".txt"],
  kind: "an assignment file. Use a .md or .txt file",
}

export function AssignmentFileEditor({
  source,
  onChange,
}: {
  source: string
  onChange: (source: string) => void
}) {
  const textarea = useRef<HTMLTextAreaElement>(null)
  const picker = useRef<HTMLInputElement>(null)
  const [loaded, setLoaded] = useState<string | null>(null)
  const [rejected, setRejected] = useState<string | null>(null)
  const checked = useDebounced(source, 300)
  const check = api.useQuery(
    "post",
    "/api/check",
    { body: { source: checked } },
    { enabled: checked.trim() !== "", retry: false }
  )

  function goToLine(line: number) {
    const element = textarea.current
    if (!element) return
    const lines = source.split("\n")
    const start =
      lines.slice(0, line - 1).join("\n").length + (line > 1 ? 1 : 0)
    const end = start + (lines[line - 1]?.length ?? 0)
    element.focus()
    element.setSelectionRange(start, end)
  }

  async function load(file: File | undefined) {
    if (!file) return
    const problem = rejection([file], ASSIGNMENT_FILE)
    if (problem) {
      setRejected(
        file.name.toLowerCase().endsWith(".pdf")
          ? `${file.name} is a PDF. Drop the assignment file here, such as assignment.md. Templates go on the Templates page.`
          : problem
      )
      return
    }
    setRejected(null)
    setLoaded(file.name)
    onChange(await file.text())
  }

  const { dragging, handlers } = useFileDrop((files) => void load(files[0]))

  return (
    <div className="flex flex-col gap-3">
      <input
        ref={picker}
        type="file"
        hidden
        aria-label="Assignment file to load"
        accept=".md,.markdown,.txt,text/markdown,text/plain"
        onChange={(event) => {
          void load(event.target.files?.[0])
          event.target.value = ""
        }}
      />
      {source !== "" && (
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          {loaded && <span>Loaded {loaded}.</span>}
          <span>Drop another file on the box to replace it, or</span>
          <Button
            type="button"
            size="xs"
            variant="outline"
            onClick={() => picker.current?.click()}
          >
            Choose a file
          </Button>
        </div>
      )}
      <div className="relative" {...handlers}>
        <Textarea
          ref={textarea}
          aria-label="Assignment file"
          value={source}
          onChange={(event) => onChange(event.target.value)}
          spellCheck={false}
          className={cn(
            "min-h-96 font-mono text-xs",
            dragging && "border-primary bg-primary/5"
          )}
        />
        {source === "" && !dragging && (
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 border border-dashed text-sm text-muted-foreground">
            <span className="font-medium text-foreground">
              Drop assignment.md here
            </span>
            <span className="text-xs">or paste the file, or</span>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="pointer-events-auto"
              onClick={() => picker.current?.click()}
            >
              Choose a file
            </Button>
          </div>
        )}
        {dragging && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center border-2 border-dashed border-primary text-sm font-medium">
            {source === ""
              ? "Drop to load the file"
              : "Drop to replace the file"}
          </div>
        )}
      </div>
      {rejected && (
        <p role="alert" className="text-xs text-destructive">
          {rejected}
        </p>
      )}
      {check.data && (
        <ul className="flex flex-col gap-1 text-xs" aria-label="Versions">
          {check.data.map((version) => (
            <li key={version.name}>
              Version {version.name}: {version.questions}{" "}
              {version.questions === 1 ? "question" : "questions"},{" "}
              {version.points} points
              {version.bonus > 0 && ` + ${version.bonus} bonus`},{" "}
              {version.pages} {version.pages === 1 ? "page" : "pages"}
            </li>
          ))}
        </ul>
      )}
      {isErrorBody(check.error) && check.error.kind === "file" ? (
        <ul
          className="flex flex-col gap-1 text-xs text-destructive"
          aria-label="Problems"
        >
          {check.error.problems.map((problem) => (
            <li key={`${problem.line}:${problem.message}`}>
              <button
                type="button"
                className="text-left underline-offset-2 hover:underline"
                onClick={() => goToLine(problem.line)}
              >
                Line {problem.line}: {problem.message}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <ErrorText error={check.error} />
      )}
    </div>
  )
}
