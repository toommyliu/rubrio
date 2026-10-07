import { useRef, useState } from "react"
import type { DragEvent } from "react"

export function useFileDrop(onFiles: (files: File[]) => void) {
  const depth = useRef(0)
  const [dragging, setDragging] = useState(false)
  const handlers = {
    onDragEnter: (event: DragEvent<HTMLElement>) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      depth.current += 1
      setDragging(true)
    },
    onDragOver: (event: DragEvent<HTMLElement>) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      event.dataTransfer.dropEffect = "copy"
    },
    onDragLeave: () => {
      depth.current = Math.max(0, depth.current - 1)
      if (depth.current === 0) setDragging(false)
    },
    onDrop: (event: DragEvent<HTMLElement>) => {
      if (!hasFiles(event)) return
      event.preventDefault()
      depth.current = 0
      setDragging(false)
      onFiles([...event.dataTransfer.files])
    },
  }
  return { dragging, handlers }
}

function hasFiles(event: DragEvent<HTMLElement>): boolean {
  return event.dataTransfer.types.includes("Files")
}
