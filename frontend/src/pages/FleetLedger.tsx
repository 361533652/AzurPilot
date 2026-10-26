import {useEffect, useId, useMemo, useRef, useState} from 'react'
import {Link, useSearchParams} from 'react-router-dom'
import {api} from '../api/client'
import type {DockCard, DockReport} from '../api/types'
import {useApp, useConnection} from '../app/context'
import {ErrorBox, Loading} from '../components/ui'
import type {CatalogShip, MindCatalog} from '../mind/types'
import {fleetFactionOrder, translateFleetTech} from '../fleet-tech/i18n'
import './fleet-ledger.css'

const nationValue = (ship?: CatalogShip) => ship?.nationality == null ? ship?.nation ?? '' : String(ship.nationality)
const nationOrder = (value: string) => {const index = fleetFactionOrder.indexOf(Number(value)); return index < 0 ? fleetFactionOrder.length : index}
const sortColumns = ['name', 'level', 'breakthrough', 'form', 'type', 'nation', 'rarity', 'reviewFlag'] as const
type SortColumn = typeof sortColumns[number]
type Sort = {column: SortColumn; direction: 'ascending' | 'descending'}
type DraftCard = Omit<DockCard, 'level'> & {level: number | ''}
type Draft = {revision: string; ships: Record<number, DraftCard>; deleted: number[]}
type Changes = Parameters<typeof api.request<'dock.patch'>>[1]['changes']

export function FleetLedger({instance, report, onReport, onBusy, onDirty, locked = false}: {
  instance: string; report?: DockReport; locked?: boolean; onReport: (report: DockReport) => void
  onBusy: (busy: boolean) => void; onDirty: (dirty: boolean) => void
}) {
  const alive = useRef(true), list = useId(), focusedShip = useRef('')
  useEffect(() => {alive.current = true; return () => {alive.current = false}}, [])
  const {ui, language} = useApp(), connection = useConnection(), [params, setParams] = useSearchParams()
  const [catalog, setCatalog] = useState<MindCatalog>(), [error, setError] = useState(''), [busy, setBusy] = useState(false), [saved, setSaved] = useState(false)
  const storageKey = `azurpilot.fleet-ledger.v1.${instance}`
  const [draft, setDraft] = useState<Draft | null>(() => {
    try {
      const value = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null')
      return value && typeof value.revision === 'string' && value.ships && Array.isArray(value.deleted) ? value : null
    } catch {return null}
  })
  const dirty = !!draft && (!!Object.keys(draft.ships).length || !!draft.deleted.length)
  const stale = dirty && !!report && draft.revision !== report.revision
  useEffect(() => {
    try {if (dirty) sessionStorage.setItem(storageKey, JSON.stringify(draft)); else sessionStorage.removeItem(storageKey)} catch { /* Optional browser draft. */ }
  }, [draft, dirty, storageKey])
  useEffect(() => {onDirty(dirty)}, [dirty, onDirty])
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {if (dirty) {event.preventDefault(); event.returnValue = ''}}
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])
  const groupFilter = params.get('group'), shipFilter = params.get('ship')
  const [query, setQuery] = useState(''), [filter, setFilter] = useState('all'), [nation, setNation] = useState(''), [type, setType] = useState('')
  const [minimum, setMinimum] = useState(''), [maximum, setMaximum] = useState(''), [page, setPage] = useState(0)
  const [sort, setSort] = useState<Sort>({column: 'name', direction: 'ascending'})
  const [batch, setBatch] = useState(false), [selected, setSelected] = useState<number[]>([])
  useEffect(() => {
    let active = true
    if (connection === 'ready') void api.request('mind.catalog', {instance}).then(value => {if (active) setCatalog(value)}).catch(e => {if (active) setError((e as Error).message)})
    return () => {active = false}
  }, [instance, connection])
  const infoFor = (row: Pick<DockCard, 'name' | 'group_type'>) => catalog?.ships.find(ship => ship.name === row.name && ship.group_type === row.group_type)
    ?? catalog?.ships.find(ship => ship.group_type === row.group_type && ship.group !== '改造')
  const nationName = (value: string) => {
    const key = `nation.${value}`, label = translateFleetTech(language, key)
    return label === key ? value : label
  }
  const nations = [...new Set(catalog?.ships.map(nationValue).filter(Boolean))]
    .sort((a, b) => nationOrder(a) - nationOrder(b) || a.localeCompare(b))
  const counts = new Map<string, number>()
  for (const row of report?.ships ?? []) {const key = String(row.group_type ?? row.name); counts.set(key, (counts.get(key) ?? 0) + 1)}
  // Keep committed rows in place while typing, including when an edit no longer matches a filter.
  const rows = useMemo(() => {
    const counts = new Map<string, number>()
    const shipInfo = new Map<number, CatalogShip | undefined>()
    for (const row of report?.ships ?? []) {const key = String(row.group_type ?? row.name); counts.set(key, (counts.get(key) ?? 0) + 1)}
    const result = (report?.ships ?? []).filter(row => {
      const ship = catalog?.ships.find(ship => ship.name === row.name && ship.group_type === row.group_type)
        ?? catalog?.ships.find(ship => ship.group_type === row.group_type && ship.group !== '改造')
      shipInfo.set(row.id, ship)
      return (!groupFilter || row.group_type === Number(groupFilter)) && (row.name.toLowerCase().includes(query.toLowerCase()) || String(row.id) === query) && (!minimum || row.level >= Number(minimum)) && (!maximum || row.level <= Number(maximum))
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
        case 'form': return row.form || null
        case 'type': return ship?.type || null
        case 'nation': return nationValue(ship) ? nationOrder(nationValue(ship)) : null
        case 'rarity': return row.rarity ? ['N', 'R', 'SR', 'SSR', 'UR'].indexOf(row.rarity) : null
        case 'reviewFlag': return row.review || !row.group_type ? 2 : row.level === 0 ? 1 : 0
      }
    }
    return result.sort((a, b) => {
      const left = sortValue(a), right = sortValue(b)
      if (left === null && right !== null) return 1
      if (right === null && left !== null) return -1
      const difference = (typeof left === 'number' && typeof right === 'number' ? left - right
        : typeof left === 'string' && typeof right === 'string' ? collator.compare(left, right) : 0
      ) || (sort.column === 'nation' ? collator.compare(nationName(nationValue(shipInfo.get(a.id))), nationName(nationValue(shipInfo.get(b.id)))) : 0)
      return difference * (sort.direction === 'ascending' ? 1 : -1)
        || collator.compare(a.name, b.name) || a.id - b.id
    })
  }, [report?.ships, catalog, query, minimum, maximum, nation, type, filter, sort, groupFilter, language])
  const additions = Object.values(draft?.ships ?? {}).filter(row => row.id < 0)
  const displayRows: DraftCard[] = [...additions, ...rows]
  const pages = Math.max(1, Math.ceil(displayRows.length / 50)), currentPage = Math.min(page, pages - 1)
  useEffect(() => {
    if (!shipFilter || focusedShip.current === shipFilter || !report || !catalog) return
    const index = displayRows.findIndex(row => row.id === Number(shipFilter))
    if (index >= 0) {setPage(Math.floor(index / 50)); focusedShip.current = shipFilter}
  }, [shipFilter, report, catalog, displayRows])
  useEffect(() => {
    if (shipFilter) document.getElementById(`ledger-ship-${shipFilter}`)?.scrollIntoView?.({block: 'nearest'})
  }, [shipFilter, currentPage, catalog])
  function sortBy(column: SortColumn) {
    setSort(current => ({column, direction: current.column === column
      ? current.direction === 'ascending' ? 'descending' : 'ascending'
      : column === 'level' || column === 'rarity' ? 'descending' : 'ascending'}))
    setPage(0)
  }
  const disabled = locked || busy || connection !== 'ready' || !report
  function change(row: DraftCard, values: Partial<DraftCard>) {
    if (disabled || !report) return
    setSaved(false)
    setDraft(current => {
      const ships = {...current?.ships}, deleted = current?.deleted ?? []
      const next = {...row, ...current?.ships[row.id], ...values}
      const original = report.ships.find(ship => ship.id === row.id)
      if (original && (['name', 'group_type', 'level', 'rarity', 'base_rarity', 'full_star', 'form', 'review'] as const)
        .every(key => (next[key] ?? null) === (original[key] ?? null))) delete ships[row.id]
      else ships[row.id] = next
      return Object.keys(ships).length || deleted.length ? {revision: current?.revision ?? report.revision, ships, deleted} : null
    })
  }
  function rename(row: DraftCard, name: string) {
    const matches = catalog?.ships.filter(info => info.name === name) ?? []
    const info = matches.length === 1 ? matches[0] : undefined
    change(row, {name, group_type: info?.group_type ?? null, rarity: info?.rarity ?? row.rarity,
      base_rarity: info?.base_rarity ?? row.base_rarity, form: info ? info.group === '改造' ? 'retrofit' : 'base' : null, review: !info})
  }
  function changeForm(row: DraftCard, form: DockCard['form']) {
    const info = catalog?.ships.find(info => info.group_type === row.group_type && (form === 'retrofit' ? info.group === '改造' : info.group !== '改造'))
    change(row, {form, ...(info ? {name: info.name, rarity: info.rarity, base_rarity: info.base_rarity} : {}), review: form === 'retrofit' && !info || row.review})
  }
  function add() {
    if (disabled || !report) return
    const id = Math.min(-1, ...Object.keys(draft?.ships ?? {}).map(Number)) - 1
    change({id, name: '', group_type: null, level: 1, rarity: 'N', base_rarity: 'N', extra_exp: null,
      full_star: null, form: null, review: true, excluded: false, source: 'manual'}, {})
    setPage(0)
  }
  function remove(row: DraftCard) {
    if (disabled || !report) return
    setSaved(false)
    setDraft(current => {
      const ships = {...current?.ships}, deleted = current?.deleted ?? []
      if (row.id < 0) delete ships[row.id]
      const nextDeleted = row.id < 0 ? deleted : deleted.includes(row.id) ? deleted.filter(id => id !== row.id) : [...deleted, row.id]
      return Object.keys(ships).length || nextDeleted.length ? {revision: current?.revision ?? report.revision, ships, deleted: nextDeleted} : null
    })
  }
  function discard() {setDraft(null); setError(''); setSaved(false); setSelected([])}
  async function save() {
    if (!report || !draft || !dirty || disabled || stale) return
    const changes: Changes = []
    for (const row of Object.values(draft.ships)) {
      if (draft.deleted.includes(row.id)) continue
      if (!row.name.trim() || row.name.length > 100 || row.level === '' || !Number.isInteger(row.level) || row.level < 0 || row.level > 125) {setError(ui('analysis.inlineInvalid')); return}
      changes.push({id: row.id > 0 ? row.id : undefined, name: row.name, group_type: row.group_type, level: row.level,
        rarity: row.rarity as Changes[number]['rarity'], base_rarity: row.base_rarity as Changes[number]['base_rarity'],
        full_star: row.full_star ?? null, form: row.form ?? null, review: row.review,
        ...(row.id < 0 ? {extra_exp: null, excluded: false, source: 'manual'} : {})})
    }
    changes.push(...draft.deleted.map(id => ({id, delete: true})))
    setBusy(true); onBusy(true); setError('')
    try {
      const value = await api.request('dock.patch', {instance, revision: draft.revision, changes})
      if (alive.current) {onReport(value); setDraft(null); setSelected([]); setSaved(true)}
    } catch (e) {if (alive.current) setError((e as Error).message)}
    finally {if (alive.current) {setBusy(false); onBusy(false)}}
  }
  const changedCount = new Set([...Object.keys(draft?.ships ?? {}).map(Number), ...draft?.deleted ?? []]).size
  return <div className="fleet-ledger">
    {error && <ErrorBox message={error}/>}{!report && <Loading/>}
    <div className="mind-toolbar" role="status"><strong>{ui('analysis.confirmedCount')}: {report?.ships.length ?? 0}</strong><span>{ui('analysis.species')}: {counts.size}</span><span>{ui('analysis.needsReview')}: {report?.ships.filter(row => row.review || !row.group_type).length ?? 0}</span><span>{ui('analysis.time')}: {report?.updated_at || '—'}</span></div>
    <div className="mind-toolbar ledger-savebar">
      <button className="button primary" disabled={disabled || !dirty || stale} onClick={() => void save()}>{ui('analysis.save')}</button>
      {dirty && <><span role="status">{ui('analysis.unsavedChanges')}: {changedCount}</span><button className="button secondary" disabled={busy || locked} onClick={discard}>{ui('analysis.discard')}</button></>}
      {saved && <span role="status">{ui('analysis.saved')}</span>}
      <button className="button secondary" disabled={disabled} onClick={add}>{ui('analysis.add')}</button>
      <Link aria-disabled={disabled} onClick={event => {if (disabled) event.preventDefault()}} className="button secondary" to="?tab=scan">{ui('analysis.scanPage')}</Link>
      {!!report?.pending_changes.length && <Link aria-disabled={disabled} onClick={event => {if (disabled) event.preventDefault()}} to="?tab=scan">{ui('analysis.pendingLink')} ({report.pending_changes.length})</Link>}
    </div>
    {stale && <p role="alert">{ui('analysis.stale')}</p>}
    <p className="ledger-hint">{ui('analysis.inlineHint')} {ui('analysis.fullStarRule')}</p>
    {groupFilter && <p>{ui('analysis.filter')}: {report?.ships.find(row => row.group_type === Number(groupFilter))?.name || groupFilter} <button className="button secondary" onClick={() => {const next = new URLSearchParams(params); next.delete('group'); setParams(next, {replace: true})}}>{ui('analysis.all')}</button></p>}
    <div className="mind-toolbar">
      <label>{ui('analysis.search')}<input type="search" value={query} onChange={e => {setQuery(e.target.value); setPage(0)}}/></label>
      <label>{ui('analysis.filter')}<select aria-label={ui('analysis.filter')} value={filter} onChange={e => {setFilter(e.target.value); setPage(0)}}>{(['all', 'review', 'duplicates'] as const).map(value => <option key={value} value={value}>{ui(value === 'review' ? 'analysis.needsReview' : `analysis.${value}`)}</option>)}</select></label>
      <label>{ui('analysis.min')}<input type="number" min={0} max={125} value={minimum} onChange={e => {setMinimum(e.target.value); setPage(0)}}/></label><label>{ui('analysis.max')}<input type="number" min={0} max={125} value={maximum} onChange={e => {setMaximum(e.target.value); setPage(0)}}/></label>
      <label>{ui('analysis.nation')}<select aria-label={ui('analysis.nation')} value={nation} onChange={e => {setNation(e.target.value); setPage(0)}}><option value="">{ui('analysis.all')}</option>{nations.map(value => <option key={value} value={value}>{nationName(value)}</option>)}</select></label>
      <label>{ui('analysis.type')}<select aria-label={ui('analysis.type')} value={type} onChange={e => {setType(e.target.value); setPage(0)}}><option value="">{ui('analysis.all')}</option>{[...new Set(catalog?.ships.map(ship => ship.type))].sort().map(value => <option key={value}>{value}</option>)}</select></label>
      <button className="button secondary" disabled={disabled} aria-pressed={batch} onClick={() => {setBatch(!batch); setSelected([])}}>{ui('analysis.batch')}</button>
      {batch && <button className="button secondary" disabled={disabled || !selected.length} onClick={() => {for (const id of selected) {const row = draft?.ships[id] ?? report?.ships.find(row => row.id === id); if (row && !draft?.deleted.includes(id)) change(row, {review: true})}}}>{ui('analysis.batchReview')} ({selected.length})</button>}
    </div>
    <datalist id={list}>{catalog?.ships.map(info => <option key={`${info.group_type}:${info.name}`} value={info.name}/>)}</datalist>
    <div className="ledger-table-wrap"><table><thead><tr>{batch && <th scope="col">{ui('analysis.batch')}</th>}{sortColumns.map(column => <th key={column} scope="col" aria-sort={sort.column === column ? sort.direction : 'none'}><button type="button" className="ledger-sort" onClick={() => sortBy(column)}>{ui(`analysis.${column}`)}<span aria-hidden="true">{sort.column === column ? sort.direction === 'ascending' ? '↑' : '↓' : '↕'}</span></button></th>)}<th scope="col">{ui('analysis.delete')}</th></tr></thead><tbody>{displayRows.slice(currentPage * 50, (currentPage + 1) * 50).map(original => {
      const row = draft?.ships[original.id] ?? original, deleted = draft?.deleted.includes(row.id), info = infoFor(row)
      const label = (key: string) => `${ui(key)} · ${row.name || ui('analysis.new')} #${row.id}`
      return <tr key={row.id} id={`ledger-ship-${row.id}`} className={[deleted ? 'ledger-deleted' : '', draft?.ships[row.id] ? 'ledger-modified' : '', shipFilter === String(row.id) ? 'ledger-highlight' : ''].filter(Boolean).join(' ')}>
        {batch && <td><input type="checkbox" disabled={disabled || deleted} aria-label={label('analysis.batch')} checked={selected.includes(row.id)} onChange={e => setSelected(e.target.checked ? [...selected, row.id] : selected.filter(id => id !== row.id))}/></td>}
        <td><input className="ledger-name" list={list} aria-label={label('analysis.name')} maxLength={100} value={row.name} disabled={disabled || deleted || !catalog} onChange={e => rename(row, e.target.value)}/>{row.id > 0 && (counts.get(String(row.group_type ?? row.name)) ?? 0) > 1 && <small> · #{row.id}</small>}{row.id < 0 && <small> · {ui('analysis.new')}</small>}</td>
        <td><input className="ledger-level" type="number" min={0} max={125} step={1} aria-label={label('analysis.level')} value={row.level} disabled={disabled || deleted} onChange={e => change(row, {level: e.target.value === '' ? '' : Number(e.target.value)})}/></td>
        <td><select aria-label={label('analysis.breakthrough')} value={row.full_star == null ? '' : String(row.full_star)} disabled={disabled || deleted} onChange={e => change(row, {full_star: e.target.value === '' ? null : e.target.value === 'true'})}><option value="">{ui('analysis.unknownValue')}</option><option value="false">{ui('analysis.fullStarNo')}</option><option value="true">{ui('analysis.fullStarYes')}</option></select></td>
        <td><select aria-label={label('analysis.form')} value={row.form ?? ''} disabled={disabled || deleted || !catalog} onChange={e => changeForm(row, e.target.value as DockCard['form'] || null)}><option value="">{ui('analysis.unknownValue')}</option><option value="base">{ui('analysis.base')}</option><option value="retrofit" disabled={!catalog?.ships.some(ship => ship.group_type === row.group_type && ship.group === '改造')}>{ui('analysis.retrofit')}</option></select></td>
        <td>{info?.type || '—'}</td><td>{nationName(nationValue(info)) || '—'}</td><td>{row.rarity}</td>
        <td><label className="ledger-review"><input type="checkbox" aria-label={label('analysis.reviewFlag')} checked={row.review || !row.group_type} disabled={disabled || deleted || !row.group_type} onChange={e => change(row, {review: e.target.checked})}/>{ui(row.review || !row.group_type ? 'analysis.needsReview' : row.level === 0 || row.level === '' ? 'analysis.missing' : 'analysis.normal')}</label></td>
        <td><button className="button secondary" disabled={disabled} onClick={() => remove(row)}>{ui(deleted ? 'analysis.restore' : 'analysis.delete')}</button>{deleted && <small> {ui('analysis.deleted')}</small>}</td>
      </tr>
    })}</tbody></table></div>
    {!displayRows.length && <p>{ui('analysis.emptyLedger')}</p>}
    <div className="mind-toolbar"><button className="button secondary" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>{ui('analysis.previous')}</button><span>{currentPage + 1} / {pages} · {displayRows.length}</span><button className="button secondary" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>{ui('analysis.next')}</button></div>
  </div>
}
