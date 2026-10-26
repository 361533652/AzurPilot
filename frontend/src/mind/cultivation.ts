import type {DockCard} from '../api/types'
import type {Catalog, Progress} from '../fleet-tech/types'
export interface CultivationRow {
  id: number; name: string; current_level: number; level: number; target_level: number
  hypothetical: boolean; mind: number | null; mind2: number | null; gold: number | null
  status: 'exact' | 'estimated' | 'unknown'; notes: string[]; stages: number[]
}
export interface CultivationReport {
  instance: string; revision: string; ships: CultivationRow[]; target_level: number
  included: number; unknown: number; estimated: number
  mind: number; mind2: number; gold: number; rules_sha256: string
}
export interface Assumption {ship_id: number; level?: number; awakened_to?: 100 | 105 | 110 | 115 | 120 | 125 | null}
export interface CultivationPlan {excluded_groups: number[]; assumptions: Assumption[]}
export function highestCopies(cards: DockCard[]): number[] {
  const groups = new Map<string, DockCard>()
  for (const card of cards) {
    const key = card.review || !card.group_type ? `copy:${card.id}` : `group:${card.group_type}`
    const previous = groups.get(key)
    if (!previous || card.level > previous.level || card.level === previous.level && card.id < previous.id) groups.set(key, card)
  }
  return [...groups.values()].map(card => card.id)
}
/** Account achievements survive retirement; an unknown achievement remains an estimate. */
export function automaticCandidates(cards: DockCard[], catalog: Catalog, progress: Progress[]): DockCard[] {
  const supported = new Set(catalog.ships.filter(row => row.technology).map(row => row.group_type))
  const completed = new Set(progress.filter(row => row.lv120 === true).map(row => row.group_type))
  const confirmed = cards.filter(card => !card.review && card.group_type && supported.has(card.group_type))
  const highest = new Set(highestCopies(confirmed))
  return confirmed.filter(card => highest.has(card.id) && card.level < 120 && !completed.has(card.group_type!))
}
export function readPlan(raw: string | null, cards: DockCard[]): CultivationPlan {
  const known = new Map(cards.map(card => [card.id, card])), groups = new Set(cards.map(card => card.group_type))
  try {
    const value = JSON.parse(raw ?? 'null') as CultivationPlan
    if (value && Array.isArray(value.excluded_groups) && Array.isArray(value.assumptions)) {
      const excluded_groups = [...new Set(value.excluded_groups.filter(id => Number.isInteger(id) && groups.has(id)))]
      const assumptions = value.assumptions.filter(row => row && known.has(row.ship_id)
        && (row.level === undefined || Number.isInteger(row.level) && row.level >= 1 && row.level <= 125)
        && (row.awakened_to == null || [100, 105, 110, 115, 120, 125].includes(row.awakened_to)))
        .map(({ship_id, level, awakened_to}) => {
          const actualLevel = level ?? known.get(ship_id)!.level
          const inferred = [100, 105, 110, 115, 120, 125].find(value => value >= actualLevel) ?? 125
          const maximum = Math.min(125, inferred + (actualLevel === inferred ? 5 : 0))
          return {ship_id, ...(level === undefined ? {} : {level}),
            ...(awakened_to != null && inferred <= awakened_to && awakened_to <= maximum ? {awakened_to} : {})}
        }).filter(row => Object.keys(row).length > 1)
      return {excluded_groups, assumptions: [...new Map(assumptions.map(row => [row.ship_id, row])).values()]}
    }
  } catch { /* Damaged preferences must never change confirmed data. */ }
  const highest = new Set(highestCopies(cards))
  return {excluded_groups: cards.filter(card => highest.has(card.id) && card.excluded && card.group_type).map(card => card.group_type!), assumptions: []}
}
