export function pointsLabel({
  points,
  bonus,
}: {
  points: number
  bonus: boolean
}): string {
  return `${points} ${bonus ? "bonus " : ""}${points === 1 ? "point" : "points"}`
}

export function formatPoints(points: number): string {
  return points > 0 ? `+${points}` : String(points)
}
