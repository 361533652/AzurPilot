import {useEffect, useMemo, useRef, useState} from 'react'
import {Link, useSearchParams} from 'react-router-dom'
import {api} from '../api/client'
import type {DockCard, DockReport} from '../api/types'
import {useApp, useConnection} from '../app/context'
import {ErrorBox, Loading} from '../components/ui'
import type {MindCatalog} from '../mind/types'
import {ShipDetailEditor} from './ShipDetailEditor'
import './fleet-ledger.css'

export function FleetLedger({instance, report, onReport, onBusy, onDirty, locked = false}: {
  instance: string; report?: DockReport; locked?: boolean; onReport: (report: DockReport) => void
  onBusy: (busy: boolean) => void; onDirty: (dirty: boolean) => void
}) {
  const alive = useRef(true)
  useEffect(() => {alive.current = true; return () => {alive.current = false}}, [])
  const {ui} = useApp(), connection = useConnection(), [params, setParams] = useSearchParams()
  const [catalog, setCatalog] = useState<MindCatalog>(), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const groupFilter = params.get('group')
  const [query, setQuery] = useState(''), [filter, setFilter] = useState('all'), [nation, setNation] = useState(''), [type, setType] = useState('')
  const [minimum, setMinimum] = useState(''), [maximum, setMaximum] = useState(''), [sort, setSort] = useState('name'), [page, setPage] = useState(0)
  const [batch, setBatch] = useState(false), [selected, setSelected] = useState<number[]>([]), [adding, setAdding] = useState(false)
  useEffect(() => {
    let active = true
    if (connection === 'ready') void api.request('mind.catalog', {instance}).then(value => {if (active) setCatalog(value)}).catch(e => {if (active) setError((e as Error).message)})
    return () => {active = false}
  }, [instance, connection])
  const infoFor = (row: DockCard) => catalog?.ships.find(ship => ship.name === row.name && ship.group_type === row.group_type)
    ?? catalog?.ships.find(ship => ship.group_type === row.group_type && ship.group !== '改造')
  const counts = new Map<string, number>()
  for (const row of report?.ships ?? []) {const key = String(row.group_type ?? row.name); counts.set(key, (counts.get(key) ?? 0) + 1)}
  const rows = useMemo(() => {
    const counts = new Map<string, number>()
    for (const row of report?.ships ?? []) {const key = String(row.group_type ?? row.name); counts.set(key, (counts.get(key) ?? 0) + 1)}
    const result = (report?.ships ?? []).filter(row => {
      const ship = catalog?.ships.find(ship => ship.name === row.name && ship.group_type === row.group_type)
        ?? catalog?.ships.find(ship => ship.group_type === row.group_type && ship.group !== '改造')
      return (!groupFilter || row.group_type === Number(groupFilter)) && row.name.toLowerCase().includes(query.toLowerCase()) && (!minimum || row.level >= Number(minimum)) && (!maximum || row.level <= Number(maximum))
        && (!nation || (ship as {nation?: string} | undefined)?.nation === nation) && (!type || ship?.type === type)
        && (filter === 'review' ? row.review || row.group_type === null : filter === 'unknown' ? row.extra_exp === null : filter === 'duplicates' ? (counts.get(String(row.group_type ?? row.name)) ?? 0) > 1 : true)
    })
    return result.sort((a, b) => sort === 'level' ? b.level - a.level || a.id - b.id : a.name.localeCompare(b.name) || a.id - b.id)
  }, [report?.ships, catalog, query, minimum, maximum, nation, type, filter, sort, groupFilter])
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
      <label>{ui('analysis.filter')}<select aria-label={ui('analysis.filter')} value={filter} onChange={e => {setFilter(e.target.value); setPage(0)}}>{(['all', 'review', 'unknown', 'duplicates'] as const).map(value => <option key={value} value={value}>{ui(value === 'review' ? 'analysis.needsReview' : `analysis.${value}`)}</option>)}</select></label>
      <label>{ui('analysis.min')}<input type="number" min={0} max={125} value={minimum} onChange={e => {setMinimum(e.target.value); setPage(0)}}/></label><label>{ui('analysis.max')}<input type="number" min={0} max={125} value={maximum} onChange={e => {setMaximum(e.target.value); setPage(0)}}/></label>
      <label>{ui('analysis.nation')}<select aria-label={ui('analysis.nation')} value={nation} onChange={e => {setNation(e.target.value); setPage(0)}}><option value="">{ui('analysis.all')}</option>{[...new Set(catalog?.ships.map(ship => (ship as {nation?: string}).nation || ''))].sort().map(value => <option key={value}>{value}</option>)}</select></label>
      <label>{ui('analysis.type')}<select aria-label={ui('analysis.type')} value={type} onChange={e => {setType(e.target.value); setPage(0)}}><option value="">{ui('analysis.all')}</option>{[...new Set(catalog?.ships.map(ship => ship.type))].sort().map(value => <option key={value}>{value}</option>)}</select></label>
      <label>{ui('analysis.level')} / {ui('analysis.name')}<select value={sort} onChange={e => setSort(e.target.value)}><option value="name">{ui('analysis.name')} ↑</option><option value="level">{ui('analysis.level')} ↓</option></select></label>
      <button className="button secondary" disabled={disabled} aria-pressed={batch} onClick={() => {setBatch(!batch); setSelected([])}}>{ui('analysis.batch')}</button>
      {batch && <button className="button secondary" disabled={disabled || !selected.length} onClick={() => void write(selected.map(id => ({id, review: true})))}>{ui('analysis.batchReview')} ({selected.length})</button>}
    </div>
    <div className="ledger-table-wrap"><table><thead><tr>{batch && <th>{ui('analysis.batch')}</th>}{['name', 'level', 'breakthrough', 'type', 'nation', 'rarity', 'reviewFlag', 'details'].map(value => <th key={value}>{ui(`analysis.${value}` as Parameters<typeof ui>[0])}</th>)}</tr></thead><tbody>{rows.slice(currentPage * 50, (currentPage + 1) * 50).map(row => <tr key={row.id}>
      {batch && <td><input type="checkbox" aria-label={`${ui('analysis.batch')} ${row.name} #${row.id}`} checked={selected.includes(row.id)} onChange={e => setSelected(e.target.checked ? [...selected, row.id] : selected.filter(id => id !== row.id))}/></td>}
      <td>{row.name}{(counts.get(String(row.group_type ?? row.name)) ?? 0) > 1 && <small> · #{row.id}</small>}</td><td>{row.level}</td><td>{(row as DockCard & {breakthrough?: number | null}).breakthrough ?? ui('analysis.unknownValue')}</td><td>{infoFor(row)?.type || '—'}</td><td>{(infoFor(row) as {nation?: string} | undefined)?.nation || '—'}</td><td>{row.rarity}</td><td>{ui(row.review || !row.group_type ? 'analysis.needsReview' : row.extra_exp === null ? 'analysis.missing' : 'analysis.normal')}</td>
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
