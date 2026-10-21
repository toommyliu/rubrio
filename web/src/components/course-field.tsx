import { useState } from "react"

import { api } from "@/api/client"
import type { components } from "@/api/schema"
import { ErrorText, Loading } from "@/components/page"
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

export type CourseValue = components["schemas"]["NewAssignment"]["course"]

export type CourseDraft = {
  selection: string
  name: string
  season: Season
  year: number
}

export function newCourseDraft(course = ""): CourseDraft {
  const today = new Date()
  return {
    selection: course,
    name: "",
    season: currentSeason(today),
    year: today.getFullYear(),
  }
}

export function courseValue(draft: CourseDraft): CourseValue | null {
  if (draft.selection) return draft.selection
  return draft.name.trim()
    ? { name: draft.name, term: `${draft.season} ${draft.year}` }
    : null
}

export function CourseField({
  value,
  onChange,
}: {
  value: CourseDraft
  onChange: (value: CourseDraft) => void
}) {
  const courses = api.useQuery("get", "/api/courses")
  const { selection, name, season, year } = value
  const [thisYear] = useState(() => new Date().getFullYear())
  const years = [-1, 0, 1].map((offset) => thisYear + offset)
  const selected = courses.data?.find((course) => course.slug === selection)

  return (
    <div className="flex flex-col gap-4">
      {courses.isPending && <Loading what="courses" />}
      <ErrorText error={courses.error} />
      {courses.data && courses.data.length > 0 && (
        <Field>
          <FieldLabel htmlFor="course">Course</FieldLabel>
          <Select
            value={selection}
            onValueChange={(next) =>
              next !== null && onChange({ ...value, selection: next })
            }
          >
            <SelectTrigger id="course" className="w-full">
              <SelectValue>
                {selected
                  ? `${selected.name} (${selected.term})`
                  : "New course"}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {courses.data.map((course) => (
                <SelectItem key={course.slug} value={course.slug}>
                  {course.name} ({course.term})
                </SelectItem>
              ))}
              <SelectItem value="">New course</SelectItem>
            </SelectContent>
          </Select>
        </Field>
      )}
      {courses.data && selection === "" && (
        <FieldGroup className="grid gap-3 sm:grid-cols-3">
          <Field>
            <FieldLabel htmlFor="course-name">Course name</FieldLabel>
            <Input
              id="course-name"
              placeholder="CS 101"
              value={name}
              onChange={(event) =>
                onChange({ ...value, name: event.target.value })
              }
              required
            />
          </Field>
          <Field>
            <FieldLabel htmlFor="course-season">Season</FieldLabel>
            <Select
              value={season}
              onValueChange={(next) =>
                next && onChange({ ...value, season: next })
              }
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
              onValueChange={(next) =>
                next && onChange({ ...value, year: next })
              }
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
      )}
    </div>
  )
}
