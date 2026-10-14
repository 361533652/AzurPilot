import {describe, expect, it} from 'vitest'
import {matches, type Row} from './types'

const row: Row = {group_type: 10102, code: 4, handbook_type: 0, blueprint: false, name: '杜威', names: {cn: '杜威', en: 'Dewey'}, nationality: 1, rarity: 'R', type: 20,
  owned: true, full_star: null, lv120: true, level: 125, observed_level: 125, status: 'complete', manual: false, points: 8,
  technology: {pt_get: 3, pt_upgrage: 7, pt_level: 5, max_star: 5, add_get_shiptype: [1, 20, 21], add_get_attr: 1, add_get_value: 1, add_level_shiptype: [7], add_level_attr: 5, add_level_value: 1}}
describe('科技联合筛选', () => {
  it('组内并集、组间交集，并支持编号和外文名称搜索', () => {
    expect(matches(row, {type: ['1', '2'], nation: ['1'], status: ['complete']}, '004', 'tech', 'all')).toBe(true)
    expect(matches(row, {nation: ['2']}, '', 'all', 'all')).toBe(false)
    expect(matches(row, {}, 'dewey', 'all', 'all')).toBe(true)
  })
  it('舰种和属性必须命中同一阶段的加成', () => {
    expect(matches(row, {bonusType: ['1'], attr: ['5']}, '', 'all', 'all')).toBe(false)
    expect(matches(row, {bonusType: ['1'], attr: ['1']}, '', 'all', 'get')).toBe(true)
    expect(matches(row, {bonusType: ['1'], attr: ['1']}, '', 'all', 'level')).toBe(false)
  })
  it('未知不会被当成未获得，无科技仍可按身份筛选', () => {
    const unknown = {...row, technology: null, owned: null, status: 'pending' as const}
    expect(matches(unknown, {status: ['unowned']}, '', 'all', 'all')).toBe(false)
    expect(matches(unknown, {status: ['pending']}, '', 'none', 'all')).toBe(true)
    expect(matches(unknown, {attr: ['1']}, '', 'none', 'all')).toBe(false)
  })
})
