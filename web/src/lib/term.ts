export const SEASONS = ["Winter", "Spring", "Summer", "Fall"] as const
export type Season = (typeof SEASONS)[number]

export function currentSeason(date: Date): Season {
  const month = date.getMonth()
  return month <= 4 ? "Spring" : month <= 6 ? "Summer" : "Fall"
}
