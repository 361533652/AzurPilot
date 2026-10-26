import {expect, test, type Page} from '@playwright/test'

async function rpc(page: Page, method: string, params: Record<string, unknown>) {
  return page.evaluate(({method, params}) => new Promise<any>((resolve, reject) => {
    const socket = new WebSocket(`ws://${location.host}/api/v1/ws`)
    const timer = setTimeout(() => {socket.close(); reject(new Error('模拟接口超时'))}, 20000)
    socket.onopen = () => socket.send(JSON.stringify({v: 1, type: 'request', id: 'ledger-test', method, params}))
    socket.onmessage = event => {
      const value = JSON.parse(String(event.data))
      if (value.id !== 'ledger-test') return
      clearTimeout(timer); socket.close()
      value.ok ? resolve(value.result) : reject(new Error(value.error.message))
    }
    socket.onerror = () => {clearTimeout(timer); reject(new Error('模拟连接失败'))}
  }), {method, params})
}

test.beforeEach(async ({page}) => {
  await page.addInitScript(() => {
    localStorage.setItem('azurpilot.theme', 'light')
    localStorage.setItem('azurpilot.language', 'zh-CN')
  })
})

test('import keeps duplicate cards pending until confirmation', async ({page}, info) => {
  const instance = `import-${info.testId.replace(/[^a-z0-9]/gi, '').slice(-12)}-${info.retry}`
  await page.goto('/'); await rpc(page, 'instances.create', {name: instance})
  await page.goto(`/#/i/${instance}/task/FleetAnalysis?tab=scan`)
  const file = page.locator('input[type=file][accept=".json,.csv,.xlsx"]')
  await expect(file).toBeEnabled()
  await file.setInputFiles({name: 'ships.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify([
    {name: '杜威', level: 100, rarity: 'R'}, {name: '杜威', level: 110, rarity: 'R'},
  ]))})
  await expect(page.getByRole('button', {name: /确认无歧义的新增和更新/})).toBeEnabled()
  const pending = await rpc(page, 'dock.report', {instance})
  expect(pending.ships).toHaveLength(0)
  expect(pending.pending_changes.filter((row: any) => row.kind === 'ship')).toHaveLength(2)
  await expect(file).toBeDisabled()
  await page.getByRole('tab', {name: '我的舰船', exact: true}).click()
  await expect(page.getByRole('button', {name: /确认无歧义/})).toHaveCount(0)
  await page.getByRole('tab', {name: '扫描更新', exact: true}).click()
  await page.getByRole('button', {name: /确认无歧义的新增和更新/}).click()
  await expect(page.getByText('没有待确认差异', {exact: true})).toBeVisible()
  const saved = await rpc(page, 'dock.report', {instance})
  expect(saved.ships.map((row: any) => row.level).sort()).toEqual([100, 110])
  expect(new Set(saved.ships.map((row: any) => row.id)).size).toBe(2)
})

test('cultivation selects duplicate copies independently and trial conditions never change facts', async ({page}, info) => {
  const instance = `plan-${info.testId.replace(/[^a-z0-9]/gi, '').slice(-12)}-${info.retry}`
  await page.goto('/'); await rpc(page, 'instances.create', {name: instance})
  const empty = await rpc(page, 'dock.report', {instance})
  const saved = await rpc(page, 'dock.patch', {instance, revision: empty.revision, changes: [
    {name: '杜威', group_type: 10102, level: 100, rarity: 'R', base_rarity: 'R', extra_exp: 0},
    {name: '杜威', group_type: 10102, level: 110, rarity: 'R', base_rarity: 'R', extra_exp: 0},
  ]})
  const [first, second] = saved.ships
  await page.goto(`/#/i/${instance}/task/FleetAnalysis?tab=mind&ships=${first.id},${second.id}`)
  await expect(page.getByLabel(`选择 杜威 副本 ${first.id}`, {exact: true})).toBeChecked()
  await expect(page.getByLabel(`选择 杜威 副本 ${second.id}`, {exact: true})).toBeChecked()
  await page.getByLabel('目标等级', {exact: true}).selectOption('125')
  await expect(page.getByText(/参与 2 艘/)).toBeVisible()
  await expect(page.locator('.mind-totals').getByText('心智单元Ⅱ')).toBeVisible()
  const row = page.locator('tbody tr').filter({has: page.getByLabel(`选择 杜威 副本 ${first.id}`, {exact: true})})
  await row.locator('summary').click()
  await page.getByLabel(`假设等级 ${first.id}`, {exact: true}).fill('120')
  await expect(row.getByText('已启用试算条件', {exact: true})).toBeVisible()
  await expect(page.getByText(/参与 2 艘/)).toBeVisible()
  const afterTrial = await rpc(page, 'dock.report', {instance})
  expect(afterTrial.revision).toBe(saved.revision)
  expect(afterTrial.ships).toEqual(saved.ships)
  await page.getByRole('button', {name: '每种选择最高等级一艘', exact: true}).click()
  await expect(page.getByLabel(`选择 杜威 副本 ${first.id}`, {exact: true})).not.toBeChecked()
  await expect(page.getByLabel(`选择 杜威 副本 ${second.id}`, {exact: true})).toBeChecked()
  await page.getByRole('tab', {name: '我的舰船', exact: true}).click()
  await page.locator(`a[href$="ship=${first.id}&edit=1"]`).click()
  await page.getByRole('dialog').getByLabel('等级', {exact: true}).fill('112')
  await page.getByRole('tab', {name: '扫描更新', exact: true}).click()
  await page.getByRole('tab', {name: '我的舰船', exact: true}).click()
  await page.locator(`a[href$="ship=${first.id}&edit=1"]`).click()
  await expect(page.getByRole('dialog').getByLabel('等级', {exact: true})).toHaveValue('112')
  await page.getByRole('dialog').getByRole('button', {name: '放弃草稿', exact: true}).click()
  await expect(page.getByRole('dialog').getByLabel('等级', {exact: true})).toHaveValue('100')
})
