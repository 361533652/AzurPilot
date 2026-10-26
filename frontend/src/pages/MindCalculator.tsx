import {useEffect, useMemo, useRef, useState} from 'react'
import {Link, useParams, useSearchParams} from 'react-router-dom'
import {api} from '../api/client'
import type {DockReport} from '../api/types'
import {useConnection} from '../app/context'
import {ErrorBox, Loading} from '../components/ui'
import {highestCopies, readPlan, type Assumption, type CultivationPlan, type CultivationReport} from '../mind/cultivation'
import '../mind/mind.css'

const amount = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString()
export function MindCalculator() {
  const {instance = ''} = useParams(), [params, setParams] = useSearchParams(), connection = useConnection()
  const [dock, setDock] = useState<DockReport>(), [plan, setPlan] = useState<CultivationPlan>()
  const [result, setResult] = useState<CultivationReport>(), [error, setError] = useState('')
  const [query, setQuery] = useState(''), [selectedOnly, setSelectedOnly] = useState(false), [page, setPage] = useState(0)
  const [reload, setReload] = useState(0), [calculating, setCalculating] = useState(false)
  const memory = useRef<{instance: string; plan: CultivationPlan} | null>(null)
  const key = `azurpilot.cultivation-plan.${instance}`
  const requested = params.get('ships'), scope = params.get('scope')
  useEffect(() => {
    let active = true
    setDock(undefined); setResult(undefined); setError('')
    if (connection !== 'ready') return
    void Promise.all([api.request('dock.report', {instance}),
      scope === 'missing120' ? api.request('fleetTech.report', {instance}) : Promise.resolve(undefined),
      scope === 'missing120' ? api.request('fleetTech.catalog', {instance}) : Promise.resolve(undefined)])
      .then(([data, technology, catalog]) => {
        if (!active) return
        let stored: string | null = null
        try {stored = localStorage.getItem(key)} catch { /* Optional local plan. */ }
        const next = readPlan(memory.current?.instance === instance ? JSON.stringify(memory.current.plan) : stored, data.ships)
        if (requested) {const ids = new Set(requested.split(',').map(Number)); next.ids = data.ships.filter(row => ids.has(row.id)).map(row => row.id)}
        if (technology) {
          const supported = new Set(catalog?.ships.filter(row => row.technology).map(row => row.group_type))
          const groups = new Set(technology.ships.filter(row => supported.has(row.group_type) && row.lv120 !== true).map(row => row.group_type))
          next.ids = highestCopies(data.ships.filter(row => row.group_type && groups.has(row.group_type)))
        }
        next.assumptions = next.assumptions.filter(row => next.ids.includes(row.ship_id))
        memory.current = {instance, plan: next}
        setDock(data); setPlan(next)
        // A transfer selects the initial objects; refresh preserves later choices.
        if (requested || scope) {
          try {localStorage.setItem(key, JSON.stringify(next))} catch { /* In-memory plan remains usable. */ }
          setParams(previous => {const clean = new URLSearchParams(previous); clean.delete('ships'); clean.delete('scope'); return clean}, {replace: true})
        }
      }).catch(e => {if (active) setError((e as Error).message)})
    return () => {active = false}
  }, [instance, connection, key, requested, scope, reload])
  useEffect(() => {
    if (!dock || dock.instance !== instance || !plan || connection !== 'ready') return
    memory.current = {instance, plan}
    try {localStorage.setItem(key, JSON.stringify(plan))} catch { /* Calculation works without browser storage. */ }
    let active = true
    setResult(undefined); setCalculating(true); setError('')
    const timer = setTimeout(() => {
      void api.request('mind.plan', {instance, revision: dock.revision, ship_ids: plan.ids,
        target_level: plan.target, assumptions: plan.assumptions}).then(value => {
        if (active) {setResult(value); setCalculating(false)}
      }).catch(e => {if (active) {setError((e as Error).message); setCalculating(false)}})
    }, 200)
    return () => {active = false; clearTimeout(timer)}
  }, [dock, plan, connection, instance, key])
  const chosen = useMemo(() => new Set(plan?.ids), [plan?.ids])
  const rows = useMemo(() => (dock?.ships ?? []).filter(row => row.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
    && (!selectedOnly || chosen.has(row.id))), [dock, query, selectedOnly, chosen])
  const pages = Math.max(1, Math.ceil(rows.length / 50)), currentPage = Math.min(page, pages - 1)
  const estimates = new Map(result?.ships.map(row => [row.id, row]))
  function select(ids: number[]) {setPlan(current => current && ({...current, ids, assumptions: current.assumptions.filter(row => ids.includes(row.ship_id))}))}
  function assume(id: number, field: 'level' | 'extra_exp' | 'awakened_to', raw: string) {
    if (!plan) return
    const existing = plan.assumptions.find(row => row.ship_id === id) ?? {ship_id: id}
    const next = {...existing} as Assumption
    if (!raw.trim()) delete next[field]
    else {
      const value = Number(raw)
      if (!Number.isSafeInteger(value) || value < (field === 'extra_exp' ? 0 : 1) || field === 'level' && value > 125) return
      if (field === 'awakened_to') next.awakened_to = value as Assumption['awakened_to']
      else next[field] = value
    }
    const rest = plan.assumptions.filter(row => row.ship_id !== id)
    setPlan({...plan, assumptions: Object.keys(next).length > 1 ? [...rest, next] : rest})
  }
  return <div className="mind-calculator">
    <h2>养成计算</h2><p>选择具体舰船和培养目标。事实数据来自「我的舰船」，试算条件只保存在本浏览器的当前方案中。</p>
    <button className="button secondary" disabled={connection !== 'ready'} onClick={() => setReload(value => value + 1)}>重新载入舰船</button>
    {error && <ErrorBox message={error}/>}
    {!dock && !error && <Loading/>}
    {dock && plan && <>
      <section className="mind-panel"><h3>1. 选择对象</h3>
        <div className="mind-toolbar">
          <button className="button secondary" onClick={() => select(dock.ships.map(row => row.id))}>选择全部舰船</button>
          <button className="button secondary" onClick={() => select(highestCopies(dock.ships))}>每种选择最高等级一艘</button>
          <button className="button secondary" onClick={() => select(rows.map(row => row.id))}>选择筛选结果</button>
          <button className="button secondary" onClick={() => select([])}>清空选择</button>
        </div>
        <p>已选 {plan.ids.length} 艘 / 共 {dock.ships.length} 艘。同名副本逐艘列出；每种选择规则只在点击时应用。</p>
        <p className="muted">首次建立方案会继承旧排除标记。之后通过勾选维护本次范围，不修改舰船记录。</p>
      </section>
      <section className="mind-panel"><h3>2. 设置目标</h3>
        <label>目标等级 <select aria-label="目标等级" value={plan.target} onChange={event => setPlan({...plan, target: Number(event.target.value) as 120 | 125})}><option value={120}>120</option><option value={125}>125</option></select></label>
        <button className="button secondary" disabled={!plan.assumptions.length} onClick={() => setPlan({...plan, assumptions: []})}>恢复全部事实值</button>
        <p>仅计算等级经验与觉醒费用，不含突破、强化或科研开发。经验未知时显示上限；处于觉醒边界时，可在明细中设置已觉醒上限。</p>
      </section>
      <section className="mind-panel" aria-busy={calculating}><h3>3. 计算结果</h3>
        {calculating && <p role="status">正在计算…</p>}
        {result && <><div className="mind-totals">
          <div>心智单元Ⅰ<strong>{amount(result.mind)}</strong></div><div>心智单元Ⅱ<strong>{amount(result.mind2)}</strong></div>
          <div>金币<strong>{amount(result.gold)}</strong></div><div>经验（上限）<strong>{amount(result.experience_upper)}</strong></div>
        </div><p role="status">参与 {result.included} 艘 · 估算 {result.estimated} 艘 · 信息不足 {result.unknown} 艘（未计入合计）</p></>}
      </section>
      <section className="mind-panel"><h3>舰船与试算明细</h3>
        <div className="mind-toolbar"><input aria-label="搜索养成舰船" placeholder="搜索舰船" value={query} onChange={event => {setQuery(event.target.value); setPage(0)}}/>
          <label><input type="checkbox" checked={selectedOnly} onChange={event => {setSelectedOnly(event.target.checked); setPage(0)}}/>仅显示已选</label>
        </div>
        <div className="mind-table-wrap"><table><thead><tr><th>选择</th><th>舰船</th><th>当前等级</th><th>目标</th><th>经验</th><th>觉醒资源</th><th>计算说明 / 试算</th></tr></thead><tbody>
          {rows.slice(currentPage * 50, (currentPage + 1) * 50).map(card => {
            const row = estimates.get(card.id), assumption = plan.assumptions.find(value => value.ship_id === card.id)
            const level = assumption?.level ?? card.level
            const cap = [100, 105, 110, 115, 120, 125].find(value => value >= level) ?? 125
            const caps = [cap, ...(level === cap && cap < 125 ? [cap + 5] : [])]
            return <tr key={card.id}><td><input type="checkbox" aria-label={`选择 ${card.name} 副本 ${card.id}`} checked={chosen.has(card.id)} onChange={event => select(event.target.checked ? [...plan.ids, card.id] : plan.ids.filter(id => id !== card.id))}/></td>
              <td><Link to={`?tab=data&ship=${card.id}&edit=1`}>{card.name} · #{card.id}</Link></td><td>{card.level || '未知'}</td><td>{chosen.has(card.id) ? plan.target : '—'}</td>
              <td>{row?.experience != null ? amount(row.experience) : row?.experience_upper != null ? `≤ ${amount(row.experience_upper)}` : '—'}</td>
              <td>{row && row.status !== 'unknown' ? `Ⅰ ${amount(row.mind)} · Ⅱ ${amount(row.mind2)} · 金币 ${amount(row.gold)}` : '—'}</td>
              <td>{row?.notes.map(note => <p key={note}>{note}</p>)}{chosen.has(card.id) && <details><summary>{assumption ? '已启用试算条件' : '设置试算条件'}</summary>
                <label>假设等级<input aria-label={`假设等级 ${card.id}`} type="number" min={1} max={125} value={assumption?.level ?? ''} placeholder={String(card.level)} onChange={event => assume(card.id, 'level', event.target.value)}/></label>
                <label>假设额外经验<input aria-label={`假设经验 ${card.id}`} type="number" min={0} value={assumption?.extra_exp ?? ''} placeholder={card.extra_exp == null ? '未知' : String(card.extra_exp)} onChange={event => assume(card.id, 'extra_exp', event.target.value)}/></label>
                <label>已觉醒上限<select aria-label={`已觉醒上限 ${card.id}`} value={assumption?.awakened_to ?? ''} onChange={event => assume(card.id, 'awakened_to', event.target.value)}><option value="">按等级估算</option>{caps.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
              </details>}</td></tr>
          })}
        </tbody></table></div>
        {!rows.length && <p>没有符合条件的舰船。</p>}
        <div className="mind-toolbar"><button disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button><span>{currentPage + 1} / {pages}</span><button disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>下一页</button></div>
      </section>
    </>}
  </div>
}
