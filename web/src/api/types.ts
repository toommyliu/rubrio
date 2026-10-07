import type { components } from "./schema"

type Schemas = components["schemas"]

export type Course = Schemas["Course"]
export type Student = Schemas["Student"]
export type RosterChange = Schemas["RosterChange"]
export type ErrorBody = Schemas["ErrorBody"]
