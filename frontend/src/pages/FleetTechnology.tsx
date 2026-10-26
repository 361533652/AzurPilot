import {useEffect, useMemo, useRef, useState} from 'react'
import {Link, useParams, useSearchParams} from 'react-router-dom'
import {api, ApiError} from '../api/client'
import {useApp, useConnection} from '../app/context'
import {ErrorBox, Loading, PageTitle} from '../components/ui'
import {Select} from '../components/FormControls'
import {translateFleetTech} from '../fleet-tech/i18n'
import {catalogRows, changeMilestone, matches, milestones, remainingPoints, shipType, type Bonuses, type Catalog, type Change, type Report, type Row, type State} from '../fleet-tech/types'
import '../fleet-tech/style.css'

const types = [1, 2, 3, 18, 4, 5, 7, 6, 13, 12, 10, 8, 17, 19, 22]
const attrs = [1, 2, 3, 4, 5, 6, 9, 8, 12]
const stageLabel = {owned: 'get', full_star: 'star', lv120: 'Lv.120'}
const stateKey = (value: State) => value === null ? 'unverified' : value ? 'finished' : 'notFinished'
const stateValue = (value: string): State => value === '' ? null : value === 'true'

export function TechnologyTable({rows, language, tr, onSelect}: {rows: Row[]; language: string; tr: (key: string) => string; onSelect: (row: Row) => void}) {
  function effect(row: Row, stage: 'get' | 'level') {
    const t = row.technology
    return t && `${[...new Set(t[`add_${stage}_shiptype`].map(shipType))].map(type => tr(`type.${type}`)).join('/')} ${tr(`attr.${t[`add_${stage}_attr`]}`)}+${t[`add_${stage}_value`]}`
  }
  return <div className="ft-table-wrap"><table><thead><tr>{['name', 'held', 'get', 'star', 'Lv.120', 'points', 'remaining'].map(key => <th key={key}>{tr(key)}</th>)}</tr></thead><tbody>{rows.map(row => {
    const t = row.technology, total = t ? t.pt_get + t.pt_upgrage + t.pt_level : 0
    return <tr key={row.group_type} data-group={row.group_type}><td className={`ft-name ft-rarity-${row.rarity}`}><button className="ft-state-button" onClick={() => onSelect(row)}>{row.names[language === 'en-US' ? 'en' : language === 'ja-JP' ? 'jp' : language === 'zh-TW' ? 'tw' : 'cn'] || row.name}</button><small>{tr(`nation.${row.nationality}`)} · {tr(`type.${shipType(row.type)}`)}</small>{Object.keys(row.conflicts ?? {}).length > 0 && <small role="status">{tr('conflictingFacts')}</small>}</td>
      <td>{row.current_count > 0 ? <Link to={`?tab=data&group=${row.group_type}`}>{row.current_count} · {row.current_level === null ? tr('unknown') : `Lv.${row.current_level}`}</Link> : tr('notHeld')}</td>
      {milestones.map(key => <td key={key}>{t ? <button className={`ft-milestone ft-state-${stateKey(row[key])}`} onClick={() => onSelect(row)} aria-label={`${row.name} · ${tr(stageLabel[key])} · ${tr(stateKey(row[key]))}`}>{tr(stateKey(row[key]))}</button> : '—'}</td>)}
      <td>{t ? `${row.points} / ${total}` : '—'}</td><td>{t ? <>{remainingPoints(row)} {tr('points')}{row.owned !== true && <small>{effect(row, 'get')}</small>}{row.lv120 !== true && <small>{effect(row, 'level')}</small>}</> : '—'}</td></tr>
  })}</tbody></table></div>
}

export function FleetTechnology() {
  const [search] = useSearchParams(), targetGroup = search.get('group')
  const openedGroup = useRef('')
  const {instance = ''} = useParams(), {language, ui, notify} = useApp(), connection = useConnection()
  const tr = (key: string) => key === 'Lv.120' ? key : translateFleetTech(language, key)
  const [catalog, setCatalog] = useState<Catalog>(), [report, setReport] = useState<Report>()
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [conflict, setConflict] = useState(false)
  const [query, setQuery] = useState(''), [filters, setFilters] = useState<Record<string, string[]>>({}), [page, setPage] = useState(0)
  const [selected, setSelected] = useState<number>(), [draft, setDraft] = useState<Change>()
  const [bonuses, setBonuses] = useState(false)
  const scope = useRef(0), writing = useRef(false), correction = useRef<HTMLElement>(null)
  useEffect(() => {if (selected !== undefined) {correction.current?.scrollIntoView({behavior: 'smooth', block: 'center'}); correction.current?.focus({preventScroll: true})}}, [selected])
  const rows = useMemo(() => catalog ? catalogRows(catalog, report?.ships ?? []) : [], [catalog, report])
  const row = rows.find(value => value.group_type === selected)
  useEffect(() => {
    const key = `${instance}:${targetGroup}`
    if (targetGroup && openedGroup.current !== key && rows.some(value => String(value.group_type) === targetGroup)) {
      openedGroup.current = key; setSelected(Number(targetGroup)); setDraft({group_type: Number(targetGroup)})
    } else if (!targetGroup) openedGroup.current = ''
  }, [targetGroup, instance, rows])
  async function reload() {
    const token = scope.current
    try {
      const [data, value] = await Promise.all([api.request('fleetTech.catalog', {instance}), api.request('fleetTech.report', {instance})])
      if (scope.current === token) {setCatalog(data); setReport(value); setError('')}
    } catch (e) {if (scope.current === token) setError((e as Error).message)}
  }
  useEffect(() => {
    scope.current++; openedGroup.current = ''; setCatalog(undefined); setReport(undefined); setSelected(undefined); setDraft(undefined); setError(''); setBusy(false); writing.current = false
    if (connection === 'ready') void reload()
    return () => {scope.current++}
  }, [instance, connection])
  async function save(changes: Change[], nations: Record<string, number | null> = {}) {
    if (!report || writing.current) return
    const token = scope.current
    writing.current = true; setBusy(true); setError(''); setConflict(false)
    try {
      const value = await api.request('fleetTech.patch', {instance, revision: report.revision, changes, nations})
      if (token === scope.current) {setReport(value); setDraft(undefined); notify(tr('saved'))}
    } catch (e) {
      if (token === scope.current) {setError((e as Error).message); setConflict(e instanceof ApiError && e.code === 'CONFLICT')}
    } finally {if (token === scope.current) {writing.current = false; setBusy(false)}}
  }
  function select(value: Row) {setSelected(value.group_type); setDraft({group_type: value.group_type}); setConflict(false); setError('')}
  function group(key: string, options: [string, string][]) {
    return <div className="ft-filter-row"><strong>{tr(key === 'type' ? 'category' : key)}</strong><div className="ft-chips">{[['', tr('all')], ...options].map(([value, label]) => <button key={value} aria-pressed={value ? filters[key]?.includes(value) ?? false : !filters[key]?.length} onClick={() => {setFilters(previous => ({...previous, [key]: value ? previous[key]?.includes(value) ? previous[key]!.filter(item => item !== value) : [...previous[key] ?? [], value] : []})); setPage(0)}}>{label}</button>)}</div></div>
  }
  function bonusList(values: Bonuses) {return <div className="ft-bonus-grid">{Object.entries(values).map(([type, effects]) => <div key={type}><b>{tr(`type.${type}`)}</b> {Object.entries(effects).map(([attr, value]) => `${tr(`attr.${attr}`)}+${value}`).join(' · ')}</div>)}</div>}
  if (!catalog || !report) return error ? <ErrorBox message={error} retry={() => void reload()}/> : <Loading/>
  const filtered = rows.filter(value => matches(value, filters, query)), pages = Math.max(1, Math.ceil(filtered.length / 50)), currentPage = Math.min(page, pages - 1)
  const delta = row && draft && row.technology ? milestones.reduce((sum, key) => {
    const points = row.technology![key === 'owned' ? 'pt_get' : key === 'full_star' ? 'pt_upgrage' : 'pt_level']
    return sum + ((Object.hasOwn(draft, key) ? draft[key] : row[key]) === true ? points : 0) - (row[key] === true ? points : 0)
  }, 0) : 0
  return <div className="fleet-technology">
    <PageTitle title={tr('nav')} actions={<button className="button secondary" disabled={busy || connection !== 'ready'} onClick={() => void reload()}>{ui('mind.reload')}</button>}/>
    {error && <ErrorBox message={error}/>}
    <section className="ft-summary"><div><strong>{tr('accountSummary')}</strong><p>{tr('confirmed')}：<b>{report.confirmed_points.toLocaleString()} / {catalog.max_points.toLocaleString()}</b></p><p>{milestones.map(key => <span key={key}>{tr(stageLabel[key])}：{report.milestone_counts[key]} / {rows.filter(value => value.technology).length}　</span>)}</p><p>{tr('finished')}：{report.completed}　{tr('unverified')}：{report.unknown_count} · {tr('factionLevel')}：{Object.entries(catalog.nations).map(([id]) => `${tr(`nation.${id}`)} ${report.nation_levels[id] ?? tr('unverified')}`).join(' · ')}</p><small>{tr('accountHint')}</small></div></section>
    <section className="ft-filters">
      {group('status', ['incomplete', 'unowned', 'missingStar', 'missing120', 'unverified', 'notHeld', 'held'].map(key => [key, tr(key)]))}
      {group('type', types.map(value => [String(value), tr(`type.${value}`)]))}
      {group('nation', [...new Set(rows.map(value => value.nationality))].sort((a, b) => a - b).map(value => [String(value), tr(`nation.${value}`)]))}
      {group('bonusType', types.map(value => [String(value), tr(`type.${value}`)]))}
      {group('attr', attrs.map(value => [String(value), tr(`attr.${value}`)]))}
    </section>
    <div className="ft-search"><input aria-label={tr('search')} placeholder={tr('search')} value={query} onChange={e => {setQuery(e.target.value); setPage(0)}}/><span>{ui('mind.rowCount', {count: filtered.length})}</span></div>
    <TechnologyTable rows={filtered.slice(currentPage * 50, currentPage * 50 + 50)} language={language} tr={tr} onSelect={select}/>
    {!filtered.length && <p>{ui('mind.empty')}</p>}
    <footer><small>{tr('version')}：{catalog.updated_at}</small><div><button disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>{ui('mind.previous')}</button><span>{currentPage + 1} / {pages}</span><button disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>{ui('mind.next')}</button></div></footer>
    <section className="ft-bonuses"><button aria-expanded={bonuses} onClick={() => setBonuses(!bonuses)}>{tr('bonuses')}</button>{bonuses && <>
      <div className="ft-nation-levels">{Object.entries(catalog.nations).map(([id, levels]) => <label key={id}>{tr(`nation.${id}`)} {tr('factionLevel')}<Select aria-label={`${tr(`nation.${id}`)} ${tr('factionLevel')}`} disabled={busy || connection !== 'ready'} value={report.nation_levels[id] ?? ''} onChange={e => void save([], {[id]: e.target.value === '' ? null : Number(e.target.value)})}><option value="">{tr('unverified')}</option>{Array.from({length: levels.length + 1}, (_, value) => <option key={value} value={value}>{value}</option>)}</Select></label>)}</div>
      <h3>{tr('shipBonuses')}</h3>{bonusList(report.ship_bonuses)}<h3>{tr('nationBonuses')}</h3>{bonusList(report.nation_bonuses)}<h3>{tr('total')}</h3>{bonusList(report.bonuses)}<p>{tr('unknownImpact')}</p>
    </>}</section>
    {row && draft && <section ref={correction} tabIndex={-1} className="ft-correction" aria-label={`${tr('correct')} · ${row.name}`}><h2>{row.name} · {tr('correct')}</h2><p>{tr('correctionHint')}</p><Link to={`?tab=data&group=${row.group_type}`}>{tr('viewShips')}</Link>
      {milestones.map(key => <label key={key}>{tr(stageLabel[key])}<Select aria-label={`${row.name} ${tr(stageLabel[key])}`} disabled={busy} value={String((Object.hasOwn(draft, key) ? draft[key] : row[key]) ?? '')} onChange={e => setDraft(changeMilestone(draft, key, stateValue(e.target.value)))}>{[null, true, false].map(value => <option key={String(value)} value={String(value ?? '')}>{tr(stateKey(value))}</option>)}</Select></label>)}
      <p>{tr('impact')}：{delta > 0 ? '+' : ''}{delta}</p>{(conflict || Object.keys(row.conflicts ?? {}).length > 0) && <p role="alert">{tr('correctionHint')} <Link to={`?tab=data&group=${row.group_type}`}>{tr('viewShips')}</Link></p>}
      <details><summary>{tr('evidence')}</summary><p>{row.source} {row.updated_at}</p>{milestones.map(key => <p key={key}>{tr(stageLabel[key])}：{tr(stateKey(row[key]))} {row.evidence[key].map(id => <Link key={id} to={`?tab=data&ship=${id}`}>#{id} </Link>)}</p>)}{Object.entries(row.historical_evidence ?? {}).map(([key, proofs]) => <div key={key}><strong>{tr('historicalEvidence')} · {tr(stageLabel[key as keyof typeof stageLabel])}</strong>{proofs.map(proof => <p key={proof.ship_id}>{row.evidence.owned.includes(proof.ship_id) ? <Link to={`?tab=data&ship=${proof.ship_id}`}>#{proof.ship_id}</Link> : <span>#{proof.ship_id} · {tr('historicalRecord')}</span>} · Lv.{proof.level} · {proof.source} · {proof.confirmed_at}</p>)}</div>)}</details>
      <div className="ft-toolbar"><button className="button" disabled={busy || connection !== 'ready' || Object.keys(draft).length === 1} onClick={() => void save([draft])}>{tr('saveCorrection')}</button><button className="button secondary" disabled={busy} onClick={() => {setSelected(undefined); setDraft(undefined)}}>{tr('close')}</button></div>
    </section>}
  </div>
}
