import {useEffect, useId, useRef, useState} from 'react'
import {Link} from 'react-router-dom'
import type {DockCard, DockReport} from '../api/types'
import {useApp} from '../app/context'
import type {MindCatalog} from '../mind/types'

type Ship = DockCard
export function ShipDetailEditor({ship, report, catalog, disabled, initiallyEditing = false, onSave, onRemove, onClose}: {
  ship: Ship; report: DockReport; catalog?: MindCatalog; disabled: boolean; initiallyEditing?: boolean
  onSave: (ship: Ship) => Promise<boolean>; onRemove: () => Promise<void>; onClose: () => void
}) {
  const {ui} = useApp(), list = useId(), panel = useRef<HTMLElement>(null)
  useEffect(() => {panel.current?.focus()}, [])
  const storageKey = `azurpilot.ship-detail.v5.${report.instance}.${ship.id}`
  const [restored] = useState<{draft: Ship; revision: string} | null>(() => {
    try {const saved = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null'); return saved?.draft?.id === ship.id && typeof saved.revision === 'string' ? saved : null} catch {return null}
  })
  const [editing, setEditing] = useState(initiallyEditing || !!restored), [draft, setDraft] = useState(restored?.draft ?? ship), [retiring, setRetiring] = useState(false)
  const [revision, setRevision] = useState(restored?.revision ?? report.revision)
  useEffect(() => {
    if (!editing) return
    try {sessionStorage.setItem(storageKey, JSON.stringify({draft, revision}))} catch { /* Optional browser draft. */ }
  }, [draft, editing, revision, storageKey])
  function discard() {
    try {sessionStorage.removeItem(storageKey)} catch { /* Optional browser draft. */ }
    setDraft(ship); setRevision(report.revision); setEditing(false)
  }
  async function save() {
    if (await onSave(draft)) {try {sessionStorage.removeItem(storageKey)} catch { /* Saved remotely. */ }}
  }
  const stale = report.revision !== revision
  const copies = report.ships.filter(row => row.id !== ship.id && (ship.group_type ? row.group_type === ship.group_type : row.name === ship.name))
  function rename(name: string) {
    const matches = catalog?.ships.filter(info => info.name === name) ?? []
    const info = matches.length === 1 ? matches[0] : undefined
    setDraft({...draft, name, group_type: info?.group_type ?? null, rarity: info?.rarity ?? draft.rarity,
      base_rarity: info?.base_rarity ?? draft.base_rarity, form: info ? info.group === '改造' ? 'retrofit' : 'base' : null, review: !info})
  }
  function changeForm(form: Ship['form']) {
    const info = catalog?.ships.find(info => info.group_type === draft.group_type && (form === 'retrofit' ? info.group === '改造' : info.group !== '改造'))
    setDraft({...draft, form, ...(info ? {name: info.name, rarity: info.rarity, base_rarity: info.base_rarity} : {}), review: form === 'retrofit' && !info || draft.review})
  }
  return <aside ref={panel} tabIndex={-1} className="mind-panel ship-detail" role="dialog" aria-label={`${ui('analysis.details')} ${ship.name}`}>
    <div className="mind-toolbar"><h2>{ship.name || ui('analysis.new')}</h2><button className="button secondary" disabled={disabled} onClick={onClose}>{ui('analysis.close')}</button></div>
    {stale && <p role="alert">{ui('analysis.stale')}</p>}
    <div className="mind-toolbar">
      <label>{ui('analysis.name')}<input list={list} value={draft.name} readOnly={!editing} disabled={disabled} onChange={e => rename(e.target.value)}/></label>
      <datalist id={list}>{catalog?.ships.map(info => <option key={`${info.group_type}:${info.name}`} value={info.name}/>)}</datalist>
      <label>{ui('analysis.level')}<input type="number" min={0} max={125} value={draft.level} readOnly={!editing} disabled={disabled} onChange={e => setDraft({...draft, level: Number(e.target.value)})}/></label>
      <label>{ui('analysis.extraExp')}<input type="number" min={0} value={draft.extra_exp ?? ''} readOnly={!editing} disabled={disabled} onChange={e => setDraft({...draft, extra_exp: e.target.value === '' ? null : Number(e.target.value)})}/></label>
      <label>{ui('analysis.breakthrough')}<select aria-label={ui('analysis.breakthrough')} value={draft.full_star == null ? '' : String(draft.full_star)} disabled={!editing || disabled} onChange={e => setDraft({...draft, full_star: e.target.value === '' ? null : e.target.value === 'true'})}><option value="">{ui('analysis.unknownValue')}</option><option value="false">{ui('analysis.fullStarNo')}</option><option value="true">{ui('analysis.fullStarYes')}</option></select></label>
      <label>{ui('analysis.form')}<select aria-label={ui('analysis.form')} value={draft.form ?? ''} disabled={!editing || disabled} onChange={e => changeForm(e.target.value as Ship['form'] || null)}><option value="">{ui('analysis.unknownValue')}</option><option value="base">{ui('analysis.base')}</option><option value="retrofit">{ui('analysis.retrofit')}</option></select></label>
      <label><input type="checkbox" checked={draft.review} disabled={!editing || disabled} onChange={e => setDraft({...draft, review: e.target.checked})}/>{ui('analysis.reviewFlag')}</label>
    </div>
    {editing && <p>{ui('analysis.fullStarRule')}</p>}
    {editing ? <button className="button primary" disabled={disabled || stale} onClick={() => void save()}>{ui('analysis.save')}</button> : <button className="button primary" disabled={disabled} onClick={() => setEditing(true)}>{ui('analysis.edit')}</button>}
    {editing && <button className="button secondary" disabled={disabled} onClick={discard}>{ui('analysis.discard')}</button>}
    <p>{ui('analysis.copies')}: {copies.length ? copies.map(row => `${row.name} #${row.id} · Lv.${row.level}`).join(' / ') : '—'}</p>
    <div className="mind-toolbar"><Link aria-disabled={disabled} onClick={event => {if (disabled) event.preventDefault()}} to={`?tab=technology&group=${ship.group_type ?? ''}`}>{ui('analysis.technologyLink')}</Link><Link aria-disabled={disabled} onClick={event => {if (disabled) event.preventDefault()}} to="?tab=mind">{ui('analysis.calculateLink')}</Link></div>
    <details><summary>{ui('analysis.evidence')}</summary><p>{ui('analysis.id')}: {ship.id} · {ui('analysis.group')}: {ship.group_type ?? ui('analysis.unknownValue')}</p><p>{ui('analysis.sourceMetadata')}: {ship.source}</p><p>{ui('analysis.time')}: {report.updated_at || '—'}</p></details>
    {ship.id > 0 && <><p>{ui('analysis.retireHint')}</p><button className="button secondary" disabled={disabled || stale} onClick={() => retiring ? void onRemove() : setRetiring(true)}>{ui(retiring ? 'analysis.retireConfirm' : 'analysis.delete')}</button></>}
  </aside>
}
