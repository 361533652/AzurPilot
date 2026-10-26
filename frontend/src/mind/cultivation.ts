import type {DockCard} from '../api/types'
export interface CultivationRow {
  id: number; name: string; current_level: number; level: number; target_level: number
  extra_exp: number | null; hypothetical: boolean; experience: number | null; experience_upper: number | null
  mind: number | null; mind2: number | null; gold: number | null
  status: 'exact' | 'estimated' | 'unknown'; notes: string[]; stages: number[]
}
export interface CultivationReport {
  instance: string; revision: string; ships: CultivationRow[]; target_level: number
  included: number; unknown: number; estimated: number; experience_upper: number
  mind: number; mind2: number; gold: number; rules_sha256: string
}
export interface Assumption {ship_id: number; level?: number; extra_exp?: number | null; awakened_to?: 100 | 105 | 110 | 115 | 120 | 125 | null}
export interface CultivationPlan {target: 120 | 125; ids: number[]; assumptions: Assumption[]}
export function highestCopies(cards: DockCard[]): number[] {
  const groups = new Map<string, DockCard>()
  for (const card of cards) {
    const key = card.review || !card.group_type ? `copy:${card.id}` : `group:${card.group_type}`
    const previous = groups.get(key)
    if (!previous || card.level > previous.level || card.level === previous.level && card.id < previous.id) groups.set(key, card)
  }
  return [...groups.values()].map(card => card.id)
}
export function readPlan(raw: string | null, cards: DockCard[]): CultivationPlan {
  const known = new Set(cards.map(card => card.id))
  try {
    const value = JSON.parse(raw ?? 'null') as CultivationPlan
    if (value && [120, 125].includes(value.target) && Array.isArray(value.ids) && Array.isArray(value.assumptions)) {
      const ids = [...new Set(value.ids.filter(id => Number.isInteger(id) && known.has(id)))]
      const assumptions = value.assumptions.filter(row => row && ids.includes(row.ship_id)
        && (row.level === undefined || Number.isInteger(row.level) && row.level >= 1 && row.level <= 125)
        && (row.extra_exp == null || Number.isSafeInteger(row.extra_exp) && row.extra_exp >= 0)
        && (row.awakened_to == null || [100, 105, 110, 115, 120, 125].includes(row.awakened_to)))
      return {target: value.target, ids, assumptions: [...new Map(assumptions.map(row => [row.ship_id, row])).values()]}
    }
  } catch { /* Damaged browser preferences must never change confirmed data. */ }
  return {target: 120, ids: highestCopies(cards.filter(card => !card.excluded)), assumptions: []}
}
