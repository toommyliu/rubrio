export function CodeText({ text }: { text: string }) {
  return text.split("`").map((part, position) =>
    position % 2 === 1 ? (
      <code key={position} className="bg-background px-1 font-mono">
        {part}
      </code>
    ) : (
      <span key={position}>{part}</span>
    )
  )
}
