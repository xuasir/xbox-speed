# Xbox Accel — OpenWrt / LuCI 插件

版本：**0.2.2 测试版**。脚本和界面均与 CPU 架构无关；已经在官方 OpenWrt 24.10.4 ARM64 rootfs 上通过安装及集成测试。未在用户的实际 Xbox、ARM 路由器和完整 Passwall/ChinaDNS 环境中验证，不承诺安装后一定提速。

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
- 总开关、手动测速、取消任务、清除结果、重新应用、实时日志和环境检测均可从 **LuCI → 服务 → Xbox 下载加速** 操作。
- 无 Node.js、Python 或 Docker 运行时依赖；Docker 只用于开发验证。

### 自动化

默认启用后：

1. 没有有效节点时，立即发起首次后台测速。
2. 每天 **05:15** 重新发现候选并测速，按路由器自身时区执行。
3. 每 **10 分钟** 健康检查。当前节点连续失败时重测；缺少节点时最多每 30 分钟重试。
4. 健康检查发现解析实例、主规则或已应用模式漂移时，重新应用配置。
5. 任务由 procd 管理；关闭浏览器不影响执行。flock 防止并发测速、健康检查和配置更新互相覆盖。

节点配置变化后，会用严格的进程/配置路径匹配对主 dnsmasq 和已识别的 Passwall DNS 副本发送 SIGHUP 清缓存，不重启 DHCP 服务。客户端自身缓存仍可能延迟；已经建立的 Xbox 下载连接不会因 DNS 更新而切换，必要时暂停并继续下载。

## 2. 安装

### 使用 opkg 的 OpenWrt（推荐）

将 `luci-app-xbox-accel_0.2.2-1_all.ipk` 上传到路由器，例如 `/root/`：

```sh
opkg update
opkg install /root/luci-app-xbox-accel_0.2.2-1_all.ipk
```

依赖由 opkg 安装：`luci-base`、`rpcd`、`curl`、`uhttpd`、`jshn`。必须已存在标准 dnsmasq 或 dnsmasq-full；插件不会替换它。BusyBox 需提供 flock、nslookup 等标准命令。

安装后刷新或重新登录 LuCI，进入 **服务 → Xbox 下载加速**。**默认未启用**，首次安装不会添加主 DNS 规则、修改 Web 配置或创建测速计划。

建议先“重新检测”，选择运营商和测速周期，再启用并保存应用。不要把测速时取得的 IP 当作经过微软认证的节点。

### 源码安装 / 使用 apk 的新版本 OpenWrt

源码安装脚本兼容 opkg 和 apk，但 apk 系统的实际安装尚未验证；`.ipk` 不能直接交给 apk 安装。

```sh
tar -xzf xbox-accel-0.2.2.tar.gz
cd xbox-accel-0.2.2
sh install.sh
```

缺少必要依赖时脚本尝试前置安装。升级前先让旧脚本撤销旧规则，再覆盖文件，保留用户配置与测速结果。

## 3. LuCI 页面

上部固定显示运行状态、节点及测速操作；下部为 **基本设置 / 自动测速 / 高级设置 / 环境检测 / 运行日志** 五个原生 LuCI 标签页。环境检测和日志不再上下堆叠，切换标签不影响状态刷新或后台任务。展示面板不写入 UCI 配置。

### 基本设置

- 总开关；国内优选；国际优选；com→cn；运营商。
- “自动”按当前线路发现节点；电信/联通/移动额外查询参考网络地址来扩展候选，**不是准确的全网运营商节点数据库**，最终仍由当前路由器实测排名。

### 自动测速

**直接选择下拉预设，不再填写表达式。**

| 选择 | 实际执行时间（路由器系统时区） |
|---|---|
| 每天 05:15（推荐） | 每天凌晨 05:15 完整测速一次 |
| 每 30 分钟 | 每小时的 00、30 分 |
| 每 1 小时 | 每小时整点 |
| 每 3 小时 | 每天 00、03、06、09、12、15、18、21 点 |
| 每 6 小时 | 每天 00、06、12、18 点 |
| 每 12 小时 | 每天 00、12 点 |
| 每天 HH:00 | 选择 00:00 到 23:00 的任意整点，每天执行一次 |
| 每周日 03:00 | 每周日凌晨 03:00 执行一次 |

小时计划按时钟刻度对齐，**不是从启用插件后开始倒计时**。路由器关机期间不会补跑错过的计划。启用时缺少节点会立即测速；健康检查失败也会触发重测，所以可能在计划时间之外执行。

节点健康检查也使用下拉选择：关闭，或每 5 / 10 / 15 / 20 / 30 分钟。它只是小流量检查当前节点，连续失败才完整重测，与上面的定时完整测速独立。建议保持每 10 分钟。

旧版自定义计划和特殊检查间隔会保留，不会升级时静默覆盖；可从下拉菜单改选明确的预设。底层仍由 cron 调度，但界面无需输入表达式，环境检测也使用自然语言显示时间安排。

- 其他参数包括最大候选数量、初筛/复测的大小和时间、复测前 N 名、切换提升百分比。
- 默认最多两组各 60 个候选，初筛每个 4 MiB / 4 秒，前 3 名加当前节点复测，每个 64 MiB / 10 秒。
- **完整一轮理论下载量接近 1 GiB**，流量计费线路应调低参数。测速期间会占用带宽。

### 高级设置

- 备用 CDN / SRV 接管、Passwall 直连集合、必要时重启 Passwall。
- 主 dnsmasq 实例、独立解析端口、TTL、备用上游、重定向 LAN IP、可替换的测试 URL。
- 更换主 dnsmasq 实例前，先停用并保存，撤销旧实例规则。
- 备用上游只允许公网 IPv4，不能填写本机或回指主 DNS，防止循环解析。

### 环境检测

与轻量状态轮询分开执行，避免每五秒做公网请求。检测包括：

- 系统、依赖、有效配置、53 端口实际监听程序。
- 解析实例是否运行、端口冲突、主 DNS 规则是否存在。
- 防火墙 DNS 重定向入口、Passwall dnsmasq 副本是否复制规则。
- 主 DNS 和最多三个重定向入口的实际 A / AAAA 返回是否符合预期。
- 本地 302 是否真正保留原路径及查询参数。
- 两组测速文件是否可下载、cron 是否运行、直连集合是否包含选中节点。

**路由器本机检测不能证明 Xbox 实际使用了这条路径**，特别是客户端使用其他 DNS、IPv6 DNS、缓存或特殊透明代理时。需在 Xbox 上验证。

## 4. 日志与任务处理

- 日志位于 `/tmp/xbox-accel/activity.log`，存储在内存文件系统，重启清空。
- 任务结束后最多保留最近 500 行；运行中可能短暂超过 500 行。LuCI 每次展示最近 300 行。
- 节点切换、重测等关键事件也写入系统日志 `xbox-accel` 标签。
- 页面支持清日志、取消测速；取消只处理本任务及其进程后代，不全局杀掉 curl 或其他下载。
- 任务和配置操作使用不同锁；启动其他常驻服务前关闭继承的锁文件描述符，避免更新后无法再次测速。

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

某些 Passwall 模式会把 LAN DNS 重定向到其 dnsmasq 副本，绕过主实例。程序会检测已知副本；默认规则不同步时重启 Passwall。该操作可能短暂中断其他代理连接，可关闭自动重启并自行操作。

候选和选中 IP 默认加入既有 `psw_white` nftset/ipset。**没有验证完整 Passwall 的分流行为**：自定义 ACL、关闭直连列表、特殊节点和 Passwall2 不一定遵循这个集合。共享 CDN IP 加入直连也可能影响其他访问相同 IP 的服务。

插件不删除共享集合里的元素，以免误删其他程序需要的地址；停止插件后元素可能继续存在，直到集合过期或 Passwall 重建。

## 7. 命令与回滚

```sh
xbox-accel check         # 环境检测
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
- 真实 Chrome + 真实 LuCI：登录、页面与环境 RPC 加载、实际修改复选框、原生“保存并应用”持久提交、恢复配置并截图。
- CGI 四项回归：路径查询串、旧域名、未知域名、CR/LF 响应头注入拒绝。
- 八项可重复的 OpenWrt 集成测试：模拟 CDN 选优与失败保留，实际 DNS A / AAAA / SRV，启停回滚，受限 RPC，任务重启取消，锁释放，健康检查规则修复，日志轮转，cron 校验，未知 DNS 重定向检测，以及更新 IP 后刷新主 DNS 缓存。

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
python3 tests/test_cgi.py
# 只在可丢弃的 Docker OpenWrt 容器运行，绝不可指向主力设备：
python3 tests/test_openwrt.py xa-test
```

附带 OpenWrt SDK `Makefile`；当前交付 `.ipk` 使用 Python 标准库构建文本/脚本数据包，已通过 opkg 安装，不声称经过完整 SDK 编译。

候选种子来自 [isbasex/xbox-speed-test](https://github.com/isbasex/xbox-speed-test)。Shell 实现独立编写，未复制其 Node.js 业务代码。

原理参考 [XboxDownload OpenWrt 文档](https://github.com/skydevil88/XboxDownload/blob/master/README_OpenWrt.md)、[SRV 讨论](https://github.com/skydevil88/XboxDownload/issues/156) 和 [Passwall 源码](https://github.com/Openwrt-Passwall/openwrt-passwall)。上游可能变化，本测试版不承诺长期自动兼容。
