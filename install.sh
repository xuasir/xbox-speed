#!/bin/sh
# xbox-accel 安装脚本，在 OpenWrt 路由器上以 root 执行：
#   sh install.sh            安装或升级
#   sh install.sh remove     卸载（保留 /etc/config/xbox-accel 和测速结果）
#   sh install.sh purge      卸载并删除配置与测速结果
# 使用 opkg 的系统也可以直接安装 .ipk；本脚本同时适用于使用 apk 的新版本系统。

set -e
SRC=$(cd "$(dirname "$0")" && pwd)/files
FILES="
/usr/bin/xbox-accel
/etc/init.d/xbox-accel
/www/cgi-bin/xbox-accel-redirect
/usr/share/xbox-accel/seeds.cn
/usr/share/xbox-accel/seeds.com
/usr/share/xbox-accel/seeds.store
/usr/share/xbox-accel/seeds.ps
/usr/share/xbox-accel/seeds.xbox_old
/usr/share/xbox-accel/seeds.switch
/usr/share/xbox-accel/platforms.sh
/usr/share/xbox-accel/ecs.telecom
/usr/share/xbox-accel/ecs.unicom
/usr/share/xbox-accel/ecs.mobile
/usr/share/xbox-accel/diagnostics.sh
/usr/share/xbox-accel/verification.sh
/usr/share/xbox-accel/application.sh
/usr/share/xbox-accel/privacy.sh
/usr/share/xbox-accel/quick-check.sh
/usr/libexec/rpcd/xbox-accel
/usr/share/luci/menu.d/luci-app-xbox-accel.json
/usr/share/rpcd/acl.d/luci-app-xbox-accel.json
/www/luci-static/resources/view/xbox-accel/overview.js
"
EXECUTABLES="/usr/bin/xbox-accel /etc/init.d/xbox-accel /www/cgi-bin/xbox-accel-redirect /usr/libexec/rpcd/xbox-accel"

[ "$(id -u)" = 0 ] || { echo "请用 root 执行" >&2; exit 1; }
[ -f /etc/openwrt_release ] || { echo "这不是 OpenWrt 系统" >&2; exit 1; }

UPDATED=0
pkg_install() {
	if command -v opkg >/dev/null 2>&1; then
		[ "$UPDATED" = 1 ] || opkg update >/dev/null 2>&1 || true
		UPDATED=1
		opkg install "$@" >/dev/null
	elif command -v apk >/dev/null 2>&1; then
		[ "$UPDATED" = 1 ] || apk update >/dev/null 2>&1 || true
		UPDATED=1
		apk add "$@" >/dev/null
	else
		return 1
	fi
}

# need 说明 检测命令 软件包
need() {
	if sh -c "$2" >/dev/null 2>&1; then
		echo "  $1：已安装"
		return 0
	fi
	echo "  $1：正在安装 $3 ..."
	pkg_install "$3" && sh -c "$2" >/dev/null 2>&1 || { echo "  $3 安装失败，请手动安装后重试" >&2; exit 1; }
}

refresh_luci() {
	/etc/init.d/rpcd reload >/dev/null 2>&1 || true
	rm -f /tmp/luci-indexcache*
	rm -rf /tmp/luci-modulecache
}

do_remove() {
	if [ -x /etc/init.d/xbox-accel ]; then
		/etc/init.d/xbox-accel stop || { echo "停用失败，已取消卸载；请先解决上面的错误" >&2; exit 1; }
		/etc/init.d/xbox-accel disable >/dev/null 2>&1 || true
	fi
	for f in $FILES; do rm -f "$f"; done
	rmdir /usr/share/xbox-accel /www/luci-static/resources/view/xbox-accel 2>/dev/null || true
	rm -rf /var/etc/xbox-accel /var/run/xbox-accel /tmp/xbox-accel
	refresh_luci
}

case "${1:-install}" in
remove)
	do_remove
	echo "已卸载（配置与测速结果保留在 /etc/config/xbox-accel、/etc/xbox-accel）"
	exit 0
	;;
purge)
	do_remove
	rm -rf /etc/xbox-accel /etc/config/xbox-accel
	echo "已卸载并清除配置"
	exit 0
	;;
install) ;;
*)
	echo "用法：sh install.sh [install|remove|purge]" >&2
	exit 1
	;;
esac

[ -d "$SRC" ] || { echo "找不到 $SRC" >&2; exit 1; }

echo "== 系统"
. /etc/openwrt_release
echo "  $DISTRIB_DESCRIPTION（$DISTRIB_ARCH）"

echo "== 依赖"
[ -x /usr/sbin/dnsmasq ] || { echo "  缺少 dnsmasq，无法继续" >&2; exit 1; }
echo "  dnsmasq：$(/usr/sbin/dnsmasq -v | awk 'NR==1{print $3}')"
command -v flock >/dev/null 2>&1 || { echo "  缺少 flock（通常由 BusyBox 提供）" >&2; exit 1; }
need "curl" "command -v curl" curl
need "HTTPS CA 证书" "test -s /etc/ssl/certs/ca-certificates.crt" ca-bundle
need "jshn" "test -f /usr/share/libubox/jshn.sh" jshn
need "rpcd" "test -x /sbin/rpcd" rpcd
need "LuCI" "test -f /www/luci-static/resources/luci.js" luci-base
if [ -x /usr/sbin/uhttpd ]; then
	echo "  uhttpd：已安装"
else
	echo "  uhttpd：未安装。com→cn 重定向不可用，CDN 优选不受影响"
fi

# 升级时先让旧版本撤销它写入的规则，再替换文件
if [ -x /etc/init.d/xbox-accel ]; then
	/etc/init.d/xbox-accel stop || { echo "旧版本停用失败，已取消升级" >&2; exit 1; }
fi

echo "== 文件"
for f in $FILES; do
	mkdir -p "$(dirname "$f")"
	cp "$SRC$f" "$f"
done
chmod 755 $EXECUTABLES
if [ -f /etc/config/xbox-accel ]; then
	echo "  保留现有配置 /etc/config/xbox-accel"
else
	cp "$SRC/etc/config/xbox-accel" /etc/config/xbox-accel
	chmod 600 /etc/config/xbox-accel
fi
/etc/init.d/xbox-accel enable
refresh_luci
echo "  已安装 xbox-accel $(/usr/bin/xbox-accel --version)"

if [ "$(uci -q get xbox-accel.main.enabled)" = 1 ]; then
	/etc/init.d/xbox-accel restart
	echo "  服务已重新启动"
fi

cat <<'TIP'

安装完成。插件默认不启用，此时没有改动任何系统配置。
  网页：LuCI → 服务 → Xbox 下载加速（看不到菜单时请重新登录 LuCI）
  命令：xbox-accel check     环境检测
        xbox-accel enable    启用（会自动开始第一次测速）
TIP
