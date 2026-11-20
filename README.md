# Xbox Accel — OpenWrt / LuCI 插件

版本：**0.3.0 测试版**。脚本和界面均与 CPU 架构无关；已经在官方 OpenWrt 24.10.4 ARM64 rootfs 上通过安装及集成测试。实际 Xbox 下载效果需在目标设备上验证。

## 1. 功能与结构

```text
Xbox → 主 dnsmasq
         ├─ 明确列出的 Xbox 下载域名 → 127.0.0.1:5335 插件解析实例
         │                              ├─ 返回优选 CDN IP
         │                              └─ 或返回路由器 LAN IP → HTTP 302 → .cn CDN
         └─ 其他查询 → 原来的 ChinaDNS / 上游，保持不变
```

- **国内 CDN 优选**、**国际 CDN 优选**、**com→cn 重定向**分开配置。
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

依赖由 opkg 安装：`luci-base`、`rpcd`、`curl`、`uhttpd`、`jshn`。必须已存在标准 dnsmasq 或 dnsmasq-full；插件不会替换它。BusyBox 需提供 flock、nslookup 等标准命令。

旧版升级时若出现多个 `flock 1000` 持续等待，请按[服务锁恢复步骤](docs/UPGRADE_LOCK_RECOVERY.md)修补现有脚本、重启路由器，再安装包含修复的软件包。

安装后刷新或重新登录 LuCI，进入 **服务 → Xbox 下载加速**。**默认未启用**，首次安装不会添加主 DNS 规则、修改 Web 配置或创建测速计划。

建议先“重新检测”，选择运营商和测速周期，再启用并保存应用。不要把测速时取得的 IP 当作经过微软认证的节点。

### 源码安装 / 使用 apk 的新版本 OpenWrt

源码安装脚本兼容 opkg 和 apk，但 apk 系统的实际安装尚未验证；`.ipk` 不能直接交给 apk 安装。

```sh
tar -xzf xbox-accel-0.3.0.tar.gz
cd xbox-accel-0.3.0
sh install.sh
```

缺少必要依赖时脚本尝试前置安装。升级前先让旧脚本撤销旧规则，再覆盖文件，保留用户配置与测速结果。

## 3. LuCI 页面

上部固定显示运行状态、节点及测速操作；下部为 **基本设置 / 自动测速 / 高级设置 / 环境检测 / 运行日志** 五个原生 LuCI 标签页。环境检测和日志不再上下堆叠，切换标签不影响状态刷新或后台任务。展示面板不写入 UCI 配置。

### 基本设置

- 总开关；开机自启动；国内优选；国际优选；com→cn；运营商。
- 开机自启动默认开启，总开关启用后会在路由器启动时恢复功能与计划任务。关闭开机自启动后，启动钩子会清理上次遗留的规则；通过页面“重新应用”可手动启动。
- 关闭总开关或某项功能并保存应用后，自动撤销对应的转发规则、重新载入已识别的 Passwall DNS 副本并清空前置 DNS 缓存。清理失败会记录错误并保留旧规则信息供重试。
- “自动”按当前线路发现节点；电信/联通/移动额外查询参考网络地址来扩展候选，**不是准确的全网运营商节点数据库**，最终仍由当前路由器实测排名。

### 自动测速

选择频率，再设置具体时间。时间使用路由器系统时区，支持任意分钟。

| 频率 | 设置示例 |
|---|---|
| 每天 | 每天 05:15（默认） |
| 每周 | 星期三 08:35 |
| 每月 | 每月 1 号 04:09 |

每月日期支持 1 至 31 号，29 至 31 号在缺少该日期的月份跳过。关机期间错过的计划按下次预定时间执行。启用时缺少节点会立即测速；健康检查失败也会触发重测，所以可能在计划时间之外执行。

节点健康检查也使用下拉选择：关闭，或每 5 / 10 / 15 / 20 / 30 分钟。它只是小流量检查当前节点，连续失败才完整重测，与上面的定时完整测速独立。建议保持每 10 分钟。

旧版每天、每周、每月的简单计划会自动映射为频率、日期和时间字段；其他旧计划提供“保留旧版自定义计划”选项。特殊健康检查间隔也会保留。底层由 cron 调度，环境检测使用自然语言显示时间安排。

- 其他参数包括最大候选数量、初筛/复测的大小和时间、复测前 N 名、切换提升百分比。
- 默认最多两组各 60 个候选，初筛每个 4 MiB / 4 秒，前 3 名加当前节点复测，每个 64 MiB / 10 秒。
- **完整一轮理论下载量接近 1 GiB**，流量计费线路应调低参数。测速期间会占用带宽。

### 高级设置

- 备用 CDN / SRV 接管、Passwall 直连集合、必要时重启 Passwall。
- 主 dnsmasq 实例、独立解析端口、TTL、备用上游、重定向 LAN IP、可替换的测试 URL。
- 更换主 dnsmasq 实例前，先停用并保存，撤销旧实例规则。
- 备用上游只允许公网 IPv4，不能填写本机或回指主 DNS，防止循环解析。

### 环境检测

页面默认显示快速初查。点击“一键完整验收”后由 procd 在后台执行完整检查，页面保持可用；“查看上次验收”展示带检测时间的历史报告。“刷新初查”始终展示本次初查结果。完整检测包括：

- 系统、依赖、有效配置、53 端口实际监听程序。
- 解析实例是否运行、端口冲突、主 DNS 规则是否存在。
- 防火墙 DNS 重定向入口、Passwall dnsmasq 副本是否复制规则。
- 独立解析实例、主 DNS 和最多三个重定向入口逐项读回下载域名及备用 CDN 的 A / AAAA，以及三个 SRV 名称；每项列出预期和实际结果。关闭的功能和兼容性校验失败的域名单独标注。
- 本地 302 是否真正保留原路径及查询参数。
- 当前模式需要的测速资源是否可下载、cron 条目是否匹配配置并运行、直连集合是否包含当前启用功能的选中节点。
- 防火墙读取失败、部分后端读取失败、DNS 超时和查询格式异常均显示“未确认”；选中节点为空时集合检查显示“未执行”。

报告分别汇总路由器链路、测试资源和直连集合，实际流量路径与 Xbox 效果标为待确认。客户端 DNS、IPv6、缓存和透明代理都可能影响实际下载，建议在 Xbox 上观察目标节点及稳定速度。

## 4. 日志与任务处理

- 日志包含任务 ID、阶段、级别及稳定错误代码。curl 的退出码、HTTP 状态、下载字节、连接/总耗时及原始错误摘要一并保留；达到时间上限且样本足够时可以参与排名。
- `/tmp/xbox-accel/activity.log` 保存本次开机后的日志，任务结束后保留最近 500 行；LuCI 展示最近 300 行。任务结束时原子保存最多 500 行、128 KiB 的快照到 `/etc/xbox-accel/activity-last.log`，重启后页面明确标为历史快照。运行中的逐行日志写入内存，持久快照在任务结束时更新。
- 最近活动、最近完整测速及最近验收报告保存在 `/etc/xbox-accel`。摘要记录开始/结束时间、耗时、触发来源、分组结果、应用结果及退出码；重启或强制结束造成的记录缺口标为“异常中断”，存储失败显示警告。
- 后台操作返回“已受理”及任务 ID。最终状态区分成功、部分完成、失败、取消、跳过和异常中断；分组失败或应用验收异常会进入最终结果。
- “生成脱敏报告”在后台收集固定白名单字段，完成后下载 JSON。报告包含任务摘要、当前完整检测、历史验收、近期及上次任务日志；IPv4、IPv6、MAC 使用报告内一致别名，URL 路径、参数及认证信息脱敏。文件读取权限仅授予插件固定报告路径，分享前建议检查内容。
- 取消任务会等待终态保存；清空日志同时清除内存日志和持久快照，任务及验收摘要保留。任务和配置操作使用不同锁，常驻服务启动前关闭继承的锁描述符。

服务子进程清理全部继承描述符。停用从 `rc.common` 外层限制在 60 秒内；超时返回 `124 / SERVICE_TIMEOUT` 并保留清理记录。解析实例按实际二进制和完整配置路径识别，PID 文件遗失或错误时仍可发现并停止本插件实例；清理失败会返回失败，重启随后中止。

错误代码、退出码和实机验收步骤见 [诊断与验收说明](docs/DIAGNOSTICS.md)。

## 5. com→cn 条件与限制

重定向使用现有 uhttpd 的 HTTP 404 CGI 入口，不新建独立 HTTP 代理，也不承载整个游戏包。

- uhttpd main 必须监听 HTTP 80，且未强制跳转 HTTPS。
- 自动为明确接管的下载域名添加重绑定例外，允许返回路由器内网 IP；**不关闭全局 DNS 重绑定保护**。
- 只有 HTTP 可用；HTTPS 的证书验证不能被普通 302 服务绕过。本插件不伪造证书、不关闭证书校验。
- 如果下载路径恰好在路由器 Web 根目录中真实存在，404 CGI 不会触发。
- 国内资源不可用或过慢时，没有逐游戏自动回退功能，需要关闭 com→cn。
- 不擅自调整 LuCI 管理端口、不开放 WAN 防火墙、不全局关闭 IPv6。

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
xbox-accel start_test    # 后台测速
xbox-accel test cn       # 当前终端只测国内
xbox-accel cancel        # 取消任务
xbox-accel clear_log
xbox-accel reset         # 清除优选结果
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
- CGI 四项回归：路径查询串、旧域名、未知域名、CR/LF 响应头注入拒绝。
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
# 只在可丢弃的 Docker OpenWrt 容器运行，绝不可指向主力设备：
python3 tests/test_cgi_openwrt.py xa-test  # BusyBox CGI、真实 302 与 LuCI 共存
python3 tests/test_openwrt.py xa-test
python3 tests/test_service_stop_openwrt.py xa-test  # 服务锁超时、清理失败、PID 文件异常
python3 tests/test_diagnostics_openwrt.py xa-test
```

附带 OpenWrt SDK `Makefile`；当前交付 `.ipk` 使用 Python 标准库构建文本/脚本数据包，已通过 opkg 安装，不声称经过完整 SDK 编译。

候选种子来自 [isbasex/xbox-speed-test](https://github.com/isbasex/xbox-speed-test)。Shell 实现独立编写，未复制其 Node.js 业务代码。

原理参考 [XboxDownload OpenWrt 文档](https://github.com/skydevil88/XboxDownload/blob/master/README_OpenWrt.md)、[SRV 讨论](https://github.com/skydevil88/XboxDownload/issues/156) 和 [Passwall 源码](https://github.com/Openwrt-Passwall/openwrt-passwall)。上游可能变化，本测试版不承诺长期自动兼容。
