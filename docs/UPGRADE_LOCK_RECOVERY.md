# 升级时服务锁持续等待的恢复步骤

受影响的旧版在重启 Passwall 时，将 OpenWrt `rc.common` 的文件描述符 1000 传给了 Passwall 的后台进程。sing-box、chinadns-ng、监控脚本及其子进程会持续持有 `/var/lock/procd_xbox-accel.lock`，导致 Xbox 服务的 `stop`、`reload`、`disable` 和软件包升级排队等待。

修复版统一关闭服务子进程的全部继承描述符，调用方继续持有原有锁。停止操作从 `rc.common` 外层设置 60 秒期限；实际解析进程按二进制身份与完整配置路径查验，覆盖 PID 文件和 procd 注册信息异常。清理失败会返回明确错误并保留待清理记录。

本次已确认旧版的文件描述符 1000 被 Passwall 后台进程继承。以下临时修补用于现有故障恢复；一次路由器重启会释放这些已持有的锁，随后安装完整修复版。

在路由器终端执行下面的命令，保存备份并临时修补旧版已安装脚本：

```sh
cp -p /usr/bin/xbox-accel /root/xbox-accel.before-lock-fix
sed -i '/^svc() /s/9>&-;/9>\&- 1000>\&-;/' /usr/bin/xbox-accel
/bin/sh -n /usr/bin/xbox-accel
sed -n '/^svc() /p' /usr/bin/xbox-accel
```

最后一行应输出：

```sh
svc() { "$@" >/dev/null 2>&1 7>&- 8>&- 9>&- 1000>&-; }
```

确认输出后执行：

```sh
sync
reboot
```

重启会短暂中断路由器网络，并清理当前排队的安装和服务操作。补丁保存在已安装脚本中，开机后即可生效。

路由器恢复后，将包含修复的 IPK 上传为 `/tmp/luci-app-xbox-accel.ipk`，执行：

```sh
opkg --force-reinstall install /tmp/luci-app-xbox-accel.ipk
```

验证升级完成后，打开 LuCI 的 Xbox 下载加速页面并刷新环境状态。安装新版时，软件包自身的 `prerm` 由已安装的旧版提供，因此在现有脚本中修补并释放锁是本次恢复的必要步骤。
