import {describe, expect, it} from 'vitest'
import {packetCrc, particleCells} from './particleCode'

// 由 ParticlePair 公开参考实现生成的协议向量，仅包含合成密钥。
const VECTOR = '100110010011001001001111100010111100000000000100000011110000000000000000001100001100011010110010100101110001011001001010100100111110100101001001011000110001011010011101100001100011110100010100100010010100101011110110111010101011000111000110111101110000111110111111011101011110011110100010010110101111010011110010011001001100'
describe('Particle Code v1 发送编码', () => {
  it('与公开参考实现逐单元互通', () => {
    expect(particleCells('00112233445566778899aabbccddeeff').map(Number).join('')).toBe(VECTOR)
  })
  it('匹配标准 CRC 校验向量，拒绝非 128 位输入', () => {
    expect(packetCrc(new TextEncoder().encode('123456789'))).toBe(0x29b1)
    expect(() => particleCells('not-a-secret')).toThrow()
  })
})
