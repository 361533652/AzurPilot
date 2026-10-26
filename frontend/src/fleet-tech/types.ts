export interface Technology {pt_get: number; pt_upgrage: number; pt_level: number; max_star: number; add_get_shiptype: number[]; add_get_attr: number; add_get_value: number; add_level_shiptype: number[]; add_level_attr: number; add_level_value: number}
export interface Ship {group_type: number; code: number; handbook_type: number; blueprint: boolean; name: string; names: Record<string, string | null>; nationality: number; rarity: string; type: number; variant?: 'child' | 'mu' | ''; technology: Technology | null}
export interface Catalog {ships: Ship[]; nations: Record<string, [number[], number, number][][]>; updated_at: string; source_commit: string; max_points: number; nation_points: Record<string, number>}
export type Milestone = 'owned' | 'full_star' | 'lv120'
export type State = boolean | null
export interface Change {group_type: number; owned?: State; full_star?: State; lv120?: State}
export interface HistoricalEvidence {ship_id: number; level: number; breakthrough: number | null; source: string; confirmed_at: string}
export interface Progress {historical_evidence?: Partial<Record<Milestone, HistoricalEvidence[]>>; conflicts?: Partial<Record<Milestone, number[]>>;source?: string; updated_at?: string; group_type: number; owned: State; full_star: State; lv120: State; current_count: number; current_level: number | null; evidence: Record<Milestone, number[]>; points: number; status: 'complete' | 'unowned' | 'unfinished' | 'unknown'}
export type Bonuses = Record<string, Record<string, number>>
export interface Report {instance: string; revision: string; ships: Progress[]; nation_levels: Record<string, number | null>; bonuses: Bonuses; ship_bonuses: Bonuses; nation_bonuses: Bonuses; confirmed_points: number; completed: number; milestone_counts: Record<Milestone, number>; unknown_count: number; unknown_ships: number}
export type Row = Ship & Progress
export const milestones: Milestone[] = ['owned', 'full_star', 'lv120']
export const shipType = (value: number) => [20, 21].includes(value) ? 1 : [22, 23, 24].includes(value) ? 22 : value
/** Missing account facts stay unknown, independently of current holdings. */
export function catalogRows(catalog: Catalog, progress: Progress[]): Row[] {
  const facts = new Map(progress.map(row => [row.group_type, row]))
  return catalog.ships.map(ship => ({...ship, ...(facts.get(ship.group_type) ?? {
    group_type: ship.group_type, owned: null, full_star: null, lv120: null, current_count: 0,
    current_level: null, evidence: {owned: [], full_star: [], lv120: []}, points: 0, status: 'unknown' as const,
  })}))
}
export function complete(row: Row) {return milestones.every(key => row[key] === true)}
export function remainingPoints(row: Row) {
  const t = row.technology
  return t ? t.pt_get + t.pt_upgrage + t.pt_level - row.points : 0
}
export function matches(row: Row, filters: Record<string, string[]>, query: string, scope = 'all', stage = 'all') {
  if (scope === 'tech' && !row.technology || scope === 'none' && row.technology) return false
  if (query && ![row.name, ...Object.values(row.names)].some(value => value?.toLocaleLowerCase().includes(query.toLocaleLowerCase()))) return false
  const statuses = [
    ...(!row.current_count ? ['notHeld'] : ['held']),
    ...(row.owned === false ? ['unowned'] : []),
    ...(row.full_star === false ? ['missingStar'] : []),
    ...(row.lv120 === false ? ['missing120'] : []),
    ...(milestones.some(key => row[key] === null) ? ['unverified'] : []),
    ...(row.technology && !complete(row) ? ['incomplete'] : []),
    ...(row.technology && complete(row) ? ['complete'] : []),
  ]
  for (const [key, values] of Object.entries(filters)) {
    if (!values.length || ['bonusType', 'attr'].includes(key)) continue
    const current = key === 'type' ? [String(shipType(row.type))] : key === 'status' ? statuses : key === 'nation' ? [String(row.nationality)] : [row.rarity]
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
/** Mirror the server's milestone dependency rules in the explicit correction form. */
export function changeMilestone(change: Change, key: Milestone, value: State): Change {
  if (key === 'owned' && value !== true) return {...change, owned: value, full_star: value, lv120: value}
  if (key !== 'owned' && value === true) return {...change, [key]: value, owned: true}
  return {...change, [key]: value}
}
