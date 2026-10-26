import {useEffect, useRef, useState} from 'react'
import {useParams, useSearchParams} from 'react-router-dom'
import {api} from '../api/client'
import type {DockReport} from '../api/types'
import {useApp, useConnection} from '../app/context'
import {ErrorBox, PageTitle} from '../components/ui'
import {MindCalculator} from './MindCalculator'
import {FleetTechnology} from './FleetTechnology'
import {FleetLedger} from './FleetLedger'
import {ScanUpdate} from './ScanUpdate'
import '../mind/mind.css'
import './fleet-analysis.css'

export function FleetAnalysis() {
  const {instance = ''} = useParams(), [params, setParams] = useSearchParams()
  const {ui} = useApp(), connection = useConnection()
  const [report, setReport] = useState<DockReport>(), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const writing = useRef(false), epoch = useRef(0)
  const value = params.get('tab'), tab = value === 'technology' || value === 'mind' || value === 'scan' ? value : 'data'
  useEffect(() => {writing.current = false; setBusy(false); epoch.current++}, [instance, tab])
  useEffect(() => {
    let active = true
    setReport(undefined); setError(''); setBusy(false); writing.current = false; epoch.current++
    if (connection !== 'ready') return
    const load = async () => {
      if (writing.current) return
      const version = epoch.current
      try {const value = await api.request('dock.report', {instance}); if (active && version === epoch.current && !writing.current) setReport(value)}
      catch (e) {if (active && version === epoch.current) setError((e as Error).message)}
    }
    void load(); const timer = setInterval(() => void load(), 5000)
    return () => {active = false; clearInterval(timer)}
  }, [instance, connection])
  const onBusy = (value: boolean) => {epoch.current++; writing.current = value; setBusy(value)}
  const onReport = (value: DockReport) => {epoch.current++; setReport(value)}
  return <div className="mind-calculator fleet-analysis"><PageTitle title={ui('analysis.title')}/>{error && <ErrorBox message={error}/>}
    <div className="ft-chips" role="tablist" aria-label={ui('analysis.title')}>{(['data', 'technology', 'mind', 'scan'] as const).map(value => <button key={value} role="tab" disabled={busy} aria-selected={tab === value} aria-controls={`analysis-${value}`} id={`tab-${value}`} onClick={() => setParams({tab: value}, {replace: true})}>{ui(value === 'scan' ? 'analysis.scanPage' : `analysis.${value}`)}</button>)}</div>
    <section role="tabpanel" id={`analysis-${tab}`} aria-labelledby={`tab-${tab}`}>
      {tab === 'data' && <FleetLedger key={instance} instance={instance} report={report} locked={busy} onReport={onReport} onBusy={onBusy} onDirty={() => {}}/>}
      {tab === 'technology' && <FleetTechnology key={instance}/>}
      {tab === 'mind' && <MindCalculator key={instance}/>}
      {tab === 'scan' && <ScanUpdate key={instance} instance={instance} report={report} locked={busy} onReport={onReport} onBusy={onBusy}/>}
    </section>
  </div>
}
