import {expect, test, type Page} from '@playwright/test'

async function rpc(page: Page, method: string, params: object) {
  return page.evaluate(({method, params}) => new Promise<any>((resolve, reject) => {
    const socket = new WebSocket(`ws://${location.host}/api/v1/ws`)
    const timer = setTimeout(() => {socket.close(); reject(new Error(`接口超时 ${method}`))}, 30000)
    socket.onopen = () => socket.send(JSON.stringify({v: 1, type: 'request', id: 'cultivation-test', method, params}))
    socket.onmessage = event => {
      const response = JSON.parse(String(event.data))
      if (response.id !== 'cultivation-test') return
      clearTimeout(timer); socket.close(); response.ok ? resolve(response.result) : reject(new Error(response.error.message))
    }
    socket.onerror = () => {clearTimeout(timer); reject(new Error('模拟连接失败'))}
  }), {method, params})
}
async function seed(page: Page, name: string, ships: object[]) {
  await page.goto('/')
  await rpc(page, 'instances.create', {name})
  const empty = await rpc(page, 'dock.report', {instance: name})
  return rpc(page, 'dock.patch', {instance: name, revision: empty.revision, changes: ships})
}
const ship = (level: number, excluded = false) => ({name: '企业', group_type: 10706, level, rarity: 'SSR', base_rarity: 'SSR', extra_exp: null, excluded})
test.beforeEach(async ({page}) => {
  await page.addInitScript(() => {localStorage.setItem('azurpilot.theme', 'light'); localStorage.setItem('azurpilot.language', 'zh-CN')})
})

test('cultivation chooses real copies and 125 target preserves unknown experience', async ({page}, info) => {
  const instance = `calc-copies-${info.retry}`
  const saved = await seed(page, instance, [ship(120), ship(100)])
  await page.goto(`/#/i/${instance}/task/FleetAnalysis?tab=mind`)
  await expect(page.getByText('参与 1 艘', {exact: false})).toBeVisible()
  await page.getByRole('button', {name: '选择全部舰船', exact: true}).click()
  await expect(page.getByText('参与 2 艘', {exact: false})).toBeVisible()
  await page.getByLabel('目标等级', {exact: true}).selectOption('125')
  await expect(page.locator('.mind-totals')).toContainText('300')
  const first = page.locator('tbody tr').filter({hasText: `企业 · #${saved.ships[0].id}`})
  await expect(first).toContainText('≤ 1,310,000')
  await expect(page.locator('input[type=file]')).toHaveCount(0)
  await expect(page.getByRole('button', {name: '扫描船坞', exact: true})).toHaveCount(0)
  expect((await rpc(page, 'dock.report', {instance})).ships).toEqual(saved.ships)
})

test('cultivation trial restores facts and explicit empty plan survives reload', async ({page}, info) => {
  const instance = `calc-trial-${info.retry}`
  const saved = await seed(page, instance, [ship(100)])
  const id = saved.ships[0].id
  await page.goto(`/#/i/${instance}/task/FleetAnalysis?tab=mind&ships=${id}`)
  await expect(page.getByText('参与 1 艘', {exact: false})).toBeVisible()
  await expect(page).not.toHaveURL(/ships=/)
  await page.getByText('设置试算条件', {exact: true}).click()
  await page.getByLabel(`假设等级 ${id}`, {exact: true}).fill('119')
  await page.getByLabel(`假设经验 ${id}`, {exact: true}).fill('0')
  await expect(page.locator('tbody')).toContainText('205,000')
  expect((await rpc(page, 'dock.report', {instance})).ships[0].level).toBe(100)
  await page.reload()
  await expect(page.getByText('已启用试算条件', {exact: true})).toBeVisible()
  await page.getByRole('button', {name: '恢复全部事实值'}).click()
  await expect(page.getByText('设置试算条件', {exact: true})).toBeVisible()
  await page.getByRole('button', {name: '清空选择', exact: true}).click()
  await expect(page.getByText('参与 0 艘', {exact: false})).toBeVisible()
  await page.reload()
  await expect(page.getByText('参与 0 艘', {exact: false})).toBeVisible()
})

test('cultivation rejects stale facts until explicit reload', async ({page}, info) => {
  const instance = `calc-revision-${info.retry}`
  const saved = await seed(page, instance, [ship(100)])
  await page.goto(`/#/i/${instance}/task/FleetAnalysis?tab=mind`)
  await expect(page.getByText('参与 1 艘', {exact: false})).toBeVisible()
  await rpc(page, 'dock.patch', {instance, revision: saved.revision, changes: [{id: saved.ships[0].id, level: 110}]})
  await page.getByLabel('目标等级', {exact: true}).selectOption('125')
  await expect(page.getByText('舰船数据已更新，请重新载入后计算', {exact: true})).toBeVisible()
  await expect(page.locator('.mind-totals')).toHaveCount(0)
  await page.getByRole('button', {name: '重新载入舰船', exact: true}).click()
  await expect(page.getByText('参与 1 艘', {exact: false})).toBeVisible()
  await expect(page.locator('tbody')).toContainText('110')
})

test('cultivation legacy exclusions seed a single isolated plan', async ({page}, info) => {
  const instance = `calc-excluded-${info.retry}`, other = `calc-isolated-${info.retry}`
  const saved = await seed(page, instance, [ship(120, true), ship(100)])
  await seed(page, other, [ship(110)])
  await page.goto(`/#/i/${instance}/task/FleetAnalysis?tab=mind`)
  await expect(page.getByRole('checkbox', {name: `选择 企业 副本 ${saved.ships[0].id}`, exact: true})).not.toBeChecked()
  await page.getByRole('button', {name: '选择全部舰船', exact: true}).click()
  await expect(page.getByText('参与 2 艘', {exact: false})).toBeVisible()
  await page.goto(`/#/i/${other}/task/FleetAnalysis?tab=mind`)
  await expect(page.getByText('参与 1 艘', {exact: false})).toBeVisible()
  await page.goto(`/#/i/${instance}/task/FleetAnalysis?tab=mind`)
  await expect(page.getByText('参与 2 艘', {exact: false})).toBeVisible()
  expect((await rpc(page, 'dock.report', {instance})).ships[0].excluded).toBe(true)
})

test('cultivation mobile page contains horizontal tables without overflowing viewport', async ({page}, info) => {
  const instance = `calc-mobile-${info.retry}`
  await seed(page, instance, [ship(100)])
  await page.setViewportSize({width: 390, height: 844})
  await page.goto(`/#/i/${instance}/task/FleetAnalysis?tab=mind`)
  await expect(page.getByText('参与 1 艘', {exact: false})).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  await page.screenshot({path: info.outputPath('cultivation-mobile.png'), fullPage: true})
})
