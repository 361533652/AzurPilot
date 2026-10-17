/** 原生光学扫码使用的粒子发送画布；不依赖上游 ParticlePair 源码或 QR。 */
import {useEffect, useRef} from 'react'
import {particleCells} from './particleCode'

/** 三臂星云负责视觉引导，绿色载波按 300ms 相位传输单次密钥。 */
export function ParticleCanvas({secret, label}: {secret: string; label: string}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const element = canvas.current, context = element?.getContext('2d', {alpha: false})
    if (!element || !context) return
    const cells = particleCells(secret)
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')
    let frame = 0, previous = 0
    const draw = (time: number) => {
      frame = requestAnimationFrame(draw)
      if (document.hidden || time - previous < 32) return
      previous = time
      const size = 432
      const step = size / 18
      const phase = Math.floor(time / 300) % 2
      const motion = reduced.matches ? 0 : time / 24000
      context.fillStyle = '#07051b'; context.fillRect(0, 0, size, size)
      for (let p = 0; p < 1100; p++) {
        const radius = Math.sqrt(((p * 997) % 1103) / 1103) * 205
        const arm = (p % 3) * Math.PI * 2 / 3
        const angle = arm + radius / 37 + motion + Math.sin(p * 1.71) * .28
        const x = 216 + Math.cos(angle) * radius, y = 216 + Math.sin(angle) * radius * .85
        context.fillStyle = p % 2 ? 'rgba(195,8,245,.72)' : 'rgba(65,12,255,.85)'
        context.beginPath(); context.arc(x, y, .6 + ((p * 7) % 11) / 6, 0, 2 * Math.PI); context.fill()
      }
      cells.forEach((bit, index) => {
        const bright = Number(bit) !== phase
        context.fillStyle = bright ? 'rgb(12,175,155)' : 'rgb(4,24,65)'
        context.beginPath()
        context.arc((index % 18 + .5) * step, (Math.floor(index / 18) + .5) * step, step * .22, 0, Math.PI * 2)
        context.fill()
      })
    }
    frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
  }, [secret])
  return <canvas ref={canvas} width={432} height={432} role="img" aria-label={label}
    className="particle-pair-canvas"/>
}
