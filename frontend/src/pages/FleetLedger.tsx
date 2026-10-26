import {useEffect, useMemo, useRef, useState} from 'react'
import {Link, useSearchParams} from 'react-router-dom'
import {api} from '../api/client'
import type {DockCard, DockReport} from '../api/types'
import {useApp, useConnection} from '../app/context'
import {ErrorBox, Loading} from '../components/ui'
import type {CatalogShip, MindCatalog} from '../mind/types'
import {fleetFactionOrder, translateFleetTech} from '../fleet-tech/i18n'
import {ShipDetailEditor} from './ShipDetailEditor'
import './fleet-ledger.css'

const nationValue = (ship?: CatalogShip) => ship?.nationality == null ? ship?.nation ?? '' : String(ship.nationality)
const nationOrder = (value: string) => {const index = fleetFactionOrder.indexOf(Number(value)); return index < 0 ? fleetFactionOrder.length : index}
const sortColumns = ['name', 'level', 'breakthrough', 'type', 'nation', 'rarity', 'reviewFlag'] as const
type SortColumn = typeof sortColumns[number]
type Sort = {column: SortColumn; direction: 'ascending' | 'descending'}

export function FleetLedger({instance, report, onReport, onBusy, onDirty, locked = false}: {
  instance: string; report?: DockReport; locked?: boolean; onReport: (report: DockReport) => void
  onBusy: (busy: boolean) => void; onDirty: (dirty: boolean) => void
}) {
  const alive = useRef(true)
  useEffect(() => {alive.current = true; return () => {alive.current = false}}, [])
  const {ui, language} = useApp(), connection = useConnection(), [params, setParams] = useSearchParams()
  const [catalog, setCatalog] = useState<MindCatalog>(), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const groupFilter = params.get('group')
  const [query, setQuery] = useState(''), [filter, setFilter] = useState('all'), [nation, setNation] = useState(''), [type, setType] = useState('')
  const [minimum, setMinimum] = useState(''), [maximum, setMaximum] = useState(''), [page, setPage] = useState(0)
  const [sort, setSort] = useState<Sort>({column: 'name', direction: 'ascending'})
  const [batch, setBatch] = useState(false), [selected, setSelected] = useState<number[]>([]), [adding, setAdding] = useState(false)
  useEffect(() => {
    let active = true
    if (connection === 'ready') void api.request('mind.catalog', {instance}).then(value => {if (active) setCatalog(value)}).catch(e => {if (active) setError((e as Error).message)})
    return () => {active = false}
  }, [instance, connection])
  const infoFor = (row: DockCard) => catalog?.ships.find(ship => ship.name === row.name && ship.group_type === row.group_type)
    ?? catalog?.ships.find(ship => ship.group_type === row.group_type && ship.group !== '改造')
  const nationName = (value: string) => {
    const key = `nation.${value}`, label = translateFleetTech(language, key)
    return label === key ? value : label
  }
  const nations = [...new Set(catalog?.ships.map(nationValue).filter(Boolean))]
    .sort((a, b) => nationOrder(a) - nationOrder(b) || a.localeCompare(b))
  const counts = new Map<string, number>()
  for (const row of report?.ships ?? []) {const key = String(row.group_type ?? row.name); counts.set(key, (counts.get(key) ?? 0) + 1)}
  const rows = useMemo(() => {
    const counts = new Map<string, number>()
    const shipInfo = new Map<number, CatalogShip | undefined>()
    for (const row of report?.ships ?? []) {const key = String(row.group_type ?? row.name); counts.set(key, (counts.get(key) ?? 0) + 1)}
    const result = (report?.ships ?? []).filter(row => {
      const ship = catalog?.ships.find(ship => ship.name === row.name && ship.group_type === row.group_type)
        ?? catalog?.ships.find(ship => ship.group_type === row.group_type && ship.group !== '改造')
      shipInfo.set(row.id, ship)
      return (!groupFilter || row.group_type === Number(groupFilter)) && row.name.toLowerCase().includes(query.toLowerCase()) && (!minimum || row.level >= Number(minimum)) && (!maximum || row.level <= Number(maximum))
        && (!nation || nationValue(ship) === nation) && (!type || ship?.type === type)
        && (filter === 'review' ? row.review || row.group_type === null : filter === 'duplicates' ? (counts.get(String(row.group_type ?? row.name)) ?? 0) > 1 : true)
    })
    const collator = new Intl.Collator(language, {numeric: true})
    function sortValue(row: DockCard): string | number | null {
      const ship = shipInfo.get(row.id)
      switch (sort.column) {
        case 'name': return row.name
        case 'level': return row.level || null
        case 'breakthrough': return row.full_star == null ? null : Number(row.full_star)
        case 'type': return ship?.type || null
        case 'nation': return nationValue(ship) ? nationOrder(nationValue(ship)) : null
        case 'rarity': return row.rarity ? ['N', 'R', 'SR', 'SSR', 'UR'].indexOf(row.rarity) : null
        case 'reviewFlag': return row.review || !row.group_type ? 2 : row.level === 0 ? 1 : 0
      }
    }
    return result.sort((a, b) => {
      const left = sortValue(a), right = sortValue(b)
      // Unknown values stay at the end in either direction.
      if (left === null && right !== null) return 1
      if (right === null && left !== null) return -1
      const difference = (typeof left === 'number' && typeof right === 'number' ? left - right
        : typeof left === 'string' && typeof right === 'string' ? collator.compare(left, right) : 0
      ) || (sort.column === 'nation' ? collator.compare(nationName(nationValue(shipInfo.get(a.id))), nationName(nationValue(shipInfo.get(b.id)))) : 0)
      return difference * (sort.direction === 'ascending' ? 1 : -1)
        || collator.compare(a.name, b.name) || a.id - b.id
    })
  }, [report?.ships, catalog, query, minimum, maximum, nation, type, filter, sort, groupFilter, language])
  function sortBy(column: SortColumn) {
    setSort(current => ({column, direction: current.column === column
      ? current.direction === 'ascending' ? 'descending' : 'ascending'
      : column === 'level' || column === 'rarity' ? 'descending' : 'ascending'}))
    setPage(0)
  }
  const pages = Math.max(1, Math.ceil(rows.length / 50)), currentPage = Math.min(page, pages - 1)
  const disabled = locked || busy || connection !== 'ready' || !report
  const ship = adding ? {id: -1, name: '', group_type: null, level: 1, rarity: 'N', base_rarity: 'N', extra_exp: null, review: true, source: 'manual', excluded: false} as DockCard : report?.ships.find(row => String(row.id) === params.get('ship'))
  function close() {setAdding(false); onDirty(false); const next = new URLSearchParams(params); next.delete('ship'); next.delete('edit'); setParams(next, {replace: true})}
  async function write(changes: Parameters<typeof api.request<'dock.patch'>>[1]['changes']) {
    if (!report || disabled) return false
    setBusy(true); onBusy(true); setError('')
    try {const value = await api.request('dock.patch', {instance, revision: report.revision, changes}); if (alive.current) {onReport(value); close(); setSelected([])} return true}
    catch (e) {if (alive.current) setError((e as Error).message); return false}
    finally {if (alive.current) {setBusy(false); onBusy(false)}}
  }
  return <div className="fleet-ledger">
    {error && <ErrorBox message={error}/>}{!report && <Loading/>}
    <div className="mind-toolbar" role="status"><strong>{ui('analysis.confirmedCount')}: {report?.ships.length ?? 0}</strong><span>{ui('analysis.species')}: {counts.size}</span><span>{ui('analysis.needsReview')}: {report?.ships.filter(row => row.review || !row.group_type).length ?? 0}</span><span>{ui('analysis.time')}: {report?.updated_at || '—'}</span></div>
    <div className="mind-toolbar"><button className="button primary" disabled={disabled} onClick={() => {setAdding(true); onDirty(true)}}>{ui('analysis.add')}</button><Link aria-disabled={disabled} onClick={event => {if (disabled) event.preventDefault()}} className="button secondary" to="?tab=scan">{ui('analysis.scanPage')}</Link>{!!report?.pending_changes.length && <Link aria-disabled={disabled} onClick={event => {if (disabled) event.preventDefault()}} to="?tab=scan">{ui('analysis.pendingLink')} ({report.pending_changes.length})</Link>}</div>
    {groupFilter && <p>{ui('analysis.filter')}: {report?.ships.find(row => row.group_type === Number(groupFilter))?.name || groupFilter} <button className="button secondary" onClick={() => {const next = new URLSearchParams(params); next.delete('group'); setParams(next, {replace: true})}}>{ui('analysis.all')}</button></p>}
    <div className="mind-toolbar">
      <label>{ui('analysis.search')}<input type="search" value={query} onChange={e => {setQuery(e.target.value); setPage(0)}}/></label>
      <label>{ui('analysis.filter')}<select aria-label={ui('analysis.filter')} value={filter} onChange={e => {setFilter(e.target.value); setPage(0)}}>{(['all', 'review', 'duplicates'] as const).map(value => <option key={value} value={value}>{ui(value === 'review' ? 'analysis.needsReview' : `analysis.${value}`)}</option>)}</select></label>
      <label>{ui('analysis.min')}<input type="number" min={0} max={125} value={minimum} onChange={e => {setMinimum(e.target.value); setPage(0)}}/></label><label>{ui('analysis.max')}<input type="number" min={0} max={125} value={maximum} onChange={e => {setMaximum(e.target.value); setPage(0)}}/></label>
      <label>{ui('analysis.nation')}<select aria-label={ui('analysis.nation')} value={nation} onChange={e => {setNation(e.target.value); setPage(0)}}><option value="">{ui('analysis.all')}</option>{nations.map(value => <option key={value} value={value}>{nationName(value)}</option>)}</select></label>
      <label>{ui('analysis.type')}<select aria-label={ui('analysis.type')} value={type} onChange={e => {setType(e.target.value); setPage(0)}}><option value="">{ui('analysis.all')}</option>{[...new Set(catalog?.ships.map(ship => ship.type))].sort().map(value => <option key={value}>{value}</option>)}</select></label>
      <button className="button secondary" disabled={disabled} aria-pressed={batch} onClick={() => {setBatch(!batch); setSelected([])}}>{ui('analysis.batch')}</button>
      {batch && <button className="button secondary" disabled={disabled || !selected.length} onClick={() => void write(selected.map(id => ({id, review: true})))}>{ui('analysis.batchReview')} ({selected.length})</button>}
    </div>
    <div className="ledger-table-wrap"><table><thead><tr>{batch && <th scope="col">{ui('analysis.batch')}</th>}{sortColumns.map(column => <th key={column} scope="col" aria-sort={sort.column === column ? sort.direction : 'none'}><button type="button" className="ledger-sort" onClick={() => sortBy(column)}>{ui(`analysis.${column}`)}<span aria-hidden="true">{sort.column === column ? sort.direction === 'ascending' ? '↑' : '↓' : '↕'}</span></button></th>)}<th scope="col">{ui('analysis.details')}</th></tr></thead><tbody>{rows.slice(currentPage * 50, (currentPage + 1) * 50).map(row => <tr key={row.id}>
      {batch && <td><input type="checkbox" aria-label={`${ui('analysis.batch')} ${row.name} #${row.id}`} checked={selected.includes(row.id)} onChange={e => setSelected(e.target.checked ? [...selected, row.id] : selected.filter(id => id !== row.id))}/></td>}
      <td>{row.name}{(counts.get(String(row.group_type ?? row.name)) ?? 0) > 1 && <small> · #{row.id}</small>}</td><td>{row.level}</td><td>{ui(row.full_star === true ? 'analysis.fullStarYes' : row.full_star === false ? 'analysis.fullStarNo' : 'analysis.unknownValue')}</td><td>{infoFor(row)?.type || '—'}</td><td>{nationName(nationValue(infoFor(row))) || '—'}</td><td>{row.rarity}</td><td>{ui(row.review || !row.group_type ? 'analysis.needsReview' : row.level === 0 ? 'analysis.missing' : 'analysis.normal')}</td>
      <td><Link aria-disabled={disabled} onClick={event => {if (disabled) event.preventDefault()}} to={`?tab=data&ship=${row.id}`}>{ui('analysis.details')}</Link>{' · '}<Link aria-disabled={disabled} onClick={event => {if (disabled) event.preventDefault()}} to={`?tab=data&ship=${row.id}&edit=1`}>{ui('analysis.edit')}</Link></td>
    </tr>)}</tbody></table></div>
    {!rows.length && <p>{ui('analysis.emptyLedger')}</p>}
    <div className="mind-toolbar"><button className="button secondary" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>{ui('analysis.previous')}</button><span>{currentPage + 1} / {pages} · {rows.length}</span><button className="button secondary" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>{ui('analysis.next')}</button></div>
    {ship && report && <ShipDetailEditor key={`${ship.id}:${params.get('edit')}`} ship={ship} report={report} catalog={catalog} disabled={disabled} initiallyEditing={adding || params.get('edit') === '1'} onClose={close} onSave={async value => {
      if (!value.name.trim() || !Number.isInteger(value.level) || value.level < 0 || value.level > 125 || value.extra_exp !== null && (!Number.isSafeInteger(value.extra_exp) || value.extra_exp < 0)) {setError(ui('analysis.invalid')); return false}
      return await write([{...value, id: value.id > 0 ? value.id : undefined, rarity: value.rarity as 'N', base_rarity: value.base_rarity as 'N'}])
    }} onRemove={async () => {await write([{id: ship.id, delete: true}])}}/>}
  </div>
}
