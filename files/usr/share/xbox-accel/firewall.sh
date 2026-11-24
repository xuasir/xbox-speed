#!/bin/sh
# fw3/fw4 重载后恢复独立入口；配置操作持锁时由该操作完成恢复。
[ -s /var/etc/xbox-accel/redirect.conf ] || exit 0
exec /usr/bin/xbox-accel _restore_redirect
