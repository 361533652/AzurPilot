# 舰队科技列表

## 页面与资料

入口是「舰队管理 → 舰队科技」，路由 `/i/:instance/task/FleetTechnology`，与舰队信息、心智计算器并列。页面布局与蓝色表头参考[碧蓝航线 Wiki](https://wiki.biligame.com/blhx/舰队科技)，使用项目自己的 React/API 实现，支持组合筛选、搜索、数值排序、每页 50 条、固定表头和窄屏表格横向滚动。

静态资料离线打包。`assets/ship/fleet_tech.json` 按 Lua `ship_data_group.group_type` 保存可见图鉴、编号、方案分类、三阶段科技点、获得与 120 级加成及四大阵营升级加成；名称、稀有度和类型关联现有 `ship_data.json`。来源提交和日期来自本地 Lua Git 快照，不代表已实时同步游戏服务器。全部、有科技、无科技均可显示；无科技的数值列显示破折号。

## 个人记录与状态

隐藏状态 `MindCalculatorScan.MindCalculator.FleetTechRecord` 随实例配置保存和备份：

- `observed`：以群组 ID 为键保存扫描到的最高历史等级，只增不减。
- `manual`：每个群组的获得、满星、Lv.120 和最高等级人工覆盖；未指定字段继续使用自动证据。
- `nations`：四大阵营手工录入的科技等级，空值为未知，0 为确认未升级。

初始未知是“待记录”，不能推断为“未获得”。“已获得”为获得状态确认；“未满级”为已获得且等级确认低于 120；“未完成”匹配明确未获得或未满级；Lv.120 匹配已确认达到 120，125 也达标。满星独立，不通过等级猜测。未知满星不计满星点数，因此汇总明确称为“已确认科技点”，并显示满星未确认数量。

编辑模式自动保存，失败保留浏览器草稿；冲突时先重新载入最新记录，再由用户提交保留的草稿。自动刷新不会抹去操作失败提示，用户主动重载或再次操作后清除。人工覆盖优先于扫描，可恢复该舰船的自动记录。总属性加成累积已确认的获得/120 加成及已录入阵营等级；阵营表中的 add 已是该等级累计值，只加入选中等级一次。导驱/风帆形态在界面合并分类，单条多目标加成不重复累计。

## 扫描与兼容

复用 `MindCalculatorScan`、既有船坞导航及截图识别循环。扫描范围自定义，默认 95–125（含边界），满足 `1 ≤ 最低 ≤ 最高 ≤ 125`。已有合法范围保留，心智计费目标仍为 120。

扫描完成或心智清单保存时，在同一配置事务中累计确认的舰名/等级。识别待核对、未知身份及等级 0 不产生正向证据；心智“排除计费”不等于未获得。改造由共享资料里的真实群组关联，II 型和 META 独立。未扫到、退役或删除心智记录不会撤销历史。扫描期间人工校正按最新配置合并；扫描失败和心智版本冲突不提交新记录。

首版自动扫描沿用国服能力，其他游戏服的识别未扩展。新增资料条目初始未知，资料重生成不清空实例记录。

## API

| 方法 | 行为 |
| --- | --- |
| `fleetTech.catalog(instance)` | 只读离线目录与来源版本 |
| `fleetTech.report(instance)` | 只读实例状态、范围、点数与加成 |
| `fleetTech.patch(instance, revision, changes, nations)` | 事务编辑人工覆盖/阵营等级；版本冲突拒绝写入 |
| `fleetTech.syncMind(instance)` | 幂等累积该实例已保存的确认清单 |

两个写接口标记 `mutates=True`，沿用认证与演示模式写保护。状态字段仍禁止通过普通 `config.patch` 整体替换。

## 生成与更新

使用同一 Lua 快照分别运行：

```powershell
uv run python -m dev_tools.ship_data_extractor --lua-repo <Lua仓库路径>
uv run python -m dev_tools.ship_data_extractor --lua-repo <Lua仓库路径> --names-only
uv run python -m dev_tools.ship_data_extractor --lua-repo <Lua仓库路径> --fleet-tech-only
```

校验图鉴与科技的群组关联、编号唯一性、非空名称、完整字段及点数汇总。目录在进程内缓存，部署资料后由应用更新重启加载。更新 fork 的上游方式与本机路径只记在被 Git 忽略的本地技能，本文不复制本机路径。

## 验证入口

后端：`tests.test_fleet_tech`、`tests.test_mind_calculator`、`tests.test_mind_scan`。前端：`src/fleet-tech/types.test.ts`、任务导航与心智类型单测；浏览器：`mind-fleet-tech.spec.ts`、`mind-navigation.spec.ts`。隔离 mock 调用真实科技算法并仅写临时目录，不连接设备。离线与 mock 通过不能替代指定实例的真实扫描验收。
