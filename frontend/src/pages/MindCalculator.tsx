import {useEffect, useMemo, useRef, useState} from 'react'
import {Link, useParams, useSearchParams} from 'react-router-dom'
import {api, ApiError} from '../api/client'
import type {DockReport} from '../api/types'
import type {Catalog, Report} from '../fleet-tech/types'
import {useConnection} from '../app/context'
import {ErrorBox, Loading} from '../components/ui'
import {automaticCandidates, readPlan, type Assumption, type CultivationPlan, type CultivationReport} from '../mind/cultivation'
import '../mind/mind.css'

const amount = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString()
interface Facts {dock: DockReport; technology: Report; catalog: Catalog}
export function MindCalculator() {
  const {instance = ''} = useParams(), [params, setParams] = useSearchParams(), connection = useConnection()
  const [facts, setFacts] = useState<Facts>(), [plan, setPlan] = useState<CultivationPlan>()
  const [result, setResult] = useState<CultivationReport>(), [error, setError] = useState('')
  const [query, setQuery] = useState(''), [includedOnly, setIncludedOnly] = useState(false), [page, setPage] = useState(0)
  const [reload, setReload] = useState(0), [calculating, setCalculating] = useState(false)
  const memory = useRef<{instance: string; plan: CultivationPlan} | null>(null)
  // Old manually selected copy IDs must not freeze the automatic calculation range.
  const key = `azurpilot.cultivation-auto.${instance}`
  useEffect(() => {
    if (params.has('ships') || params.has('scope')) setParams(previous => {
      const clean = new URLSearchParams(previous); clean.delete('ships'); clean.delete('scope'); return clean
    }, {replace: true})
  }, [params, setParams])
  useEffect(() => {
    let active = true, pending = false
    setFacts(undefined); setResult(undefined); setError('')
    if (connection !== 'ready') return
    const load = async () => {
      if (pending) return
      pending = true
      try {
        const [dock, technology, catalog] = await Promise.all([api.request('dock.report', {instance}),
          api.request('fleetTech.report', {instance}), api.request('fleetTech.catalog', {instance})])
        if (!active) return
        const previous = memory.current?.instance === instance ? memory.current.plan : undefined
        let stored: string | null = null
        if (!previous) try {stored = localStorage.getItem(key)} catch { /* Optional browser preferences. */ }
        const next = readPlan(previous ? JSON.stringify(previous) : stored, dock.ships)
        if (!previous || JSON.stringify(previous) !== JSON.stringify(next)) {
          memory.current = {instance, plan: next}; setPlan(next)
        }
        setFacts(current => current?.dock.revision === dock.revision && current.technology.revision === technology.revision
          ? current : {dock, technology, catalog})
      } catch (e) {if (active) {setError((e as Error).message); setFacts(undefined); setResult(undefined)}}
      finally {pending = false}
    }
    void load(); const timer = setInterval(() => void load(), 5000)
    return () => {active = false; clearInterval(timer)}
  }, [instance, connection, key, reload])
  const candidates = useMemo(() => facts ? automaticCandidates(facts.dock.ships, facts.catalog, facts.technology.ships) : [], [facts])
  const ids = useMemo(() => candidates.filter(card => !plan?.excluded_groups.includes(card.group_type!)).map(card => card.id), [candidates, plan?.excluded_groups])
  useEffect(() => {
    if (!facts || facts.dock.instance !== instance || !plan || connection !== 'ready') return
    memory.current = {instance, plan}
    try {localStorage.setItem(key, JSON.stringify(plan))} catch { /* Calculation works without browser storage. */ }
    let active = true
    setResult(undefined); setCalculating(true)
    const timer = setTimeout(() => {
      void api.request('mind.plan', {instance, revision: facts.dock.revision, ship_ids: ids,
        target_level: 120, assumptions: plan.assumptions.filter(row => ids.includes(row.ship_id))}).then(value => {
        if (active) {setResult(value); setCalculating(false); setError('')}
      }).catch(e => {
        if (!active) return
        setCalculating(false)
        if (e instanceof ApiError && e.code === 'CONFLICT') setReload(value => value + 1)
        else setError((e as Error).message)
      })
    }, 200)
    return () => {active = false; clearTimeout(timer)}
  }, [facts, plan, ids, connection, instance, key])
  const included = useMemo(() => new Set(ids), [ids])
  const rows = useMemo(() => candidates.filter(row => row.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
    && (!includedOnly || included.has(row.id))), [candidates, query, includedOnly, included])
  const pages = Math.max(1, Math.ceil(rows.length / 50)), currentPage = Math.min(page, pages - 1)
  const estimates = new Map(result?.ships.map(row => [row.id, row]))
  const progress = new Map(facts?.technology.ships.map(row => [row.group_type, row]))
  function exclude(group: number, excluded: boolean) {
    setPlan(current => current && ({...current, excluded_groups: excluded
      ? [...new Set([...current.excluded_groups, group])] : current.excluded_groups.filter(value => value !== group)}))
  }
  function assume(id: number, field: 'level' | 'awakened_to', raw: string) {
    if (!plan) return
    const existing = plan.assumptions.find(row => row.ship_id === id) ?? {ship_id: id}
    const next = {...existing} as Assumption
    if (!raw.trim()) {delete next[field]; if (field === 'level') delete next.awakened_to}
    else {
      const value = Number(raw)
      if (!Number.isSafeInteger(value) || value < 1 || value > 125) return
      if (field === 'awakened_to') next.awakened_to = value as Assumption['awakened_to']
      else {next[field] = value; delete next.awakened_to}
    }
    const rest = plan.assumptions.filter(row => row.ship_id !== id)
    setPlan({...plan, assumptions: Object.keys(next).length > 1 ? [...rest, next] : rest})
  }
  const unverified = candidates.filter(card => progress.get(card.group_type!)?.lv120 == null).length
  const review = facts?.dock.ships.filter(card => card.review || !card.group_type).length ?? 0
  return <div className="mind-calculator">
    <h2>养成计算</h2><p>自动按每种舰船现有最高等级的一艘，计算培养至120级所需的心智单元和物资。累计120级科技已完成的舰船种类不再重复计算。</p>
    {error && <ErrorBox message={error}/>}
    {!facts && !error && <Loading/>}
    {facts && plan && <>
      <section className="mind-panel" aria-busy={calculating}><h3>培养至120级</h3>
        {calculating && <p role="status">正在计算…</p>}
        {result && <><div className="mind-totals mind-resource-totals">
          <div>心智单元<strong>{amount(result.mind)}</strong></div><div>物资<strong>{amount(result.gold)}</strong></div>
        </div><p role="status">参与 {result.included - result.unknown} 艘 · 觉醒状态待确认 {result.estimated} 艘 · 信息不足 {result.unknown} 艘（未计入合计）</p></>}
        <p>自动识别 {candidates.length} 种舰船，本次排除 {candidates.length - ids.length} 个。数据更新后自动重算。</p>
        {unverified > 0 && <p className="muted">其中 {unverified} 种舰船的累计120级科技未核实，暂按当前等级估算费用。</p>}
        {review > 0 && <p className="muted">另有 {review} 条舰船身份待核对，未参与自动计算。<Link to="?tab=data">前往我的舰船核对</Link></p>}
        <p className="muted">仅统计认知觉醒费用。处于觉醒边界时，默认按尚未觉醒估算；可在试算条件中设置已觉醒上限。</p>
      </section>
      <section className="mind-panel"><h3>舰船与资源明细</h3>
        <div className="mind-toolbar"><input aria-label="搜索养成舰船" placeholder="搜索舰船" value={query} onChange={event => {setQuery(event.target.value); setPage(0)}}/>
          <label><input type="checkbox" checked={includedOnly} onChange={event => {setIncludedOnly(event.target.checked); setPage(0)}}/>仅显示参与计算</label>
          <button className="button secondary" disabled={!plan.excluded_groups.length && !plan.assumptions.length} onClick={() => setPlan({excluded_groups: [], assumptions: []})}>恢复自动计算</button>
        </div>
        <div className="mind-table-wrap"><table><thead><tr><th>参与计算</th><th>舰船</th><th>当前等级</th><th>心智单元</th><th>物资</th><th>计算说明 / 试算</th></tr></thead><tbody>
          {rows.slice(currentPage * 50, (currentPage + 1) * 50).map(card => {
            const row = estimates.get(card.id), assumption = plan.assumptions.find(value => value.ship_id === card.id)
            const level = assumption?.level ?? card.level
            const cap = [100, 105, 110, 115, 120, 125].find(value => value >= level) ?? 125
            const caps = [cap, ...(level === cap && cap < 125 ? [cap + 5] : [])]
            return <tr key={card.id}><td><input type="checkbox" aria-label={`参与计算 ${card.name}`} checked={included.has(card.id)} onChange={event => exclude(card.group_type!, !event.target.checked)}/></td>
              <td><Link to={`?tab=data&ship=${card.id}`}>{card.name} · #{card.id}</Link></td><td>{card.level || '未知'}</td>
              <td>{amount(row?.mind)}</td><td>{amount(row?.gold)}</td>
              <td>{!included.has(card.id) ? <p>本次已排除</p> : <>
                {progress.get(card.group_type!)?.lv120 == null && <p>累计120级科技未核实，按当前等级估算</p>}
                {row?.notes.map(note => <p key={note}>{note}</p>)}<details><summary>{assumption ? '已启用试算条件' : '设置试算条件'}</summary>
                  <label>假设等级<input aria-label={`假设等级 ${card.id}`} type="number" min={1} max={125} value={assumption?.level ?? ''} placeholder={String(card.level)} onChange={event => assume(card.id, 'level', event.target.value)}/></label>
                  <label>已觉醒上限<select aria-label={`已觉醒上限 ${card.id}`} value={assumption?.awakened_to ?? ''} onChange={event => assume(card.id, 'awakened_to', event.target.value)}><option value="">按等级估算</option>{caps.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
                  {assumption && <button className="button secondary" onClick={() => setPlan({...plan, assumptions: plan.assumptions.filter(value => value.ship_id !== card.id)})}>恢复事实值</button>}
                </details></>}
              </td></tr>
          })}
        </tbody></table></div>
        {!rows.length && <p>{candidates.length ? '没有符合筛选条件的舰船。' : '当前没有需要计算120级养成费用的舰船。'}</p>}
        <div className="mind-toolbar"><button disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button><span>{currentPage + 1} / {pages}</span><button disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>下一页</button></div>
      </section>
    </>}
  </div>
}
