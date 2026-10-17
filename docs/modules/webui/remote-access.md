# Cloudflare Quick Tunnel 与 AFA 配对

## 启用

AP 的「远程访问」页将模式选为 `cloudflare`，设置 WebUI 密码并开启远程访问，重启 AP。
`CloudflareRegistry` 默认为 `https://remote.azurpilot.com`。
注册与配对使用 `/api/v1/*`，根路径为介绍页。已知旧试验地址会迁移到新域名，保留设备身份；自定义服务器不变。
AFA 在「设置 → Runtime」点击「试用 Quick Tunnel」，确认重启后使用同一套 AP provider；
已有 SSH / WebRTC 模式只有在用户选择试用时才切换。

设备身份保存在 `config/remote-access.json`：安装 ID 与注册密钥持久化，配对访问凭据只保存
SHA-256 摘要。AFA 第一次启用时将 Android ID 与随机安装因子的摘要作为设备 ID，
不上传 Android ID 原文；卸载后重新安装会获得新身份，避免丢失旧密钥后占用原设备入口。
`CloudflareDeviceId` 留空时自动生成；兼容部署解析器保留的 `""` / `''` 空值。
旧版若已将引号空值保存为身份，只修复未被服务器接受的 ID，保留原设备密钥与已保存授权。
备份身份文件才能在迁移后保留原固定入口；禁止把此文件作为用户实例、测试夹具或 Git 内容提交。

## 生命周期

`CloudflareRemoteAccessProvider` 在线程内调用 `pyflared==0.1.0` 的 `run_quick_tunnel()`。
上游库导入时创建的数据库目录定向到项目 `cache/cloudflare/pyflared/`，无需写入
Windows `AppData/Local/Azmain`。适配仅匹配 pyflared 的应用标识，导入后恢复通用目录函数。
每次启动产生新的 `*.trycloudflare.com` URL，用设备密钥注册到 Go 服务；每 60 秒续约。
固定入口为 `<registry>/d/<device_id>/`，服务返回 **301** 与 `Cache-Control: no-store`。
路径和查询参数会传到当前 Tunnel；固定入口不代理业务流量。

优雅停止时上报离线并回收 cloudflared；异常退出后服务端租约 180 秒失效。
固定入口还会用四秒超时请求 Tunnel 的 `/healthz`，确认 AP 的协议健康状态；不跟随探测跳转或携带凭据。
设备不存在、离线、租约过期或 Tunnel 失效时，以 302 跳到同域 `/error`；在线时仍返回禁止缓存的 301。
Tunnel 或续约失败会清除当前可访问地址、关闭旧进程，以 2–60 秒退避重建并重新注册。
`pyflared` 的流读取在 EOF 时返回 SKIP，本模块将其转为 EOF，避免进程退出后忙循环。
提供者不更改 cloudflared 以外的后台任务或游戏状态。

公网 Host 保留到现有网关，由既有本机判定与 WebUI 密码策略鉴权。无有效 WebUI 密码时
拒绝启动 Quick Tunnel。自签名本机 HTTPS 证书不会自动跳过证书校验。

## 配对

1. AP 远程访问就绪后点击「配对」。AP 生成独立的客户端凭据，将它交给注册服务暂存。
2. 服务器生成六位数字码（保留前导零）、128 位扫描密钥和五分钟有效期。AP 用 Particle Code v1
   编码密钥，三臂粒子星云叠加绿色差分载波，每相位 300ms。减少动态效果时停止星云旋转，
   保留传输所需的载波变化。编码器、渲染器与原生解码器为独立实现，未引入上游源码依赖。
3. AFA 扫描图形或输入六码，向服务领取固定入口和独立凭据。领取在 SQLite 事务内执行，
   只成功一次；已领取凭据从配对记录清空。数字码和扫描 secret 仅存摘要。
4. AFA 用 CameraX 原生采集绿色通道，相隔约一个相位的画面进行差分；同步边界、
   Hamming(12,8) 与 CRC-16 全部通过且重复识别一致后，才用扫描密钥领取配对。
   手动六码与光学扫描共享原子领取状态，一种方式成功后另一种不能重放。
5. AFA 用 Android Keystore AES-GCM 保存固定入口和独立凭据。点击已配对 AP 后，现有
   `AzurPilotGateway` 重新解析固定入口，以 WSS 调用 `auth.login`；认证完成才对原生页面发布就绪。
   现有总览、实例选择、配置、日志和统计均复用原生仓库与主题订阅。
   切换来源先等待编辑保存，关闭旧连接并清理缓存；返回本机恢复原监听地址。
   本机 PRoot、悬浮窗和设备控制链不随远程网关改址，远程部署设置也不会同步到本机偏好。

AP 的「已授权的配对」可撤销单个凭据。已连接客户端在下一次请求（含 15 秒心跳）时停止授权，
不需要更改 WebUI 主密码。AFA「移除」只删除本机保存的入口，服务器授权应从 AP 撤销。
重新生成配对码会替换服务器上该设备尚未领取的码。

注册服务源码与部署说明位于 AFA 仓库的 `server/remote-access/`。它不保存 WebUI 主密码，
不代理 AP 的 WebSocket、截图或业务数据。配对凭据只有未领取阶段以明文暂存于 SQLite，
因此数据库和服务端私钥需使用服务自己的私有目录。

## 试验与验证

Python 定向测试：`uv run python -m pytest tests/test_remote_cloudflare.py`。
前端定向测试覆盖 `src/api/client.test.ts`、`src/app/remoteStatus.test.ts` 与远程页面。
Go 测试覆盖设备 ID 防冒用、URL 限制、301 更新、离线租约、配对单次领取、过期与限流。
测试使用临时身份和模拟游戏服务，真实账号与调度器不参与网络试验。

AFA 自身开启 Tunnel 需要包含本次 AP 实现和锁定依赖的 Runtime；仅安装新 APK 不会修改旧
Runtime 中的 Python 业务代码。构建 Runtime 时通过 `AZURPILOT_REPO` 与 `AZURPILOT_REF`
指向包含本次 AP 提交的源码，再按 AFA 的 `doc/runtime-provisioning.md` 构建和部署。
`tools/remote-gateway-check` 在 JVM 上直接编译生产网关和光学解码器，验证鉴权顺序、重连解析、
来源切换、旋转镜像、曝光偏移、纠错与随机噪声拒绝，不运行 AFA 既有孤立测试源集。
扫码、相机权限、后台断网恢复与 PRoot 内 cloudflared 需要在实际 Android 设备上验收。

Quick Tunnel 用于测试和开发，Cloudflare 不提供可用性保证，并限制并发请求、SSE 等能力。
AP 的业务通道使用 WebSocket，固定入口不会消除 Quick Tunnel 的服务限制。
参阅 [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
和 [pyflared](https://pypi.org/project/pyflared/)。
