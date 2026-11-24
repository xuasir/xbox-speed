# Xbox Accel — OpenWrt / LuCI 插件

版本：**0.3.0 测试版**。支持 Xbox、微软商店、PlayStation 和 Switch 下载域名优选。脚本和界面均与 CPU 架构无关；已经在官方 OpenWrt 24.10.4 ARM64 rootfs 上通过安装及集成测试。实际设备下载效果需在目标网络验证。

## 1. 功能与结构

```text
客户端 → 主 dnsmasq
         ├─ 明确列出的平台下载域名 → 127.0.0.1:5335 插件解析实例
         │                              ├─ 返回优选 CDN IP
         │                              └─ 或返回路由器 LAN IP → HTTP 302 → .cn CDN
         └─ 其他查询 → 原来的 ChinaDNS / 上游，保持不变
```

- **国内优选**独立开关；**国际下载方式**统一选择默认解析、国际优选或转到国内下载。
- **微软商店、PlayStation、Xbox 老游戏下载、Switch**增加独立平台组，默认关闭；资源与配置详见[平台下载组](docs/PLATFORMS.md)。
- 同一个国际域名不能同时指向 CDN 和本地 HTTP 服务；重定向开启时优先使用重定向。
- 主 dnsmasq 只添加特定下载域名的固定上游规则。更新节点时修改独立解析实例，不反复改主 DNS 规则。
- IPv4 优选节点、HTTP 响应状态及下载量校验、串行初筛、前几名复测、切换阈值、失败保留旧 IP。
- 主机的 Xbox SRV 记录及四个已知备用 CDN 主机名一起处理；不泛化覆盖整个微软域名。
- 总开关、手动测速、取消任务、清除结果、重新应用、重启服务、实时日志、完整验收和脱敏报告导出均可从 **LuCI → 服务 → Xbox 下载加速** 操作。
- “重启服务”使用已保存配置重启插件并完成验收，结果显示在最近活动与日志中。后台任务执行期间按钮禁用；总开关关闭时执行停用清理。
- 无 Node.js、Python 或 Docker 运行时依赖；Docker 只用于开发验证。

### 自动化

默认启用后：

1. 没有有效节点时，立即发起首次后台测速。
2. 每天 **05:15** 重新发现候选并测速，按路由器自身时区执行。
3. 每 **10 分钟** 健康检查。当前节点连续失败时重测；缺少节点时最多每 30 分钟重试。
4. 健康检查发现解析实例、主规则或已应用模式漂移时，重新应用配置。
5. 任务由 procd 管理；关闭浏览器不影响执行。flock 防止并发测速、健康检查和配置更新互相覆盖。

节点配置变化后，对主 dnsmasq 和已识别副本发送 SIGHUP 清缓存。发送前核对运行中的可执行文件、完整配置路径与进程启动时间，支持系统 dnsmasq 的任意改名、软链接和二进制复制，以及 `-C` / `--conf-file` 参数形式。客户端自身缓存仍可能延迟；已有 Xbox 下载连接在暂停并继续后可重新建立。

## 2. 安装

### 使用 opkg 的 OpenWrt（推荐）

将 `luci-app-xbox-accel_0.3.0-1_all.ipk` 上传到路由器，例如 `/root/`：

```sh
opkg update
opkg install /root/luci-app-xbox-accel_0.3.0-1_all.ipk
```

依赖由 opkg 安装：`luci-base`、`rpcd`、`curl`、`ca-bundle`、`uhttpd`、`jshn`、`conntrack`。运行环境使用现有标准 dnsmasq 或 dnsmasq-full；BusyBox 提供 flock、nslookup 等标准命令。

旧版升级时若出现多个 `flock 1000` 持续等待，请按[服务锁恢复步骤](docs/UPGRADE_LOCK_RECOVERY.md)修补现有脚本、重启路由器，再安装包含修复的软件包。

安装后刷新或重新登录 LuCI，进入 **服务 → Xbox 下载加速**。**默认未启用**，首次安装不会添加主 DNS 规则、修改 Web 配置或创建测速计划。

建议先在“测速”中选择所需平台进行独立比较，再采用结果；也可选择功能并启动，缺少节点时自动首次选优。

### 源码安装 / 使用 apk 的新版本 OpenWrt

源码安装脚本兼容 opkg 和 apk，但 apk 系统的实际安装尚未验证；`.ipk` 不能直接交给 apk 安装。

```sh
tar -xzf xbox-accel-0.3.0.tar.gz
cd xbox-accel-0.3.0
sh install.sh
```

缺少必要依赖时脚本尝试前置安装。升级前先让旧脚本撤销旧规则，再覆盖文件，保留用户配置与测速结果。

## 3. LuCI 页面

页面提供 **总览 / 加速功能 / 测速 / 运行记录** 四个入口，围绕“选择功能、比较节点、确认生效、查看历史”组织。稳定页面每 30 秒刷新；后台任务、异常状态和运行中的总览每 5 秒刷新。

| 入口 | 日常操作 |
|---|---|
| 总览 | 查看服务、各平台配置验收、当前节点；选择下载设备，观察真实连接与下行流量 |
| 加速功能 | 选择平台与国际下载方式，保存并应用 |
| 测速 | 单独测一个平台或勾选多个平台；查看独立候选结果，选择采用 |
| 运行记录 | 查看日常、异常事件及测速与操作历史，导出诊断报告 |

### 选择加速功能

国内优选、微软商店、PlayStation、Xbox 老游戏下载和 Switch 各自提供开关。国际下载方式统一选择“默认解析 / 国际节点优选 / 转到国内下载（HTTP）”；选择转到国内下载时同步开启国内优选。

打开“启动加速”并保存应用后，服务按所选平台运行；缺少运行节点时自动执行首次选优。采用测速结果也会开启对应平台和总开关。关闭平台或总开关并保存应用后，程序撤销相应转发规则并刷新已识别的 DNS 缓存。

自动维护和网络兼容设置默认折叠。自动维护包括开机恢复、仅手动 / 每天 / 每周 / 每月更新节点及节点异常恢复。计划时间使用路由器时区，每月 29 至 31 号在缺少对应日期的月份跳过；首次选优与异常恢复各自按启用状态执行。旧版简单计划自动映射，其他旧计划保留自定义选项。

网络兼容设置集中提供备用 CDN、Passwall 直连同步、主 DNS 实例、解析端口、TTL、备用上游与重定向入口。更换主 DNS 实例前先停用并保存。运营商选项用于扩展候选，最终排名由当前线路实测决定。

### 按需测速与采用

所有平台都支持独立测速，包括服务已关闭、平台开关已关闭的情况。勾选平台后点击“开始测速”，或在结果行点击“单独测”。测速使用已保存的采样参数和资源，后台任务关闭页面后继续执行。

独立测速保存每个平台的候选节点、速度、时间及资源兼容性，运行节点和功能配置保持原状。点击“采用结果 / 启用并应用”后，程序重新检查资源与运行配置，启用对应功能、提交节点、应用并验收。资源设置变化后，旧候选显示需要重新测速；采样失败或取消时保留此前候选与运行节点。

采样参数和各平台测速资源分别折叠。微软商店自动获取有效签名链接；PlayStation 支持分域名校验；Switch 使用公开测速资源，实际游戏链接用于可选增强校验。资源规则见[平台下载组](docs/PLATFORMS.md)。

默认每组最多 60 个候选，初筛每个 4 MiB / 4 秒，前 3 名加当前节点复测，每个 64 MiB / 10 秒。理论上限每组约 496 MiB，所选组分别累加；按带宽和流量额度调整。

### 下载时确认生效

在设备上开始下载，再回到总览选择设备。页面同时展示配置验收和实际连接：

- **已命中当前节点**：设备连接目标与当前配置节点一致。共享 CDN 地址同时列出可能的平台。
- **连接沿用旧节点**：配置已经切换，既有连接继续使用此前节点；暂停并继续下载可建立新连接。
- **下载目标存在差异**：目标关联到平台下载域名或近期节点，实际地址与当前节点有差异；检查设备 DNS、IPv6 与分流设置。
- **平台待确认**：连接的下载平台尚待关联。

下行速度由连接表的回复方向字节增量估算。缺少流量计数时展示连接证据和统计待确认。分流路径单独显示“直连已确认 / 已进入重定向服务 / 观察到本地转发 / 分流路径待确认”；直连判断要求回复元组、零连接标记、完整规则读取与实际路由查询共同支持。重定向请求与后续国内节点连接分别展示。

观测使用设备租约、邻居表、连接表和插件自己的 DNS 日志，覆盖 IPv4、IPv6、TCP 和 UDP 443。设备经过其他网关、使用加密 DNS 或缺少连接表权限时，关联范围会受限制。节点命中说明配置节点与实际连接一致，提速幅度需要设备下载同一资源进行对比。

## 4. 运行记录与验收

运行记录默认展示日常和异常事件，以及测速、采用、应用、验收等任务历史。验收与环境详情、原始日志、维护操作和日志采集设置各自折叠，排查时展开。

- 日常事件最多 300 条 / 128 KiB，每 10 分钟批量保存；异常事件最多 100 条 / 64 KiB，及时保存。服务正常停止时刷新近期记录。
- 最近 20 次任务与最新健康检查分别保留；独立测速候选按平台持久保存。重启后可查看已保存的事件、候选、任务和验收报告。
- 原始日志默认“日常摘要”，包含阶段汇总、节点变化与错误；“详细诊断”增加逐节点、逐域名记录。原始日志保留最近 500 行，任务快照上限 128 KiB。
- “清空事件与日志”清除事件和原始日志，任务及验收历史继续保留；“导出诊断报告”提供带地址别名、隐藏资源路径与签名的报告。

完整验收检查配置、解析实例、各 DNS 入口的 A / AAAA / SRV、资源、计划及直连集合，并读取当前设备连接证据。各平台分别汇总；逐域名详情按 DNS 入口折叠，通过组收起、异常组展开。历史验收显示时间和配置适用状态，实时设备连接始终在总览更新。

测速、采用和配置任务串行执行，运行期间可查看进度或取消任务。取消会终止任务子进程并保留已完成结果。常驻服务启动前清理继承的锁描述符；停用操作默认期限 60 秒，超时返回 124 / SERVICE_TIMEOUT 并保留清理记录。

错误代码、数据保留规则与实机步骤见[诊断与验收说明](docs/DIAGNOSTICS.md)。

## 5. com→cn 条件与限制

开启重定向后，procd 同时管理独立解析、下载观测与重定向实例。独立 uhttpd 默认监听 `127.0.0.1:18081` 和 `LAN_IP:18081`，Web 根目录为 `/var/etc/xbox-accel/redirect`。主 uhttpd 保留原有端口、HTTPS 跳转与过滤配置；LuCI 页面及文件继续由主服务提供。重定向服务返回 302，游戏内容由客户端直接从国内 CDN 下载。

```text
国际下载域名 → DNS 返回 192.0.2.1
→ LAN 发往 192.0.2.1:80 的请求转入独立服务 18081
→ HTTP 302 → 客户端访问国内 CDN
```

“加速功能 → 网络兼容设置”提供 `redirect_ip`（默认 `192.0.2.1`）与 `redirect_port`（默认 `18081`）。专用虚拟地址需位于直连网段及现有专用路由之外，监听端口需在 `1024..65535` 且与解析端口分开。启用前检查地址及端口占用，参数错误会保留现有运行配置并返回错误。

- **下载设备需经插件所在路由器到达专用地址**。主路由部署可使用默认值；旁路由部署需在客户端或主路由配置 `192.0.2.1/32` 的下一跳为旁路由 LAN 地址。专用地址由 DNS 和转发规则使用。
- nftables 使用插件自己的 IPv4 表，iptables 使用专用 PREROUTING / OUTPUT 链；规则精确匹配 LAN 设备、专用地址与 TCP 80。防火墙重载入口自动恢复规则，健康检查也会检查重定向进程和转发状态。开启 Passwall 自动直连同步时，专用地址也加入直连集合；手动分流时将该地址设为本地直连。
- 适用于 HTTP 下载；HTTPS 保持原有证书验证。国内资源不可用或过慢时，关闭 com→cn 后按国际 CDN 优选设置处理。DNS 重绑定保护继续启用，例外限定在插件接管的下载域名；IPv6 处理限定在已固定域名。

独立实例随功能开启而启动，异常退出由 procd 重新拉起。关闭重定向或总开关会撤销专用转发与防火墙重载入口；停止服务会核查实际进程的二进制与 Web 根目录，兼容 PID 文件遗失或错误。升级时恢复旧版保存的主 uhttpd 404 配置，并将旧版路由器地址语义迁移为专用地址；后续启停仅管理独立入口。

## 6. 与 Passwall / ChinaDNS 共存

你的主 dnsmasq → ChinaDNS 链路可以保留，插件的按下载域名规则在主 dnsmasq 层先处理。

某些 Passwall 模式会把 LAN DNS 重定向到其 dnsmasq 副本，绕过主实例。程序会检测已知副本；启用时按“启用时自动重载 Passwall”设置同步新增规则。撤销总开关或某项功能时始终自动重载遗留规则并刷新缓存。重载可能短暂影响代理连接。

清理按本插件记录的规则所有权执行，并保留其他域名规则。旧端口、旧主实例、副本配置路径与待重载标记保存在 `/etc/xbox-accel/`，服务重载或缓存刷新失败时保留记录，后续停用或重启可继续处理。主 DNS 的规则变更通过系统服务脚本正常重启，Passwall / Passwall2 副本通过对应服务脚本重建。

候选和选中 IP 默认加入既有 `psw_white` nftset/ipset。**没有验证完整 Passwall 的分流行为**：自定义 ACL、关闭直连列表、特殊节点和 Passwall2 不一定遵循这个集合。共享 CDN IP 加入直连也可能影响其他访问相同 IP 的服务。

插件不删除共享集合里的元素，以免误删其他程序需要的地址；停止插件后元素可能继续存在，直到集合过期或 Passwall 重建。

## 7. 命令与回滚

```sh
xbox-accel check         # 环境检测
xbox-accel start_verify  # 后台完整验收
xbox-accel start_report  # 后台生成脱敏报告
xbox-accel enable        # 启用，必要时自动首次测速
xbox-accel status        # 当前节点和模式
xbox-accel measure ps             # 独立测速，支持关闭的平台
xbox-accel measure cn,switch      # 多平台独立测速
xbox-accel start_measure store    # 后台独立测速
xbox-accel start_adopt ps 结果ID   # 启用平台、采用候选并验收
xbox-accel json_records           # 日常、异常事件与任务历史
xbox-accel start_test             # 自动选优并应用，供计划任务和旧 CLI
xbox-accel test cn                # 当前终端对已选功能选优并应用
xbox-accel cancel        # 取消任务
xbox-accel clear_log
xbox-accel reset         # 清除运行节点，独立候选与历史保留
xbox-accel disable
```

停用撤销本插件管理的特定上游规则、自身新增的重绑定例外、uhttpd error_page 和自身 cron 条目；不覆盖整个 DHCP 配置，保留原有其他规则。

自启动与定时配置示例：

```sh
uci set xbox-accel.main.autostart='1'
uci set xbox-accel.main.schedule_type='weekly'
uci set xbox-accel.main.schedule_weekday='3'  # 0=星期日，1..6=星期一至星期六
uci set xbox-accel.main.schedule_time='08:35'
uci commit xbox-accel
/etc/init.d/xbox-accel reload
# 每月计划改用 schedule_type='monthly' 和 schedule_monthday='1'..'31'
```

```sh
opkg remove luci-app-xbox-accel
# 或源码安装的卸载：
sh install.sh remove    # 保留配置与节点记录
sh install.sh purge     # 同时清除配置与记录
```

配置和结果持久保存于 `/etc/config/xbox-accel`、`/etc/xbox-accel`。卸载依赖库不自动删除，防止影响其他插件。

## 8. 验证记录与边界

开发测试使用官方 `openwrt/rootfs:armsr-armv8-24.10.4`（aarch64_generic）容器，procd、dnsmasq、uhttpd、rpcd、fw4 均实际运行。

已验证：

- `.ipk` 由 opkg 在新容器安装，自动安装依赖，默认未改 DNS / Web 配置。
- LuCI JS 替身测试：渲染、表单参数、纯文本日志、按钮状态、等待 UCI 提交后应用。
- 真实 Chrome + 真实 LuCI：登录、页面与环境 RPC 加载、自启动开关、每天/每周/每月计划与时间、原生“保存并应用”持久提交、恢复配置并截图。
- CGI 回归：路径查询串、旧域名、未知域名、控制字符与响应头注入检查；独立 Web 目录的真实 HTTP 请求覆盖根路径及 LuCI 同名路径。
- 独立重定向回归：真实 DNS / 302、LAN 客户端转发、主服务 HTTPS 与端口设置、端口及地址变更、procd 异常恢复、防火墙重载、PID 遗失、参数及端口冲突、旧版配置迁移、同名规则所有权，以及 nftables / iptables 清理。
- 诊断回归：curl 传输/HTTP 分类、IPv6/MAC/URL 认证信息脱敏、导出排队等待与历史日志标记；OpenWrt 容器注入部分测速失败、异常记录、DNS 冲突、完整域名/SRV/AAAA 验收、较大报告导出、防火墙全部/部分读取失败、零速度、运行中及排队取消、重启后日志读取、持久写入失败和空/缺失的 Passwall 集合。
- 十一项可重复的 OpenWrt 集成测试：模拟 CDN 选优与失败保留，实际 DNS A / AAAA / SRV，启停回滚，受限 RPC，任务重启取消，锁释放，健康检查规则修复，日志轮转，cron 校验，未知 DNS 重定向检测，更新 IP 后刷新主 DNS 缓存，日/周/月计划，启动钩子，以及运行中 DNS 副本的停用清理。DNS 副本测试使用真实 dnsmasq 与模拟的 Passwall 重载脚本。

公网实际测速出现大量超时，未形成稳定复测结果；因此**没有证明该插件在你的宽带上能提升 Xbox 下载速度**。模拟测试只证明控制逻辑。

没有 Xbox 硬件测试，没有完整真实 Passwall/ChinaDNS 集成测试，未验证所有 OpenWrt / LuCI 版本及 32 位 ARM。测试容器的 LuCI 已通过真实浏览器渲染及保存应用检查；目标设备的实际分流、DNS 链路和下载效果仍须验证。

## 9. 构建与来源

### GitHub Actions 发布

- 推送到 **`release/test`** 分支：自动构建 Beta 并创建 GitHub Pre-release。
- 推送 **`vX.Y.Z`** 或 **`X.Y.Z`** tag：自动构建正式版并创建普通 Release。
- 正式 tag 必须与运行脚本 `VERSION` 和 Makefile `PKG_VERSION` 一致。
- Beta 使用 `X.Y.Z~beta.构建号.g提交短SHA`，保证同基线正式版可以覆盖升级 Beta。
- 测试、校验通过后才发布，包含 IPK、完整源码、构建信息、精确安装说明和 SHA256 校验和。

详细规则、权限和首次提交步骤见 [发布指南](docs/RELEASING.md)。工作流位于 [.github/workflows/release.yml](.github/workflows/release.yml)。

### 本地构建

```sh
python3 tools/build_ipk.py  # 生成 dist 下的 ipk 和源码 tar.gz
node tests/test_luci.js
python3 tests/test_dns_cleanup.py
python3 tests/test_service_locks.py
python3 tests/test_schedule_startup.py
python3 tests/test_diagnostics.py
python3 tests/test_cgi.py
python3 tests/test_redirect.py
# 只在可丢弃的 Docker OpenWrt 容器运行，绝不可指向主力设备：
python3 tests/test_cgi_openwrt.py xa-test  # BusyBox CGI、独立 HTTP 与 LuCI 共存
python3 tests/test_redirect_openwrt.py xa-test  # 专用入口、LAN 客户端、启停与迁移
python3 tests/test_openwrt.py xa-test
python3 tests/test_service_stop_openwrt.py xa-test  # 服务锁超时、清理失败、PID 文件异常
python3 tests/test_diagnostics_openwrt.py xa-test
```

附带 OpenWrt SDK `Makefile`；当前交付 `.ipk` 使用 Python 标准库构建文本/脚本数据包，已通过 opkg 安装，不声称经过完整 SDK 编译。

Xbox 两组候选种子来自 [isbasex/xbox-speed-test](https://github.com/isbasex/xbox-speed-test)。新平台域名、参考节点与资源来自 [XboxDownload](https://github.com/skydevil88/XboxDownload)，具体来源与校验方式见[平台下载组](docs/PLATFORMS.md)。Shell 实现独立编写。

原理参考 [XboxDownload OpenWrt 文档](https://github.com/skydevil88/XboxDownload/blob/master/README_OpenWrt.md)、[SRV 讨论](https://github.com/skydevil88/XboxDownload/issues/156) 和 [Passwall 源码](https://github.com/Openwrt-Passwall/openwrt-passwall)。上游可能变化，本测试版不承诺长期自动兼容。
