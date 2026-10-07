export type Accept = { extensions: string[]; kind: string }

export const PDF: Accept = { extensions: [".pdf"], kind: "a PDF" }

export function rejection(files: File[], accept: Accept): string | null {
  const wrong = files.find(
    (file) =>
      !accept.extensions.some((extension) =>
        file.name.toLowerCase().endsWith(extension)
      )
  )
  return wrong ? `${wrong.name} isn't ${accept.kind}.` : null
}
