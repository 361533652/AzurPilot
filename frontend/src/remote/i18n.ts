/** 远程配对的五语言文案。 */
import type {Language} from '../i18n'

const zh = {
  title: '与 AFA 配对', start: '配对', refresh: '生成新配对码',
  hint: '在 AFA 中扫描图形，或输入下面的六位配对码。',
  waiting: '等待 AFA 连接', connected: '配对成功', expired: '配对码已过期',
  remaining: '秒后失效', revoke: '移除配对', devices: '已授权的配对',
  ready: '选择 cloudflare 模式并启用远程访问，重启 AP 后即可配对。',
  busy: '正在生成…', qr: 'AFA 配对图形',
}
type Copy = typeof zh
export const pairCopy: Record<Language, Copy> = {
  'zh-CN': zh,
  'zh-MIAO': {...zh, title: '与 AFA 配对喵', waiting: '等 AFA 连接喵'},
  'zh-TW': {title: '與 AFA 配對', start: '配對', refresh: '產生新配對碼',
    hint: '在 AFA 中掃描圖形，或輸入下方的六位配對碼。', waiting: '等待 AFA 連線',
    connected: '配對成功', expired: '配對碼已過期', remaining: '秒後失效', revoke: '移除配對',
    devices: '已授權的配對', ready: '選擇 cloudflare 模式並啟用遠端存取，重新啟動 AP 後即可配對。',
    busy: '正在產生…', qr: 'AFA 配對圖形'},
  'en-US': {title: 'Pair with AFA', start: 'Pair', refresh: 'Generate a new code',
    hint: 'Scan this image in AFA or enter the six-digit pairing code below.', waiting: 'Waiting for AFA',
    connected: 'Paired', expired: 'Code expired', remaining: 'seconds remaining', revoke: 'Remove pairing',
    devices: 'Authorized pairings', ready: 'Select cloudflare mode, enable remote access, and restart AP to pair.',
    busy: 'Generating…', qr: 'AFA pairing image'},
  'ja-JP': {title: 'AFA とペアリング', start: 'ペアリング', refresh: '新しいコードを生成',
    hint: 'AFA で画像をスキャンするか、下の6桁のコードを入力してください。', waiting: 'AFA の接続を待機中',
    connected: 'ペアリング完了', expired: 'コードの有効期限切れ', remaining: '秒で期限切れ',
    revoke: 'ペアリングを解除', devices: '許可したペアリング',
    ready: 'cloudflare モードでリモートアクセスを有効にし、AP を再起動してください。',
    busy: '生成中…', qr: 'AFA ペアリング画像'},
}
