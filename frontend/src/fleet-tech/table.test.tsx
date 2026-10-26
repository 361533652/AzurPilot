import {describe, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {MemoryRouter} from 'react-router-dom'
import {TechnologyTable} from '../pages/FleetTechnology'
import {translateFleetTech} from './i18n'
import type {Row} from './types'
const row: Row = {group_type: 100, code: 1, handbook_type: 0, blueprint: false, name: 'Enterprise', names: {}, nationality: 1, rarity: 'SSR', type: 7, owned: true, full_star: null, lv120: false, current_count: 0, current_level: null, evidence: {owned: [], full_star: [], lv120: []}, points: 3, status: 'unknown', technology: {pt_get: 3, pt_upgrage: 7, pt_level: 5, max_star: 5, add_get_shiptype: [7], add_get_attr: 5, add_get_value: 1, add_level_shiptype: [7], add_level_attr: 5, add_level_value: 2}}
function render(value: Row) {return renderToStaticMarkup(<MemoryRouter><TechnologyTable rows={[value]} language="en-US" tr={key => translateFleetTech('en-US', key)} onSelect={vi.fn()}/></MemoryRouter>)}
describe('technology catalog table', () => {
  it('renders three distinct milestone states independently of no current holdings', () => {
    const html = render(row)
    expect(html).toContain('Not currently held')
    expect(html).toContain('Enterprise · Obtain · Completed')
    expect(html).toContain('Enterprise · Max stars · Unverified')
    expect(html).toContain('Enterprise · Lv.120 · Not completed')
    expect(html).toContain('3 / 15')
    expect(html).toContain('12 Tech points')
    expect(html).not.toContain('type="number"')
  })
  it('shows actual copy count and highest level with a current-ships link', () => {
    const html = render({...row, current_count: 2, current_level: 125})
    expect(html).toContain('2 · Lv.125')
    expect(html).toContain('?tab=data&amp;group=100')
  })
})
