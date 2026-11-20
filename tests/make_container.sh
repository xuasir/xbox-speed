#!/bin/sh
# 创建一个用完即弃的 OpenWrt（arm64）容器，供 test_openwrt.py 使用。
# 用法：sh tests/make_container.sh [容器名] [映射到本机的 LuCI 端口]
# 容器需要特权模式：OpenWrt 的 procd 要用 ujail 启动 dnsmasq。
set -e
NAME=${1:-xa-test}
PORT=${2:-18080}
IMAGE=openwrt/rootfs:armsr-armv8-24.10.4

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --privileged --platform linux/aarch64_generic \
	-p "127.0.0.1:$PORT:80" "$IMAGE" /sbin/init >/dev/null
sleep 12
ADDR=$(docker inspect "$NAME" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}/{{.IPPrefixLen}} {{.Gateway}}{{end}}')
set -- $ADDR
# netifd 把 eth0 并入 br-lan 并换成 192.168.1.1，这里把容器原来的地址和默认路由加回去
docker exec "$NAME" sh -c "
	ip addr add $1 dev br-lan
	ip route add default via $2
	echo 'nameserver 223.5.5.5' > /tmp/resolv.conf.d/resolv.conf.auto
	(/etc/init.d/dnsmasq restart >/dev/null 2>&1 &)
	for i in 1 2 3 4 5 6 7 8 9 10; do
		nslookup -type=a openwrt.org 127.0.0.1 2>/dev/null | grep -q '^Name:' && { echo 'container ready'; exit 0; }
		sleep 2
	done
	echo 'container started, but DNS does not work' >&2
	exit 1
"
