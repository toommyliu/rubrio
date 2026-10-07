import type { components } from "./schema"

type Schemas = components["schemas"]

export type Course = Schemas["Course"]
export type Student = Schemas["Student"]
export type RosterChange = Schemas["RosterChange"]
export type AssignmentInfo = Schemas["AssignmentInfo"]
export type VersionSummary = Schemas["VersionSummary"]
export type Problem = Schemas["Problem"]
export type QuestionInfo = Schemas["QuestionInfo"]
export type RubricItem = Schemas["RubricItem"]
export type ErrorBody = Schemas["ErrorBody"]
