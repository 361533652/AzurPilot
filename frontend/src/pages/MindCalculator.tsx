import {useEffect, useMemo, useRef, useState} from 'react'
import {Link, useParams, useSearchParams} from 'react-router-dom'
import {api, ApiError} from '../api/client'
import type {DockReport} from '../api/types'
import type {Catalog, Report} from '../fleet-tech/types'
import {useApp, useConnection} from '../app/context'
import {ErrorBox, Loading} from '../components/ui'
import {automaticCandidates, readPlan, type CultivationPlan, type CultivationReport} from '../mind/cultivation'
import '../fleet-tech/style.css'
import '../mind/cultivation.css'

const amount = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString()
const columns = {name: '舰船', level: '当前等级', mind: '心智单元', gold: '物资', efficiency: '每千心智科技点'} as const
type Column = keyof typeof columns
type Sort = {column: Column; direction: 'ascending' | 'descending'}
interface Facts {dock: DockReport; technology: Report; catalog: Catalog}
export function MindCalculator() {
  const {instance = ''} = useParams(), [params, setParams] = useSearchParams(), connection = useConnection(), {language} = useApp()
  const [facts, setFacts] = useState<Facts>(), [plan, setPlan] = useState<CultivationPlan>()
  const [result, setResult] = useState<CultivationReport>(), [error, setError] = useState('')
  const [query, setQuery] = useState(''), [includedOnly, setIncludedOnly] = useState(false)
  const [sort, setSort] = useState<Sort>({column: 'efficiency', direction: 'descending'})
  const [mindCosts, setMindCosts] = useState<Record<string, number>>({})
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
        target_level: 120}).then(value => {
        if (active) {setResult(value); setMindCosts(value.mind_costs_100_to_120); setCalculating(false); setError('')}
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
  const estimates = useMemo(() => new Map(result?.ships.map(row => [row.id, row])), [result])
  const efficiencies = useMemo(() => {
    const ships = new Map(facts?.catalog.ships.map(ship => [ship.group_type, ship]))
    return new Map(candidates.map(card => {
      const ship = ships.get(card.group_type!), points = ship?.technology?.pt_level
      const rarity = ship?.rarity === 'PR' ? 'SSR' : ship?.rarity === 'DR' ? 'UR' : ship?.rarity
      const mind = rarity ? mindCosts[rarity] : undefined
      return [card.id, {points, mind, value: points != null && mind != null && mind > 0 ? points / mind * 1000 : null}]
    }))
  }, [candidates, facts?.catalog, mindCosts])
  const rows = useMemo(() => {
    const collator = new Intl.Collator(language, {numeric: true})
    const valueFor = (card: DockReport['ships'][number]): string | number | null => {
      switch (sort.column) {
        case 'name': return card.name
        case 'level': return card.level || null
        case 'mind': return estimates.get(card.id)?.mind ?? null
        case 'gold': return estimates.get(card.id)?.gold ?? null
        case 'efficiency': return efficiencies.get(card.id)?.value ?? null
      }
    }
    return candidates.filter(row => row.name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
      && (!includedOnly || included.has(row.id))).sort((a, b) => {
      const left = valueFor(a), right = valueFor(b)
      if (left === null && right !== null) return 1
      if (right === null && left !== null) return -1
      const difference = typeof left === 'number' && typeof right === 'number' ? left - right
        : typeof left === 'string' && typeof right === 'string' ? collator.compare(left, right) : 0
      return difference * (sort.direction === 'ascending' ? 1 : -1) || collator.compare(a.name, b.name) || a.id - b.id
    })
  }, [candidates, query, includedOnly, included, sort, estimates, efficiencies, language])
  function sortBy(column: Column) {
    setSort(current => ({column, direction: current.column === column
      ? current.direction === 'ascending' ? 'descending' : 'ascending'
      : column === 'name' ? 'ascending' : 'descending'}))
  }
  function exclude(group: number, excluded: boolean) {
    setPlan(current => current && ({...current, excluded_groups: excluded
      ? [...new Set([...current.excluded_groups, group])] : current.excluded_groups.filter(value => value !== group)}))
  }
  const review = facts?.dock.ships.filter(card => card.review || !card.group_type).length ?? 0
  return <div className="fleet-technology cultivation-calculator">
    {error && <ErrorBox message={error}/>}
    {!facts && !error && <Loading/>}
    {facts && plan && <>
      <section className="ft-summary" aria-busy={calculating}>
        <h2>养成计算</h2>
        {calculating && <p role="status">正在计算…</p>}
        {result && <><div className="cultivation-totals">
          <div>心智单元<strong>{amount(result.mind)}</strong></div><div>物资<strong>{amount(result.gold)}</strong></div>
        </div><p role="status">计入 {result.included - result.unknown} 种舰船{result.unknown > 0 && ` · 信息不足 ${result.unknown} 种，未计入合计`}{candidates.length > ids.length && ` · 手动排除 ${candidates.length - ids.length} 种`}</p></>}
        <small>每种舰船取现有最高等级的一艘，已完成120级科技或已有120级副本的舰船不重复计算。数据更新后自动重算。</small>
        {review > 0 && <p className="cultivation-review">另有 {review} 条舰船身份待核对。<Link to="?tab=data">前往我的舰船核对</Link></p>}
      </section>
      <p className="ft-edit-hint">仅计算心智单元和物资。100、105、110、115级均视为尚未支付下一阶段觉醒费用，计入从该阶段至120级的全部剩余费用。</p>
      <p className="ft-edit-hint">每千心智科技点 = 120级奖励科技点 ÷ 100→120级完整心智单元费用 × 1000，数值越高越划算。</p>
      <div className="ft-search"><input type="search" aria-label="搜索养成舰船" placeholder="搜索舰船" value={query} onChange={event => setQuery(event.target.value)}/><span>共 {rows.length} 种</span>
        <label className="cultivation-included"><input type="checkbox" checked={includedOnly} onChange={event => setIncludedOnly(event.target.checked)}/>仅显示参与计算</label>
        <button className="button secondary" disabled={!plan.excluded_groups.length} onClick={() => setPlan({excluded_groups: []})}>恢复全部参与</button>
      </div>
      <div className="ft-table-wrap cultivation-table"><table><thead><tr><th scope="col">参与计算</th>{(Object.entries(columns) as [Column, string][]).map(([column, label]) => <th key={column} scope="col" aria-sort={sort.column === column ? sort.direction : 'none'} title={column === 'efficiency' ? '120级奖励科技点 ÷ 100→120级完整心智单元费用 × 1000；越高越划算' : undefined}><button type="button" className="ft-sort" onClick={() => sortBy(column)}>{label}<span aria-hidden="true">{sort.column === column ? sort.direction === 'ascending' ? '↑' : '↓' : '↕'}</span></button></th>)}</tr></thead><tbody>
        {rows.map(card => {
          const row = estimates.get(card.id), participating = included.has(card.id), efficiency = efficiencies.get(card.id)
          return <tr key={card.id} className={participating ? '' : 'cultivation-excluded'}>
            <td><input type="checkbox" aria-label={`参与计算 ${card.name}`} checked={participating} onChange={event => exclude(card.group_type!, !event.target.checked)}/></td>
            <td className="ft-name"><Link to={`?tab=data&ship=${card.id}`}>{card.name}</Link>{participating && row?.status === 'unknown' && <small className="cultivation-warning">{row.notes.join('；')} · <Link to={`?tab=data&ship=${card.id}`}>前往核对</Link></small>}{!participating && <small className="cultivation-note">已排除</small>}</td>
            <td>{card.level || '未知'}</td><td>{amount(row?.mind)}</td><td>{amount(row?.gold)}</td>
            <td title={efficiency?.value == null ? undefined : `${efficiency.points} 科技点 ÷ ${amount(efficiency.mind)} 心智单元 × 1000（100→120级）`}>{efficiency?.value == null ? '—' : efficiency.value.toLocaleString(language, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</td>
          </tr>
        })}
      </tbody></table></div>
      {!rows.length && <p>{candidates.length ? '没有符合筛选条件的舰船。' : '当前没有需要计算120级养成费用的舰船。'}</p>}
    </>}
  </div>
}
