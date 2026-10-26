import {describe, expect, it} from 'vitest'
import {highestCopies, readPlan} from './cultivation'
import type {DockCard} from '../api/types'
const card = (id: number, level: number, extra: Partial<DockCard> = {}): DockCard => ({id, name: '企业', group_type: 10706, level, rarity: 'SSR', base_rarity: 'SSR', extra_exp: null, review: false, excluded: false, source: '', ...extra})
describe('cultivation selection', () => {
  it('selects concrete highest copy IDs, keeping unknown identities separate', () => {
    expect(highestCopies([card(1, 100), card(2, 120), card(3, 0, {review: true}), card(4, 0, {review: true})])).toEqual([2, 3, 4])
  })
  it('inherits legacy exclusions once and retains later explicit selections', () => {
    const cards = [card(1, 100, {excluded: true}), card(2, 120)]
    expect(readPlan(null, cards).ids).toEqual([2])
    expect(readPlan(JSON.stringify({target: 125, ids: [1, 1, 99], assumptions: []}), cards).ids).toEqual([1])
  })
  it('deduplicates restored per-copy assumptions', () => {
    const plan = readPlan(JSON.stringify({target: 120, ids: [1], assumptions: [{ship_id: 1, level: 105}, {ship_id: 1, level: 110}]}), [card(1, 100)])
    expect(plan.assumptions).toEqual([{ship_id: 1, level: 110}])
  })
  it('ignores corrupted plans and invalid assumptions', () => {
    const cards = [card(1, 100)]
    expect(readPlan('{', cards).target).toBe(120)
    expect(readPlan(JSON.stringify({target: 120, ids: [1], assumptions: [{ship_id: 1, level: 999}]}), cards).assumptions).toEqual([])
  })
})
