#!/bin/sh
# 由插件独立 uhttpd 的 error_page / index 调用：把国际下载请求 302 到国内域名。
# 其他请求维持普通的 404。

host=${HTTP_HOST%%:*}
case "$host" in
dlassets.xboxlive.com | dlassets2.xboxlive.com)
	target=dlassets.xboxlive.cn
	;;
assets1.xboxlive.com | assets2.xboxlive.com | d1.xboxlive.com | d2.xboxlive.com | \
	xvcf1.xboxlive.com | xvcf2.xboxlive.com | 5.xb.delivery.microsoft.com | \
	assets1.xboxlive.com.edgesuite.net | cl-glcb907925.gcdn.co | fg.microsoft.map.fastly.net)
	target=assets1.xboxlive.cn
	;;
*)
	printf 'Status: 404 Not Found\r\nContent-Type: text/plain\r\n\r\nNot Found\n'
	exit 0
	;;
esac

uri=$REQUEST_URI
# 八进制范围兼容 BusyBox tr，拦截 C0 / DEL（含原始 CR/LF）。
if [ "$(printf '%s' "$uri" | LC_ALL=C tr -d '\000-\037\177')" != "$uri" ]; then
	printf 'Status: 400 Bad Request\r\nContent-Length: 0\r\n\r\n'
	exit 0
fi
case "$uri" in /*) ;; *) uri=/ ;; esac
# 仅记录设备、白名单域名和时间，资源路径与签名继续留在请求中。
case "$REMOTE_ADDR" in ''|127.*|::1|*[!0-9a-fA-F:.]*) ;; *)
	if [ "${#REMOTE_ADDR}" -le 45 ] && [ -d /tmp/xbox-accel ]; then
		printf '%s %s %s %s\n' "$(date +%s)" "$REMOTE_ADDR" "$host" "$target" >>/tmp/xbox-accel/redirect-observation.log
	fi ;;
esac
printf 'Status: 302 Found\r\nLocation: http://%s%s\r\nContent-Length: 0\r\n\r\n' "$target" "$uri"
