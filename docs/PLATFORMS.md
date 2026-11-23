# 平台下载组

保留 Xbox 国内 `cn` 与国际 `com` 两组，增加微软商店 `store`、PlayStation `ps`、Xbox 老游戏下载 `xbox_old` 和 Switch `switch`。四个新组默认关闭，独立保存候选、节点、域名兼容性与任务结果。启用后参加现有定时测速、健康检查、DNS 规则同步、Passwall 直连和停用清理。

每组按主测速资源测量吞吐。微软商店、PlayStation 与 Xbox 老游戏下载逐域名验证资源兼容性，通过的域名使用选中 IPv4；Switch 默认用公开测速节点映射游戏下载域名，并提供可选的实际资源增强校验。主资源暂时失效时保留旧节点；次要资源校验失败时，该域名继续使用上游解析，分组结果显示部分完成。客户端使用的节点与实际游戏速度由对应设备确认。

## 微软商店

域名：

- `dl.delivery.mp.microsoft.com`
- `tlu.dl.delivery.mp.microsoft.com`
- `1d.tlu.dl.delivery.mp.microsoft.com`

默认选择微软商店应用的安装包，也可选择 Xbox 应用。链接通过 XboxDownload 的公共接口获取：

```text
https://xbox.skydevil.xyz/Game/GetAppPackage?WuCategoryId=<应用 GUID>
```

接口请求使用 `User-Agent: XboxDownload`、`X-Organization: XboxDownload` 与 `X-Author: Devil`。商店 GUID 为 `64293252-5926-453c-9494-2d4021f1c78d`，Xbox 应用 GUID 为 `986a47b3-0085-4c0c-b3b3-3b806f969b00`。

解析接口 JSON 后校验平台域名、至少 1 MiB 的包大小、`P1` 过期时间与 `P4` 签名。链接剩余有效期超过五分钟时复用；测速、健康检查或完整验收发现缓存过期时刷新。健康检查的首次资源请求失败后还会尝试刷新链接。接口获取失败单独记录为 `STORE_LINK_FETCH_FAILED`，节点与现有 DNS 规则继续保留。

签名链接以权限 `600` 缓存在 `/tmp/xbox-accel/store-resource.json`。该缓存随关机清除。任务日志隐藏查询参数；导出报告隐藏 URL 路径与参数。可通过 `test_url_store` 配置手动测速链接，手动签名链接由使用者更新。

## PlayStation

| 域名 | 默认校验资源 |
|---|---|
| `gst.prod.dl.playstation.net` | `/networktest/get_192m`，192 MiB 公开测试文件，同时用于选优 |
| `gs2.ww.prod.dl.playstation.net` | XboxDownload 提供的 PS4 生化危机 7 下载包 |
| `zeus.dl.playstation.net` | XboxDownload 提供的 PS3 GTA5 下载包 |
| `ares.dl.playstation.net` | 在 `verification_urls_ps` 中配置该域名的实际资源 |

完整路径定义在 [`platforms.sh`](../files/usr/share/xbox-accel/platforms.sh) 中。每个域名使用对应路径；`verification_urls_ps` 可按域名覆盖默认资源或补充 ares。每个域名使用列表中的第一条链接，最多配置八条。新增或更换校验资源后，运行一次“只测 PlayStation”。

## Xbox 老游戏下载

国内域名为 `dlassets.xboxlive.cn`、`dlassets2.xboxlive.cn`。默认使用 XboxDownload 的刺客信条 4 下载资源：

```text
http://dlassets.xboxlive.cn/public/content/0044aef3-bd7e-4478-8782-6191546dcd9a/f6585e19-765c-49c3-ba77-326c3766e05a/1.0.4.0.60281fce-b9c0-49f6-a2b6-027abad22637/ACBF_1.0.4.0_x64__b6krnev7r9sf8
```

该组可与现有 Xbox 国际 HTTP 重定向配合：`dlassets.xboxlive.com` 和 `dlassets2.xboxlive.com` 先通过 302 转到国内域名，国内域名再解析到该组选中节点。

## Switch

公开测试域名为：

- `ctest-dl-lp1.cdn.nintendo.net`
- `ctest-dl.p01.lp1.ctest.srv.nintendo.net`

默认主测速资源为 `http://ctest-dl-lp1.cdn.nintendo.net/30m`，请求使用 `User-Agent: XboxDownload/Nintendo NX`。

公开主资源验证通过后，以下三个游戏下载域名默认映射到选中 IPv4：

- `atum.hac.lp1.d4c.nintendo.net`
- `nemof.p01.lp1.nemo.srv.nintendo.net`
- `nemof.hac.lp1.nemo.srv.nintendo.net`

启用 Switch 并完成公开测速即可应用默认映射。已有公开主资源验证结果的安装可通过“重新应用”补齐游戏下载域名，沿用现有选中节点。默认映射的验收检查这三个域名的 A 与 AAAA 响应；实际游戏包兼容性和设备下载速度可进一步验证。

“平台下载 → Switch 游戏下载校验链接（可选）”接受完整游戏链接，包括必要的签名参数。填写后，保存并应用会重新测速并增强校验对应域名；资源校验通过后接管，失败时该域名使用上游解析，其余默认映射继续生效。配置列表最多八条，每个域名使用第一条链接。上传测速域名与屏蔽规则由设备和原有网络规则处理。

增强校验证据仅保存“域名 + 链接摘要”至 `/etc/xbox-accel/resources_switch`。路径或签名改变后，旧摘要失效，对应域名等待重新校验；删除链接并重新应用后恢复该域名的默认映射。健康检查验证公开主资源和已接管的增强资源。界面分别显示公开测速节点、准备接管的游戏域名数量、默认映射数量与增强校验数量，完整验收将默认映射的 DNS 结果和增强游戏资源验证分别展示。

XboxDownload 同样使用公开资源选优，将 Nintendo 选中节点写入游戏下载域名组。此轮核查的上游提交为 `0f5bab26309562c09be45f4fc59dca1203560354`；其中提供 `/30m` 公共资源，实际 atum/nemof 游戏包路径由使用者补充。

## 配置与命令

“平台下载”页集中提供 Xbox 国内 CDN 优选、国际 CDN 优选、国际下载重定向，以及四个扩展平台的开关、资源覆盖与额外公网 IPv4 候选。每组自动通过 DNS/HTTPDNS 发现节点，并使用独立的 `seeds.<组名>` 参考列表；节点仍由当前线路实测排名。

```sh
uci set xbox-accel.main.optimize_store='1'
uci set xbox-accel.main.store_test_app='store'
uci set xbox-accel.main.optimize_ps='1'
uci set xbox-accel.main.optimize_xbox_old='1'
uci set xbox-accel.main.optimize_switch='1'
uci commit xbox-accel

xbox-accel start_test store
xbox-accel start_test ps
xbox-accel start_test xbox_old
xbox-accel start_test switch
```

可选资源配置字段为 `test_url_<组名>`，额外节点为 `list extra_ips_<组名>`。测速起始偏移由 `test_offset_<组名>` 指定；PS 与 Switch 的其他独立资源从零偏移开始验证。URL 接受已知平台域名的 HTTP/HTTPS 默认端口，保留完整资源路径；IPv4 候选使用公网地址。

HTTPS 请求使用域名 URL 与 `--resolve` 指向候选 IP，保留 Host、SNI 和证书校验。新平台域名通过精确 `host-record`、本地 AAAA 空响应与子域上游例外组合配置；微软下载父域名的其他子域继续查询插件备用上游。软件包增加 `ca-bundle` 依赖。

每组最大理论采样流量为：

```text
候选数 × 初筛大小 +（复测前 N 名 + 1 个当前节点）× 复测大小
```

默认参数对应每组最多约 496 MiB；启用组分别累加。Switch 的公开文件为 30 MiB，实际流量还受文件长度与时间上限限制。建议先启用正在使用的平台，并按线路带宽调整候选数和采样大小。

## 来源与验证

- [XboxDownload 域名组](https://github.com/skydevil88/XboxDownload/blob/master/XboxDownload/Services/DnsMappingGenerator.cs)
- [XboxDownload 测速资源](https://github.com/skydevil88/XboxDownload/blob/master/XboxDownload/Models/SpeedTest/SpeedTestDataBuilder.cs)
- [XboxDownload 公共测速选优](https://github.com/skydevil88/XboxDownload/blob/0f5bab26309562c09be45f4fc59dca1203560354/XboxDownload/Services/SpeedTestService.cs)
- [XboxDownload 商店链接刷新实现](https://github.com/skydevil88/XboxDownload/blob/master/XboxDownload/Services/UpdateService.cs#L924)
- [候选 IP 列表](https://github.com/skydevil88/XboxDownload/tree/master/IP)，新组采用 2026-11-23 的分散 IPv4 快照

2026-11-23，微软商店与 Xbox 应用签名链接、PlayStation 的 gst/gs2/zeus 资源、Xbox 老游戏下载主资源和 Switch 两个公开测试域名均完成有界 Range 请求验证。资源有效性与网络分配会随时间、地区和节点变化。

本次商店资源在 `tlu.dl.delivery.mp.microsoft.com` 与 `1d.tlu.dl.delivery.mp.microsoft.com` 返回 `206`，`dl.delivery.mp.microsoft.com` 返回 `403`。程序逐域名校验，通过的域名使用优选节点，其余域名使用上游解析，并记录部分覆盖结果。

开发回归覆盖四组独立选优、商店链接过期与获取失败、无效接口响应、Switch 默认游戏下载映射、旧结果迁移、增强游戏链接摘要与删除恢复、后台取消与固定 RPC 动作、DNS A/AAAA 与子域回退、分组关闭清理、真实 TLS/SNI/Range 和诊断脱敏。实际游戏链接及设备下载速度在目标网络完成验收。
