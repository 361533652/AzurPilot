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
  await page.goto('/'); await rpc(page, 'instances.create', {name: instance}); await page.goto('about:blank'); await page.goto('/')
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

test('automatic cultivation follows committed copies while inline drafts persist and discard independently', async ({page}, info) => {
  const instance = `plan-${info.testId.replace(/[^a-z0-9]/gi, '').slice(-12)}-${info.retry}`
  await page.goto('/'); await rpc(page, 'instances.create', {name: instance}); await page.goto('about:blank'); await page.goto('/')
  const empty = await rpc(page, 'dock.report', {instance})
  const saved = await rpc(page, 'dock.patch', {instance, revision: empty.revision, changes: [
    {name: '杜威', group_type: 10102, level: 100, rarity: 'R', base_rarity: 'R', extra_exp: 0},
    {name: '杜威', group_type: 10102, level: 110, rarity: 'R', base_rarity: 'R', extra_exp: 0},
  ]})
  const [first, second] = saved.ships
  await page.goto(`/#/i/${instance}/task/FleetAnalysis?tab=mind`)
  const selected = page.getByRole('link', {name: '杜威', exact: true})
  await expect(page.getByText('计入 1 种舰船', {exact: false})).toBeVisible()
  await expect(page.locator('tbody tr')).toHaveCount(1)
  await expect(selected).toHaveAttribute('href', `?tab=data&ship=${second.id}`)
  await page.getByRole('tab', {name: '我的舰船', exact: true}).click()
  const level = page.getByLabel(`等级 · 杜威 #${first.id}`, {exact: true})
  await level.fill('112')
  expect((await rpc(page, 'dock.report', {instance})).ships).toEqual(saved.ships)
  await page.getByRole('tab', {name: '养成计算', exact: true}).click()
  await expect(selected).toHaveAttribute('href', `?tab=data&ship=${second.id}`)
  await page.getByRole('tab', {name: '我的舰船', exact: true}).click()
  await expect(level).toHaveValue('112')
  await page.getByRole('button', {name: '放弃草稿', exact: true}).click()
  await expect(level).toHaveValue('100')
  await level.fill('112')
  await page.getByRole('button', {name: '保存', exact: true}).click()
  await expect(page.getByText('舰船数据已保存', {exact: true})).toBeVisible()
  const committed = await rpc(page, 'dock.report', {instance})
  expect(committed.ships.find((card: any) => card.id === first.id).level).toBe(112)
  expect(committed.ships.find((card: any) => card.id === second.id).level).toBe(110)
  await page.getByRole('tab', {name: '养成计算', exact: true}).click()
  await expect(selected).toHaveAttribute('href', `?tab=data&ship=${first.id}`)
})
