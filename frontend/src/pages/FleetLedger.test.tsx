import {describe, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {MemoryRouter} from 'react-router-dom'
import type {DockReport} from '../api/types'
import {translateUi} from '../i18n'
import {FleetLedger} from './FleetLedger'
import {ScanUpdate, safeScanChanges} from './ScanUpdate'
vi.mock('../app/context', () => ({useConnection: () => 'ready', useApp: () => ({ui: (key: Parameters<typeof translateUi>[1]) => translateUi('zh-CN', key), instances: [], notify: vi.fn()})}))
const report: DockReport = {instance: 'test', revision: 'r1', updated_at: null, min_level: 1, max_level: 125, complete: false, review_count: 0, source: 'manual', pending_revision: 'p1', pending_changes: [], ships: [1, 2].map((id) => ({id, name: '重复舰船', group_type: 100, level: 120, rarity: 'SSR', base_rarity: 'SSR', extra_exp: null, review: false, source: 'manual'}))}
function render(value: DockReport | undefined, entry = '?tab=data') {return renderToStaticMarkup(<MemoryRouter initialEntries={[entry]}><FleetLedger instance="test" report={value} onReport={vi.fn()} onBusy={vi.fn()}/></MemoryRouter>)}
describe('current ships and scan separation', () => {
  it('shows the loading boundary until the catalog resolves, even with confirmed ships', () => {
    // Static rendering does not run the effect that fetches the ship catalog.
    const html = render(report)
    expect(html).toContain('class="loading" role="status"')
    expect(html).toContain(translateUi('zh-CN', 'common.loading'))
    expect(html).not.toContain('<table')
    expect(html).not.toContain('>保存</button>')
    expect(html).not.toContain('待确认差异</h2>')
  })
  it('keeps a duplicate deep link at the loading boundary while ship data is pending', () => {
    const html = render(undefined, '?tab=data&ship=2')
    expect(html).toContain('class="loading" role="status"')
    expect(html).not.toContain('ledger-ship-2')
    expect(html).not.toContain('role="dialog"')
    expect(html).not.toContain('舰船数据已保存')
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
