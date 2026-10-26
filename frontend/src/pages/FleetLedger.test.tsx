import {describe, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {MemoryRouter} from 'react-router-dom'
import type {DockReport} from '../api/types'
import {translateUi} from '../i18n'
import {FleetLedger} from './FleetLedger'
import {ScanUpdate, safeScanChanges} from './ScanUpdate'
vi.mock('../app/context', () => ({useConnection: () => 'ready', useApp: () => ({ui: (key: Parameters<typeof translateUi>[1]) => translateUi('zh-CN', key), instances: [], notify: vi.fn()})}))
const report: DockReport = {instance: 'test', revision: 'r1', updated_at: null, min_level: 1, max_level: 125, complete: false, review_count: 0, source: 'manual', pending_revision: 'p1', pending_changes: [], ships: [null, 0].map((extra_exp, index) => ({id: index + 1, name: '重复舰船', group_type: 100, level: 120, rarity: 'SSR', base_rarity: 'SSR', extra_exp, excluded: false, review: false, source: 'manual'}))}
function render(value = report, entry = '?tab=data') {return renderToStaticMarkup(<MemoryRouter initialEntries={[entry]}><FleetLedger instance="test" report={value} onReport={vi.fn()} onBusy={vi.fn()} onDirty={vi.fn()}/></MemoryRouter>)}
describe('current ships and scan separation', () => {
  it('shows duplicates as separate read-only rows and a species summary', () => {
    const html = render()
    expect(html).toContain('ship=1'); expect(html).toContain('ship=2')
    expect(html).toContain('舰船种类'); expect(html).toContain('批量选择')
    expect(html).not.toContain('保存舰船数据'); expect(html).not.toContain('待确认差异</h2>')
    const table = html.split('<tbody>')[1].split('</tbody>')[0]
    expect(table).not.toContain('<input'); expect(table).not.toContain('<select')
  })
  it('opens the exact duplicate through a shared details link, preserving unknown vs zero EXP', () => {
    expect(render(report, '?tab=data&ship=1')).toMatch(/type="number" min="0" readOnly=""[^>]*value=""/)
    expect(render(report, '?tab=data&ship=2')).toMatch(/type="number" min="0" readOnly=""[^>]*value="0"/)
    expect(render(report, '?tab=data&ship=2')).toContain('累计科技进度')
  })
  it('never accepts removal or duplicate matching in the safe batch', () => {
    const changes = (['add', 'update', 'remove', 'resolve'] as const).map(operation => ({id: operation, operation, kind: 'ship' as const, ship_id: null, group_type: 100, name: '重复舰船', before: null, after: {level: 120}, fields: ['level'], candidates: report.ships}))
    expect(safeScanChanges(changes).map(item => item.operation)).toEqual(['add', 'update'])
    const html = renderToStaticMarkup(<ScanUpdate instance="test" report={{...report, pending_changes: changes}} locked={false} onReport={vi.fn()} onBusy={vi.fn()}/>)
    expect(html).toContain('请选择目标卡片'); expect(html).toContain('新增独立卡片')
    expect(html).toContain('disabled="" aria-label="接受 resolve"')
    expect(html).toContain('请先处理已有待确认更新')
  })
})
