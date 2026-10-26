import {describe, expect, it} from 'vitest'
import {automaticCandidates, highestCopies, readPlan} from './cultivation'
import type {DockCard} from '../api/types'
import type {Catalog, Progress} from '../fleet-tech/types'
const card = (id: number, level: number, extra: Partial<DockCard> = {}): DockCard => ({id, name: '企业', group_type: 10706, level, rarity: 'SSR', base_rarity: 'SSR', extra_exp: null, review: false, excluded: false, source: '', ...extra})
const catalog = {ships: [{group_type: 10706, technology: {}}, {group_type: 10102, technology: null}]} as Catalog
const progress = (lv120: boolean) => [{group_type: 10706, lv120}] as Progress[]
describe('automatic cultivation', () => {
  it('selects highest physical copy once, keeping unknown identities separate', () => {
    expect(highestCopies([card(1, 100), card(2, 110), card(3, 0, {review: true}), card(4, 0, {review: true})])).toEqual([2, 3, 4])
    expect(automaticCandidates([card(1, 100), card(2, 110)], catalog, progress(false)).map(row => row.id)).toEqual([2])
  })
  it('retains cumulative completion after the level120 copy retires', () => {
    expect(automaticCandidates([card(1, 100)], catalog, progress(true))).toEqual([])
    expect(automaticCandidates([card(1, 100), card(2, 120)], catalog, progress(false))).toEqual([])
  })
  it('uses incomplete milestones without changing them and omits unsupported identities', () => {
    const facts = progress(false), before = JSON.stringify(facts)
    expect(automaticCandidates([card(1, 100), card(2, 119, {review: true}), card(3, 100, {group_type: 10102}), card(4, 0, {group_type: null})], catalog, facts).map(row => row.id)).toEqual([1])
    expect(JSON.stringify(facts)).toBe(before)
  })
  it('replaces the chosen copy automatically when a new higher copy appears', () => {
    const first = [card(1, 100)], updated = [...first, card(2, 110)]
    expect(automaticCandidates(first, catalog, progress(false)).map(row => row.id)).toEqual([1])
    expect(automaticCandidates(updated, catalog, progress(false)).map(row => row.id)).toEqual([2])
    expect(automaticCandidates([...updated, card(3, 120)], catalog, progress(true))).toEqual([])
  })
  it('stores exclusions by group so they survive replacement of the highest copy', () => {
    const plan = readPlan(JSON.stringify({excluded_groups: [10706, 10706, 99], assumptions: []}), [card(2, 110)])
    expect(plan.excluded_groups).toEqual([10706])
    expect(readPlan(null, [card(1, 100, {excluded: true}), card(2, 110)]).excluded_groups).toEqual([])
    expect(readPlan(null, [card(2, 110, {excluded: true})]).excluded_groups).toEqual([10706])
  })
  it('ignores old manual selection plans instead of freezing the automatic range', () => {
    expect(readPlan(JSON.stringify({target: 125, ids: [], assumptions: []}), [card(1, 100)])).toEqual({excluded_groups: []})
    expect(readPlan('{', [card(1, 100)])).toEqual({excluded_groups: []})
  })
  it('discards legacy trial levels and awakening caps while preserving exclusions', () => {
    const raw = JSON.stringify({excluded_groups: [10706], assumptions: [{ship_id: 1, level: 119, awakened_to: 120}]})
    expect(readPlan(raw, [card(1, 100)])).toEqual({excluded_groups: [10706]})
  })
})
