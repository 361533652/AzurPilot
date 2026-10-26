import {describe, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {MemoryRouter, Route, Routes} from 'react-router-dom'
import {translateUi} from '../i18n'
import {FleetAnalysis} from './FleetAnalysis'

vi.mock('../app/context', () => ({useConnection: () => 'ready', useApp: () => ({ui: (key: Parameters<typeof translateUi>[1]) => translateUi('zh-CN', key), instances: [], notify: vi.fn()})}))
vi.mock('./MindCalculator', () => ({MindCalculator: () => <p>mind content</p>}))
vi.mock('./FleetTechnology', () => ({FleetTechnology: () => <p>technology content</p>}))
vi.mock('./FleetLedger', () => ({FleetLedger: () => <p>data content</p>}))
vi.mock('./ScanUpdate', () => ({ScanUpdate: () => <p>scan content</p>}))
describe('ship data navigation', () => {
  it.each(['data', 'technology', 'mind', 'scan'])('mounts only the active %s page', tab => {
    const html = renderToStaticMarkup(<MemoryRouter initialEntries={[`/i/test/task/FleetAnalysis?tab=${tab}`]}><Routes><Route path="/i/:instance/task/FleetAnalysis" element={<FleetAnalysis/>}/></Routes></MemoryRouter>)
    expect(html.match(/role="tab"/g)).toHaveLength(4)
    expect(html.match(/role="tabpanel"/g)).toHaveLength(1)
    expect(html).toContain(`${tab} content`)
    for (const other of ['data', 'technology', 'mind', 'scan'].filter(value => value !== tab)) expect(html).not.toContain(`${other} content`)
  })
})
