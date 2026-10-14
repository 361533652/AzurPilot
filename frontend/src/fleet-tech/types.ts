import type {Parameters} from '../api/generated'

export type Change = NonNullable<Parameters['fleetTech.patch']['changes']>[number]
export interface Technology {pt_get: number; pt_upgrage: number; pt_level: number; max_star: number; add_get_shiptype: number[]; add_get_attr: number; add_get_value: number; add_level_shiptype: number[]; add_level_attr: number; add_level_value: number}
export interface Ship {group_type: number; code: number; handbook_type: number; blueprint: boolean; name: string; names: Record<string, string | null>; nationality: number; rarity: string; type: number; technology: Technology | null}
export interface Catalog {ships: Ship[]; nations: Record<string, [number[], number, number][][]>; updated_at: string; source_commit: string; max_points: number; nation_points: Record<string, number>}
export interface Progress {group_type: number; owned: boolean | null; full_star: boolean | null; lv120: boolean | null; level: number | null; observed_level: number | null; status: 'complete' | 'unowned' | 'unfinished' | 'pending'; manual: boolean; points: number}
export interface Report {instance: string; revision: string; ships: Progress[]; nation_levels: Record<string, number | null>; bonuses: Record<string, Record<string, number>>; confirmed_points: number; completed: number; pending_star: number; min_level: number; max_level: number}
export type Row = Ship & Progress
export const shipType = (value: number) => [20, 21].includes(value) ? 1 : [22, 23, 24].includes(value) ? 22 : value
export function matches(row: Row, filters: Record<string, string[]>, query: string, scope: string, stage: string) {
  if (scope === 'tech' && !row.technology || scope === 'none' && row.technology) return false
  if (query && ![row.name, String(row.code).padStart(3, '0'), ...Object.values(row.names)].some(value => value?.toLocaleLowerCase().includes(query.toLocaleLowerCase()))) return false
  const type = shipType(row.type)
  const categories = [String(type), ...([1, 2, 3, 18].includes(type) ? ['front'] : [4, 5, 6, 7, 10, 12, 13].includes(type) ? ['back'] : [])]
  const statuses = [row.status, ...(row.owned ? ['owned'] : []), ...(row.status === 'unowned' || row.status === 'unfinished' ? ['incomplete'] : [])]
  for (const [key, values] of Object.entries(filters)) {
    if (!values.length || ['bonusType', 'attr'].includes(key)) continue
    const current = key === 'type' ? categories : key === 'status' ? statuses : [String(key === 'nation' ? row.nationality : row.rarity)]
    if (!values.some(value => current.includes(value))) return false
  }
  if (filters.bonusType?.length || filters.attr?.length) {
    const t = row.technology
    if (!t) return false
    const effects = [...(stage !== 'level' ? [[t.add_get_shiptype, t.add_get_attr] as const] : []), ...(stage !== 'get' ? [[t.add_level_shiptype, t.add_level_attr] as const] : [])]
    if (!effects.some(([types, attr]) => (!filters.bonusType?.length || types.some(value => filters.bonusType!.includes(String(shipType(value))))) && (!filters.attr?.length || filters.attr.includes(String(attr))))) return false
  }
  return true
}
