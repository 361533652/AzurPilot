import {expect, test, type Page} from '@playwright/test'

async function rpc(page: Page, method: string, params: Record<string, unknown>) {
  return page.evaluate(({method, params}) => new Promise<any>((resolve, reject) => {
    const socket = new WebSocket(`ws://${location.host}/api/v1/ws`)
    const timer = setTimeout(() => {socket.close(); reject(new Error('RPC timeout'))}, 30000)
    socket.onopen = () => socket.send(JSON.stringify({v: 1, type: 'request', id: 'tech-test', method, params}))
    socket.onmessage = event => {const value = JSON.parse(String(event.data)); if (value.id === 'tech-test') {clearTimeout(timer); socket.close(); value.ok ? resolve(value.result) : reject(new Error(value.error.message))}}
    socket.onerror = () => {clearTimeout(timer); reject(new Error('RPC connection failed'))}
  }), {method, params})
}
async function choose(page: Page, label: string, option: string) {
  await page.getByRole('combobox', {name: label, exact: true}).click()
  await page.getByRole('option', {name: option, exact: true}).click()
}
test.beforeEach(async ({page}) => {
  await page.addInitScript(() => {localStorage.setItem('azurpilot.theme', 'light'); localStorage.setItem('azurpilot.language', 'zh-CN')})
})

test('authoritative milestones distinguish unknown, never obtained and not currently held', async ({page}, info) => {
  const instance = `tech-facts-${info.testId.replace(/[^a-z0-9]/gi, '').slice(-12)}-${info.retry}`
  await page.goto('/'); await rpc(page, 'instances.create', {name: instance})
  await page.goto(`/#/i/${instance}/task/FleetAnalysis?tab=technology&group=10102`)
  const panel = page.getByRole('region', {name: '纠正科技记录 · 杜威', exact: true})
  await expect(panel).toBeVisible()
  await expect(page.getByRole('button', {name: '合并记录', exact: true})).toHaveCount(0)
  await expect(page.getByRole('button', {name: '舰船底账', exact: true})).toHaveCount(0)
  await choose(page, '杜威 获得', '已完成')
  await choose(page, '杜威 Lv.120', '已完成')
  await panel.getByRole('button', {name: '保存纠正', exact: true}).click()
  await expect(panel).toHaveCount(0)
  await page.getByLabel('搜索舰名', {exact: true}).fill('杜威')
  const row = page.locator('.ft-table-wrap tbody tr[data-group="10102"]')
  await expect(row).toContainText('未持有')
  await expect(row.getByRole('button', {name: '杜威 · 满星 · 未核实', exact: true})).toBeVisible()
  const report = await rpc(page, 'fleetTech.report', {instance})
  const dewey = report.ships.find((ship: any) => ship.group_type === 10102)
  expect([dewey.owned, dewey.full_star, dewey.lv120, dewey.current_count]).toEqual([true, null, true, 0])
  expect(dewey.status).toBe('unknown')
  await page.getByRole('button', {name: '未获得', exact: true}).click()
  await expect(row).toHaveCount(0)
  await page.getByRole('button', {name: '未获得', exact: true}).click()
  await page.getByRole('button', {name: '未持有', exact: true}).click()
  await expect(row).toHaveCount(1)
  await page.reload()
  await expect(panel).toBeVisible()
  await choose(page, '杜威 获得', '未完成')
  await expect(panel.getByText('本次科技点变化：-8', {exact: true})).toBeVisible()
  await expect(panel.getByRole('combobox', {name: '杜威 Lv.120'})).toContainText('未完成')
  await panel.getByRole('button', {name: '保存纠正', exact: true}).click()
  await expect(panel).toHaveCount(0)
  await page.reload()
  const corrected = await rpc(page, 'fleetTech.report', {instance})
  expect(corrected.ships.find((ship: any) => ship.group_type === 10102)).toMatchObject({owned: false, full_star: false, lv120: false, points: 0})
  await expect(page.getByRole('link', {name: '缺120级舰船加入养成计算', exact: true})).toHaveCount(0)
  await page.getByRole('tab', {name: '养成计算', exact: true}).click()
  await expect(page).toHaveURL(/tab=mind$/)
  await expect(page.getByText(/参与 0 艘/)).toBeVisible()
})

test('correction conflicts identify current copies and retirement preserves cumulative milestones and evidence', async ({page}, info) => {
  const instance = `tech-retire-${info.testId.replace(/[^a-z0-9]/gi, '').slice(-12)}-${info.retry}`
  await page.goto('/'); await rpc(page, 'instances.create', {name: instance})
  const empty = await rpc(page, 'dock.report', {instance})
  const dock = await rpc(page, 'dock.patch', {instance, revision: empty.revision, changes: [
    {name: '杜威', group_type: 10102, level: 120, rarity: 'R', base_rarity: 'R', extra_exp: 0},
  ]})
  const card = dock.ships[0]
  await page.goto(`/#/i/${instance}/task/FleetAnalysis?tab=technology&group=10102`)
  const panel = page.getByRole('region', {name: '纠正科技记录 · 杜威', exact: true})
  await expect(panel).toBeVisible()
  await choose(page, '杜威 Lv.120', '未完成')
  await panel.getByRole('button', {name: '保存纠正', exact: true}).click()
  await expect(page.getByRole('alert').first()).toContainText('科技纠错与当前舰船事实冲突')
  await expect(panel.getByRole('link', {name: '查看对应舰船', exact: true}).first()).toHaveAttribute('href', /tab=data&group=10102/)
  const before = await rpc(page, 'fleetTech.report', {instance})
  expect(before.ships.find((ship: any) => ship.group_type === 10102).lv120).toBe(true)
  await rpc(page, 'dock.patch', {instance, revision: dock.revision, changes: [{id: card.id, delete: true}]})
  await page.reload()
  await expect(panel).toBeVisible()
  await expect(panel.getByRole('combobox', {name: '杜威 Lv.120'})).toContainText('已完成')
  await expect(panel.locator(`a[href*="ship=${card.id}"]`)).toHaveCount(0)
  const retired = await rpc(page, 'fleetTech.report', {instance})
  const progress = retired.ships.find((ship: any) => ship.group_type === 10102)
  expect(progress).toMatchObject({owned: true, full_star: true, lv120: true, current_count: 0, points: 15})
  await choose(page, '杜威 Lv.120', '未完成')
  await panel.getByRole('button', {name: '保存纠正', exact: true}).click()
  await expect(panel).toHaveCount(0)
  await page.reload()
  await expect(panel.getByRole('combobox', {name: '杜威 Lv.120'})).toContainText('未完成')
  const after = await rpc(page, 'fleetTech.report', {instance})
  expect(after.ships.find((ship: any) => ship.group_type === 10102)).toMatchObject({lv120: false, points: 10})
})
