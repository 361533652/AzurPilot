/** AP 配对入口：服务器单次密钥驱动的 Particle Code v1 光学动画。 */
import {useEffect, useState} from 'react'
import {ParticleCanvas} from './ParticleCanvas'
import {api} from '../api/client'
import type {RemoteClient, RemotePairing} from '../api/types'
import {useApp} from '../app/context'
import {pairCopy} from './i18n'
import './pairing.css'

/** 只在用户发起配对后展示动画；领取、过期和离开页面均停止轮询。 */
export function PairingPanel({ready}: {ready: boolean}) {
  const {language} = useApp()
  const copy = pairCopy[language]
  const [pairing, setPairing] = useState<RemotePairing>()
  const [state, setState] = useState('')
  const [remaining, setRemaining] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [clients, setClients] = useState<RemoteClient[]>([])

  useEffect(() => {
    let active = true
    if (ready) void api.request('remote.clients', {}).then(items => {if (active) setClients(items)}).catch(() => {})
    return () => {active = false}
  }, [ready])

  useEffect(() => {
    if (!pairing || state !== 'pending') return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const update = async () => {
      const left = Math.max(0, pairing.expires_at - Math.floor(Date.now() / 1000))
      setRemaining(left)
      if (!left) {setState('expired'); return}
      try {
        const result = await api.request('remote.pair.status', {id: pairing.id})
        if (!active) return
        if (result.state !== 'pending') {
          setState(result.state)
          if (result.state === 'claimed') setClients(await api.request('remote.clients', {}))
          return
        }
      } catch (err) {if (active) setError((err as Error).message)}
      if (active) timer = setTimeout(update, 2000)
    }
    void update()
    return () => {active = false; clearTimeout(timer)}
  }, [pairing, state])

  async function start() {
    setBusy(true); setError(''); setState('')
    try {
      if (pairing && state !== 'claimed') await api.request('remote.revoke', {id: pairing.client_id})
      const next = await api.request('remote.pair.create', {})
      setPairing(next); setRemaining(next.expires_at - Math.floor(Date.now() / 1000)); setState('pending')
    } catch (err) {setError((err as Error).message)}
    finally {setBusy(false)}
  }

  async function revoke(id: string) {
    try {
      await api.request('remote.revoke', {id})
      setClients(previous => previous.filter(item => item.id !== id))
    } catch (err) {setError((err as Error).message)}
  }

  return <section className="panel config-group remote-pairing">
    <div className="panel-heading"><h2>{copy.title}</h2></div>
    {!ready && <p className="muted">{copy.ready}</p>}
    <button type="button" className="button primary" disabled={!ready || busy} onClick={() => void start()}>
      {busy ? copy.busy : pairing ? copy.refresh : copy.start}
    </button>
    {error && <p role="alert">{error}</p>}
    {pairing && <div className="pairing-session" role="status" aria-live="polite">
      {state === 'pending' && <>
        <p>{copy.hint}</p>
        <ParticleCanvas secret={pairing.particle_secret} label={copy.qr}/>
        <code className="pairing-code" aria-label={pairing.code}>{pairing.code.slice(0,3)} {pairing.code.slice(3)}</code>
        <p className="muted">{copy.waiting} · {remaining} {copy.remaining}</p>
      </>}
      {state === 'claimed' && <p>{copy.connected}</p>}
      {state === 'expired' && <p>{copy.expired}</p>}
    </div>}
    {clients.length > 0 && <div className="pairing-clients"><h3>{copy.devices}</h3>{clients.map(item =>
      <div className="remote-address" key={item.id}>
        <span>{new Date(item.created_at * 1000).toLocaleString(language === 'zh-MIAO' ? 'zh-CN' : language)}</span>
        <button type="button" className="button secondary" onClick={() => void revoke(item.id)}>{copy.revoke}</button>
      </div>)}</div>}
  </section>
}
