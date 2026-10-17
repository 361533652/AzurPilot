/**
 * Particle Code v1 的独立帧编码实现，按公开协议采用 CRC-16 和 Hamming(12,8)。
 * 协议参考：https://github.com/tianrking/ParticlePair#particle-code-v1
 */
const DATA_POSITIONS = [3, 5, 6, 7, 9, 10, 11, 12]

/** CRC-16/CCITT-FALSE：多项式 0x1021，初值 0xffff，无反射。 */
export function packetCrc(bytes: ArrayLike<number>): number {
  let state = 65535
  for (let i = 0; i < bytes.length; i++) {
    state ^= bytes[i] << 8
    for (let bit = 0; bit < 8; bit++) state = ((state << 1) ^ ((state & 32768) ? 4129 : 0)) & 65535
  }
  return state
}

/** 将服务器的 128 位单次扫描密钥编码为 324 个光学单元。 */
export function particleCells(secret: string): boolean[] {
  if (!/^[a-f0-9]{32}$/.test(secret)) throw new Error('Invalid optical pairing secret')
  const packet = [167, 1, 16, ...secret.match(/../g)!.map(value => parseInt(value, 16))]
  const checksum = packetCrc(packet)
  packet.push(checksum >>> 8, checksum & 255)
  const words = packet.flatMap(byte => {
    let word = 0
    DATA_POSITIONS.forEach((position, bit) => {if ((byte >>> bit) & 1) word |= 1 << (position - 1)})
    for (const parity of [1, 2, 4, 8]) {
      let value = 0
      for (let p = 1; p <= 12; p++) if (p & parity) value ^= (word >>> (p - 1)) & 1
      word |= value << (parity - 1)
    }
    return Array.from({length: 12}, (_, bit) => !!(word & (1 << bit)))
  })
  let cursor = 0
  return Array.from({length: 324}, (_, index) => {
    const row = Math.floor(index / 18), col = index % 18
    if (row === 0 || col === 0 || row === 17 || col === 17) return (row * 3 + col * 5 + (row === 0 ? 1 : 0)) % 7 < 3
    const value = cursor < 252 ? words[cursor] : cursor % 3 === 0
    cursor++
    return value
  })
}
