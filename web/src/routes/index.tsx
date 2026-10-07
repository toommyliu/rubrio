import { useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { useState } from "react"

import { api } from "@/api/client"
import { ErrorText, Loading, PageTitle, Section } from "@/components/page"
import { Button } from "@/components/ui/button"
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { currentSeason, SEASONS, type Season } from "@/lib/term"

export const Route = createFileRoute("/")({
  component: Home,
})

function Home() {
  const courses = api.useQuery("get", "/api/courses")
  return (
    <div className="flex max-w-2xl flex-col gap-8">
      <PageTitle
        title="Courses"
        description="Each course has a roster and its assignments."
      />
      {courses.isPending && <Loading what="courses" />}
      <ErrorText error={courses.error} />
      {courses.data && courses.data.length > 0 && (
        <ul className="flex flex-col border-t">
          {courses.data.map((course) => (
            <li key={course.id} className="border-b">
              <Link
                to="/courses/$course"
                params={{ course: course.slug }}
                className="flex items-baseline justify-between gap-4 py-2 text-sm hover:bg-muted"
              >
                <span className="font-medium">{course.name}</span>
                <span className="text-xs text-muted-foreground">
                  {course.term} · {course.slug}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <NewCourse />
    </div>
  )
}

function NewCourse() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [name, setName] = useState("")
  const [today] = useState(() => new Date())
  const [season, setSeason] = useState<Season>(() => currentSeason(today))
  const [year, setYear] = useState(() => today.getFullYear())
  const years = [-1, 0, 1].map((offset) => today.getFullYear() + offset)
  const term = `${season} ${year}`
  const create = api.useMutation("post", "/api/courses", {
    onSuccess: async (course) => {
      await queryClient.invalidateQueries()
      await navigate({
        to: "/courses/$course",
        params: { course: course.slug },
      })
    },
  })
  return (
    <Section title="New course">
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault()
          create.mutate({ body: { name, term } })
        }}
      >
        <FieldGroup className="grid grid-cols-3 gap-3">
          <Field>
            <FieldLabel htmlFor="course-name">Name</FieldLabel>
            <Input
              id="course-name"
              placeholder="CS 101"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="course-season">Season</FieldLabel>
            <Select
              value={season}
              onValueChange={(value) => value && setSeason(value)}
            >
              <SelectTrigger id="course-season" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SEASONS.map((option) => (
                  <SelectItem key={option} value={option}>
                    {option}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field>
            <FieldLabel htmlFor="course-year">Year</FieldLabel>
            <Select
              value={year}
              onValueChange={(value) => value && setYear(value)}
            >
              <SelectTrigger id="course-year" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {years.map((option) => (
                  <SelectItem key={option} value={option}>
                    {option}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </FieldGroup>
        <ErrorText error={create.error} />
        <div>
          <Button type="submit" disabled={create.isPending}>
            Create course
          </Button>
        </div>
      </form>
    </Section>
  )
}
