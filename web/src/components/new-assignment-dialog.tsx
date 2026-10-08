import { Link } from "@tanstack/react-router"
import type { ReactNode } from "react"

import { Button, buttonVariants } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"

export function NewAssignmentDialog({ course }: { course: string }) {
  return (
    <Dialog>
      <DialogTrigger render={<Button />}>New assignment</DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>New assignment</DialogTitle>
          <DialogDescription>Choose how to start.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 sm:grid-cols-2 sm:gap-0 sm:divide-x">
          <Choice
            title="Start from a PDF"
            description="Upload the template you'll print, such as a PDF exported from Word or Google Docs. Rubrio finds the questions and their points, and you fill in anything it missed."
            action={
              <Link
                to="/courses/$course/new/pdf"
                params={{ course }}
                className={buttonVariants()}
              >
                Upload a PDF
              </Link>
            }
          />
          <Choice
            title="Write an assignment file"
            description="Write the questions, answer keys and rubrics in markdown."
            action={
              <Link
                to="/courses/$course/new"
                params={{ course }}
                className={buttonVariants({ variant: "outline" })}
              >
                Write a file
              </Link>
            }
          />
        </div>
      </DialogContent>
    </Dialog>
  )
}

function Choice({
  title,
  description,
  action,
}: {
  title: string
  description: string
  action: ReactNode
}) {
  return (
    <section className="flex flex-col gap-3 sm:px-4 sm:first:pl-0 sm:last:pr-0">
      <h3 className="text-sm font-medium">{title}</h3>
      <p className="flex-1 text-muted-foreground">{description}</p>
      <div>{action}</div>
    </section>
  )
}
