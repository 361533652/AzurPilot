import {useEffect, useMemo, useRef, useState} from 'react'
import {Link, useParams} from 'react-router-dom'
import {api, ApiError} from '../api/client'
import type {DockReport} from '../api/types'
import {catalogRows, matches, type Catalog, type Report} from '../fleet-tech/types'
import {fleetFactionOrder, translateFleetTech} from '../fleet-tech/i18n'
import {useApp, useConnection} from '../app/context'
import {ErrorBox, Loading} from '../components/ui'
import {automaticCandidates, readPlan, technologyEfficiency, type CultivationPlan, type CultivationReport} from '../mind/cultivation'
import '../fleet-tech/style.css'
import '../mind/cultivation.css'

const amount = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString()
const types = [1, 2, 3, 18, 4, 5, 7, 6, 13, 12, 10, 8, 17, 19, 22]
const attrs = [1, 2, 3, 4, 5, 6, 9, 8, 12]
const rarities = ['N', 'R', 'SR', 'SSR', 'UR', 'PR', 'DR']
const nationOrder = (value: number) => {const index = fleetFactionOrder.indexOf(value); return index < 0 ? fleetFactionOrder.length : index}
const columns = {name: '舰船', level: '当前等级', mind: '心智单元', gold: '物资', efficiency: '每千心智科技点'} as const
type Column = keyof typeof columns
type Sort = {column: Column; direction: 'ascending' | 'descending'}
interface Facts {dock: DockReport; technology: Report; catalog: Catalog}
export function MindCalculator() {
  const {instance = ''} = useParams(), connection = useConnection(), {language} = useApp()
  const [facts, setFacts] = useState<Facts>(), [plan, setPlan] = useState<CultivationPlan>()
  const [result, setResult] = useState<CultivationReport>(), [error, setError] = useState('')
  const [query, setQuery] = useState(''), [includedOnly, setIncludedOnly] = useState(false)
  const [filters, setFilters] = useState<Record<string, string[]>>({}), [extra, setExtra] = useState(false)
  const tr = (key: string) => translateFleetTech(language, key)
  const [sort, setSort] = useState<Sort>({column: 'efficiency', direction: 'descending'})
  const [mindCosts, setMindCosts] = useState<Record<string, number>>({})
  const [reload, setReload] = useState(0), [calculating, setCalculating] = useState(false)
  const memory = useRef<{instance: string; plan: CultivationPlan} | null>(null)
  const key = `azurpilot.cultivation-auto.${instance}`
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
  const technologyRows = useMemo(() => new Map(facts ? catalogRows(facts.catalog, facts.technology.ships).map(row => [row.group_type, row]) : []), [facts])
  const ids = useMemo(() => candidates.filter(card => !plan?.excluded_groups.includes(card.group_type!)).map(card => card.id), [candidates, plan?.excluded_groups])
  useEffect(() => {
    if (!facts || facts.dock.instance !== instance || !plan || connection !== 'ready') return
    memory.current = {instance, plan}
    try {localStorage.setItem(key, JSON.stringify(plan))} catch { /* Calculation works without browser storage. */ }
    let active = true
    // Keep the completed view mounted during subsequent recalculations.
    setCalculating(true)
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
    return new Map(candidates.map(card => [card.id, technologyEfficiency(ships.get(card.group_type!), mindCosts)]))
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
    return candidates.filter(card => {
      const technology = technologyRows.get(card.group_type!)
      const search = query.trim()
      return technology && matches(technology, filters, '')
        && (!search || card.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()) || matches(technology, {}, search))
        && (!includedOnly || included.has(card.id))
    }).sort((a, b) => {
      const left = valueFor(a), right = valueFor(b)
      if (left === null && right !== null) return 1
      if (right === null && left !== null) return -1
      const difference = typeof left === 'number' && typeof right === 'number' ? left - right
        : typeof left === 'string' && typeof right === 'string' ? collator.compare(left, right) : 0
      return difference * (sort.direction === 'ascending' ? 1 : -1) || collator.compare(a.name, b.name) || a.id - b.id
    })
  }, [candidates, query, includedOnly, included, sort, estimates, efficiencies, language, filters, technologyRows])
  function sortBy(column: Column) {
    setSort(current => ({column, direction: current.column === column
      ? current.direction === 'ascending' ? 'descending' : 'ascending'
      : column === 'name' ? 'ascending' : 'descending'}))
  }
  function exclude(group: number, excluded: boolean) {
    setPlan(current => current && ({...current, excluded_groups: excluded
      ? [...new Set([...current.excluded_groups, group])] : current.excluded_groups.filter(value => value !== group)}))
  }
  function group(key: string, options: [string, string][], label = tr(key === 'type' ? 'category' : key)) {
    return <div className="ft-filter-row"><strong>{label}</strong><div className="ft-chips">{[['', tr('all')], ...options].map(([value, label]) => <button type="button" key={value} aria-pressed={value ? filters[key]?.includes(value) ?? false : !filters[key]?.length} onClick={() => setFilters(current => ({...current, [key]: value ? current[key]?.includes(value) ? current[key]!.filter(item => item !== value) : [...current[key] ?? [], value] : []}))}>{label}</button>)}</div></div>
  }
  function toggleExtra() {
    setExtra(!extra)
    if (extra) setFilters(current => {
      const next = {...current}
      for (const key of ['getBonusType', 'getAttr', 'levelBonusType', 'levelAttr']) delete next[key]
      return next
    })
  }
  const review = facts?.dock.ships.filter(card => card.review || !card.group_type).length ?? 0
  if (!facts || !plan || !result) return error ? <ErrorBox message={error}/> : <Loading/>
  return <div className="fleet-technology cultivation-calculator">
    {error && <ErrorBox message={error}/>}
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
      <section className="ft-filters" aria-label="养成舰船筛选">
        {group('type', [['front', tr('front')], ['back', tr('back')], ...types.map(value => [String(value), tr(`type.${value}`)] as [string, string])])}
        {group('rarity', rarities.map(value => [value, tr(`rarity.${value}`)]))}
        {group('nation', [...new Set([...technologyRows.values()].filter(row => row.technology).map(row => row.nationality))].sort((a, b) => nationOrder(a) - nationOrder(b) || a - b).map(value => [String(value), tr(`nation.${value}`)]))}
        {group('bonusType', types.map(value => [String(value), tr(`type.${value}`)]))}
        {group('attr', attrs.map(value => [String(value), tr(`attr.${value}`)]))}
        {extra && <>{group('getBonusType', types.map(value => [String(value), tr(`type.${value}`)]), `${tr('unlock')} · ${tr('shipClass')}`)}{group('getAttr', attrs.map(value => [String(value), tr(`attr.${value}`)]), `${tr('unlock')} · ${tr('attr')}`)}{group('levelBonusType', types.map(value => [String(value), tr(`type.${value}`)]), `${tr('completionBonus')} · ${tr('shipClass')}`)}{group('levelAttr', attrs.map(value => [String(value), tr(`attr.${value}`)]), `${tr('completionBonus')} · ${tr('attr')}`)}</>}
        {group('status', ['notTrained120', 'missingStar', 'awakening_pending'].map(key => [key, tr(key)]))}
        <div className="ft-filter-row"><strong>筛选功能</strong><div className="ft-chips"><button type="button" aria-expanded={extra} onClick={toggleExtra}>{tr('extra')}</button></div></div>
      </section>
      <p className="ft-edit-hint">仅计算心智单元和物资。100、105、110、115级均视为尚未支付下一阶段觉醒费用，计入从该阶段至120级的全部剩余费用。</p>
      <p className="ft-edit-hint">每千心智科技点 = 120级奖励科技点 ÷ 100→120级完整心智单元费用 × 1000，数值越高越划算。</p>
      <div className="ft-search"><input type="search" aria-label="搜索养成舰船" placeholder="搜索舰船" value={query} onChange={event => setQuery(event.target.value)}/><span title="搜索与筛选仅影响明细，资源合计按全部参与舰船计算">显示 {rows.length} / {candidates.length} 种</span>
        <label className="cultivation-included"><input type="checkbox" checked={includedOnly} onChange={event => setIncludedOnly(event.target.checked)}/>仅显示参与计算</label>
        <button className="button secondary" disabled={!plan.excluded_groups.length} onClick={() => setPlan({excluded_groups: []})}>恢复全部参与</button>
      </div>
      <div className="ft-table-wrap cultivation-table"><table><thead><tr><th scope="col">参与计算</th>{(Object.entries(columns) as [Column, string][]).map(([column, label]) => <th key={column} scope="col" aria-sort={sort.column === column ? sort.direction : 'none'} title={column === 'efficiency' ? '120级奖励科技点 ÷ 100→120级完整心智单元费用 × 1000；越高越划算' : undefined}><button type="button" className="ft-sort" onClick={() => sortBy(column)}>{label}<span aria-hidden="true">{sort.column === column ? sort.direction === 'ascending' ? '↑' : '↓' : '↕'}</span></button></th>)}<th scope="col">{tr('awakening_pending')}</th></tr></thead><tbody>
        {rows.map(card => {
          const row = estimates.get(card.id), participating = included.has(card.id), efficiency = efficiencies.get(card.id), awakeningPending = technologyRows.get(card.group_type!)?.awakening_pending === true
          return <tr key={card.id} className={participating ? '' : 'cultivation-excluded'}>
            <td><input type="checkbox" aria-label={`参与计算 ${card.name}`} checked={participating} onChange={event => exclude(card.group_type!, !event.target.checked)}/></td>
            <td className="ft-name"><Link to={`?tab=data&ship=${card.id}`}>{card.name}</Link>{participating && row?.status === 'unknown' && <small className="cultivation-warning">{row.notes.join('；')} · <Link to={`?tab=data&ship=${card.id}`}>前往核对</Link></small>}{!participating && <small className="cultivation-note">已排除</small>}</td>
            <td>{card.level || '未知'}</td><td>{amount(row?.mind)}</td><td>{amount(row?.gold)}</td>
            <td title={efficiency?.value == null ? undefined : `${efficiency.points} 科技点 ÷ ${amount(efficiency.mind)} 心智单元 × 1000（100→120级）`}>{efficiency?.value == null ? '—' : efficiency.value.toLocaleString(language, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</td>
            <td className={awakeningPending ? 'ft-awakening-pending' : ''} title="与舰队科技共用同一份标记，可在舰队科技的编辑模式中修改"><span className="ft-mark" aria-label={`${card.name} · ${tr('awakening_pending')} · ${tr(awakeningPending ? 'marked' : 'notMarked')}`}>{awakeningPending ? '✓' : '—'}</span></td>
          </tr>
        })}
      </tbody></table></div>
      {!rows.length && <p>{candidates.length ? '没有符合筛选条件的舰船。' : '当前没有需要计算120级养成费用的舰船。'}</p>}
    </>}
  </div>
}
