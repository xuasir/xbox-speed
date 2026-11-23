#!/bin/sh
# 独立 HTTP 302 服务：专用虚拟 IPv4 的 80 端口转入插件自己的监听端口。

redirect_local_ip() {
    ip -4 addr show 2>/dev/null | awk '/inet / {sub(/\/.*/, "", $2); print $2}' | grep -qxF "$1"
}

# 旧版 redirect_ip 表示路由器地址；版本标记兼容升级前已完成的旧服务清理。
redirect_ip() {
    local value
    value=$(cfg redirect_ip 192.0.2.1)
    if { [ "$(cfg redirect_service_version 0)" != 1 ] || [ -e "$STATE_DIR/uhttpd_pending" ] || [ -e "$STATE_DIR/uhttpd_error_page" ] || [ "$(uci -q get uhttpd.main.error_page)" = "$CGI_URL" ]; } && redirect_local_ip "$value"; then
        echo 192.0.2.1
    else
        echo "$value"
    fi
}

redirect_port() { cfg redirect_port 18081; }

redirect_device() {
    local device
    device=$(ubus call network.interface.lan status 2>/dev/null | jsonfilter -e '@.l3_device' 2>/dev/null)
    [ -n "$device" ] || device=$(uci -q get network.lan.device)
    [ -n "$device" ] || device=$(uci -q get network.lan.ifname)
    echo "${device:-br-lan}"
}

redirect_backend() {
    if command -v nft >/dev/null 2>&1; then echo nft;
    elif command -v iptables >/dev/null 2>&1; then echo iptables;
    else return 1; fi
}

# 使用二进制与独立 Web 根目录验证进程身份，兼容 PID 文件遗失和端口变更。
redirect_process() {
    case "$1" in ''|*[!0-9]*) return 1 ;; esac
    grep -qF "$REDIRECT_ROOT" "/proc/$1/cmdline" 2>/dev/null || return 1
    tr '\000' '\n' <"/proc/$1/cmdline" 2>/dev/null |
        awk -v root="$REDIRECT_ROOT" 'previous=="-h" && $0==root {found=1} {previous=$0} END {exit !found}' || return 1
    cmp -s "/proc/$1/exe" /usr/sbin/uhttpd
}

redirect_pids() {
    local process pid
    for process in /proc/[0-9]*; do
        pid=${process##*/}
        redirect_process "$pid" && echo "$pid"
    done
    return 0
}

redirect_running() {
    redirect_process "$(cat "$RUN_DIR/redirect.pid" 2>/dev/null)" && return 0
    [ -n "$(redirect_pids)" ]
}

redirect_listener_ready() {
    local pid pids
    pids=$(netstat -lntp 2>/dev/null | awk -v address="$1:$2" '$4==address && $6=="LISTEN" {split($NF, process, "/"); print process[1]}')
    for pid in $pids; do redirect_process "$pid" && return 0; done
    return 1
}

redirect_ready() {
    redirect_running && redirect_listener_ready 127.0.0.1 "$(redirect_port)" &&
        redirect_listener_ready "$(lan_ip)" "$(redirect_port)"
}

redirect_port_available() {
    local hex listeners pids pid inode fd ours
    hex=$(printf '%04X' "$1")
    listeners=$(awk -v port=":$hex" '$2 ~ (port "$" ) && $4=="0A" {print $10}' /proc/net/tcp /proc/net/tcp6 2>/dev/null)
    [ -n "$listeners" ] || return 0
    pids=$(redirect_pids)
    for inode in $listeners; do
        ours=0
        for pid in $pids; do
            for fd in /proc/"$pid"/fd/*; do
                [ "$(readlink "$fd" 2>/dev/null)" = "socket:[$inode]" ] && ours=1 && break
            done
            [ "$ours" = 1 ] && break
        done
        [ "$ours" = 1 ] || return 1
    done
    return 0
}

redirect_blocker() {
    local entry listen port device route
    [ -x /usr/sbin/uhttpd ] || { echo '缺少 uhttpd 程序'; return; }
    [ -r "$SHARE_DIR/redirect.cgi" ] || { echo '缺少独立重定向脚本'; return; }
    redirect_backend >/dev/null || { echo '需要 nftables 或 iptables'; return; }
    port=$(redirect_port)
    case "$port" in ''|0*|*[!0-9]*) echo 'redirect_port 需要不带前导零的整数'; return ;; esac
    [ "$port" -ge 1024 ] && [ "$port" -le 65535 ] || { echo 'redirect_port 必须在 1024..65535'; return; }
    [ "$port" != "$(cfg resolver_port 5335)" ] || { echo '重定向端口与解析端口相同，请选择独立端口'; return; }
    redirect_port_available "$port" || { echo "重定向端口 $port 被其他服务占用"; return; }
    entry=$(redirect_ip)
    [ "$(printf '%s' "$entry" | ipv4s)" = "$entry" ] &&
        printf '%s\n' "$entry" | awk -F. 'NF==4 && $1>0 && $1<224 && $1!=127 && !($1==169 && $2==254) && $4>0 && $4<255 {valid=1; for(i=1;i<=4;i++) if(length($i)>1 && substr($i,1,1)=="0") valid=0} END {exit !valid}' ||
        { echo 'redirect_ip 需要有效的单播 IPv4 专用地址'; return; }
    redirect_local_ip "$entry" && { echo 'redirect_ip 已是本机地址，请使用专用虚拟地址（默认 192.0.2.1）'; return; }
    route=$(ip -4 route get "$entry" 2>/dev/null)
    if printf '%s\n' "$route" | awk '/ dev / && !/ via / {found=1} END {exit !found}'; then
        echo 'redirect_ip 位于直连网络或现有专用路由中，请选择路由器经网关到达的专用地址'; return
    fi
    listen=$(lan_ip)
    [ -n "$listen" ] && redirect_local_ip "$listen" || { echo '需要可用的路由器 LAN IPv4 地址'; return; }
    device=$(redirect_device)
    printf '%s\n' "$device" | grep -qE '^[A-Za-z0-9_.:-]+$' && ip link show dev "$device" >/dev/null 2>&1 ||
        { echo '无法确定 LAN 网络设备'; return; }
    return 0
}

migrate_redirect_ip() {
    local value previous
    value=$(redirect_ip)
    previous=$(cfg redirect_ip 192.0.2.1)
    [ "$value" = "$previous" ] && [ "$(cfg redirect_service_version 0)" = 1 ] && return 0
    uci -q set "$NAME.main.redirect_ip=$value" && uci -q set "$NAME.main.redirect_service_version=1" && uci -q commit "$NAME" || return 1
    [ "$value" = "$previous" ] || note "旧版重定向入口已迁移到专用地址 $value"
    return 0
}

redirect_nft_owned() {
    nft list table ip xbox_accel_redirect 2>/dev/null | grep -qF 'comment "xbox-accel redirect"'
}

redirect_iptables_owned() {
    [ -e "$STATE_DIR/redirect_iptables" ] ||
        iptables -w 2 -t nat -S "$1" 2>/dev/null | grep -qF 'xbox-accel redirect'
}

clear_redirect_nft() {
    command -v nft >/dev/null 2>&1 || { [ "$(cat "$STATE_DIR/redirect_backend" 2>/dev/null)" != nft ]; return $?; }
    if nft list table ip xbox_accel_redirect >/dev/null 2>&1; then
        redirect_nft_owned || { note '同名 nftables 表由其他配置持有，保留该表'; return 1; }
        nft delete table ip xbox_accel_redirect || return 1
    else
        nft list tables >/dev/null 2>&1 || return 1
    fi
    return 0
}

clear_redirect_iptables() {
    local chain hook
    command -v iptables >/dev/null 2>&1 || { [ ! -e "$STATE_DIR/redirect_iptables" ]; return $?; }
    if [ -e "$STATE_DIR/redirect_iptables" ]; then iptables -w 2 -t nat -S >/dev/null 2>&1 || return 1; fi
    for chain in XBOX_ACCEL_PRE XBOX_ACCEL_OUT; do
        iptables -w 2 -t nat -S "$chain" >/dev/null 2>&1 || continue
        redirect_iptables_owned "$chain" || { note "同名 iptables 链 $chain 由其他配置持有，保留该链"; return 1; }
        case "$chain" in XBOX_ACCEL_PRE) hook=PREROUTING ;; *) hook=OUTPUT ;; esac
        while iptables -w 2 -t nat -C "$hook" -j "$chain" 2>/dev/null; do
            iptables -w 2 -t nat -D "$hook" -j "$chain" || return 1
        done
        iptables -w 2 -t nat -F "$chain" && iptables -w 2 -t nat -X "$chain" || return 1
    done
    rm -f "$STATE_DIR/redirect_iptables"
}

sync_redirect_include() {
    local type path
    type=$(uci -q get firewall.xbox_accel_redirect)
    path=$(uci -q get firewall.xbox_accel_redirect.path)
    if [ -n "$type" ] && { [ "$type" != include ] || [ "$path" != "$SHARE_DIR/firewall.sh" ]; }; then
        note '同名防火墙 include 由其他配置持有，保留该配置'; return 1
    fi
    if [ "$1" = 0 ]; then
        [ -n "$type" ] || return 0
        uci -q delete firewall.xbox_accel_redirect && uci -q commit firewall
        return $?
    fi
    if [ "$type" = include ] && [ "$(uci -q get firewall.xbox_accel_redirect.type)" = script ] &&
        [ "$(uci -q get firewall.xbox_accel_redirect.fw4_compatible)" = 1 ] && [ "$(uci -q get firewall.xbox_accel_redirect.reload)" = 1 ]; then return 0; fi
    uci -q set firewall.xbox_accel_redirect=include &&
        uci -q set "firewall.xbox_accel_redirect.path=$SHARE_DIR/firewall.sh" &&
        uci -q set firewall.xbox_accel_redirect.type=script &&
        uci -q set firewall.xbox_accel_redirect.fw4_compatible=1 &&
        uci -q set firewall.xbox_accel_redirect.reload=1 && uci -q commit firewall
}

gen_redirect_nft() {
    local entry=$1 listen=$2 port=$3 device=$4
    cat <<EOF
table ip xbox_accel_redirect { comment "xbox-accel redirect"; }
flush table ip xbox_accel_redirect
table ip xbox_accel_redirect {
    comment "xbox-accel redirect"
    chain prerouting {
        type nat hook prerouting priority -110; policy accept;
        iifname "$device" ip daddr $entry tcp dport 80 counter dnat to $listen:$port comment "xbox-accel redirect"
    }
    chain output {
        type nat hook output priority -110; policy accept;
        ip daddr $entry tcp dport 80 counter dnat to 127.0.0.1:$port comment "xbox-accel redirect"
    }
}
EOF
}

apply_redirect_firewall() {
    local entry=$1 listen=$2 port=$3 device=$4 backend=$5 new="$RUN_DIR/redirect.nft.new" chain hook
    case "$backend" in
    nft)
        if nft list table ip xbox_accel_redirect >/dev/null 2>&1; then
            redirect_nft_owned || { note '同名 nftables 表由其他配置持有，保留该表'; return 1; }
        fi
        clear_redirect_iptables || return 1
        gen_redirect_nft "$entry" "$listen" "$port" "$device" >"$new" || return 1
        nft -c -f "$new" && nft -f "$new" || { rm -f "$new"; return 1; }
        rm -f "$new"
        ;;
    iptables)
        clear_redirect_nft || return 1
        for chain in XBOX_ACCEL_PRE XBOX_ACCEL_OUT; do
            if iptables -w 2 -t nat -S "$chain" >/dev/null 2>&1; then
                redirect_iptables_owned "$chain" || { note "同名 iptables 链 $chain 由其他配置持有，保留该链"; return 1; }
            fi
        done
        : >"$STATE_DIR/redirect_iptables" || return 1
        for chain in XBOX_ACCEL_PRE XBOX_ACCEL_OUT; do
            iptables -w 2 -t nat -N "$chain" 2>/dev/null || iptables -w 2 -t nat -F "$chain" || return 1
            case "$chain" in XBOX_ACCEL_PRE) hook=PREROUTING ;; *) hook=OUTPUT ;; esac
            iptables -w 2 -t nat -C "$hook" -j "$chain" 2>/dev/null || iptables -w 2 -t nat -I "$hook" 1 -j "$chain" || return 1
        done
        iptables -w 2 -t nat -A XBOX_ACCEL_PRE -i "$device" -d "$entry" -p tcp --dport 80 -m comment --comment 'xbox-accel redirect' -j DNAT --to-destination "$listen:$port" || return 1
        iptables -w 2 -t nat -A XBOX_ACCEL_OUT -d "$entry" -p tcp --dport 80 -m comment --comment 'xbox-accel redirect' -j DNAT --to-destination "127.0.0.1:$port" || return 1
        ;;
    *) return 1 ;;
    esac
    printf '%s\n' "$backend" >"$STATE_DIR/redirect_backend"
}

redirect_firewall_present() {
    if command -v nft >/dev/null 2>&1 && nft list table ip xbox_accel_redirect >/dev/null 2>&1; then return 0; fi
    if command -v iptables >/dev/null 2>&1; then
        iptables -w 2 -t nat -S XBOX_ACCEL_PRE >/dev/null 2>&1 && return 0
        iptables -w 2 -t nat -S XBOX_ACCEL_OUT >/dev/null 2>&1 && return 0
    fi
    return 1
}

redirect_firewall_ready() {
    local entry listen port device backend pre out
    [ -s "$REDIRECT_CONF" ] || return 1
    read -r listen port entry device backend <"$REDIRECT_CONF"
    [ "$listen $port $entry $device $backend" = "$(lan_ip) $(redirect_port) $(redirect_ip) $(redirect_device) $(redirect_backend)" ] || return 1
    case "$backend" in
    nft)
        redirect_nft_owned || return 1
        pre=$(nft list chain ip xbox_accel_redirect prerouting 2>/dev/null) || return 1
        out=$(nft list chain ip xbox_accel_redirect output 2>/dev/null) || return 1
        printf '%s\n' "$pre" | grep -qF "iifname \"$device\" ip daddr $entry tcp dport 80" &&
            printf '%s\n' "$pre" | grep -qF "dnat to $listen:$port" &&
            printf '%s\n' "$out" | grep -qF "ip daddr $entry tcp dport 80" &&
            printf '%s\n' "$out" | grep -qF "dnat to 127.0.0.1:$port"
        ;;
    iptables)
        iptables -w 2 -t nat -C PREROUTING -j XBOX_ACCEL_PRE &&
            iptables -w 2 -t nat -C OUTPUT -j XBOX_ACCEL_OUT &&
            iptables -w 2 -t nat -C XBOX_ACCEL_PRE -i "$device" -d "$entry" -p tcp --dport 80 -m comment --comment 'xbox-accel redirect' -j DNAT --to-destination "$listen:$port" &&
            iptables -w 2 -t nat -C XBOX_ACCEL_OUT -d "$entry" -p tcp --dport 80 -m comment --comment 'xbox-accel redirect' -j DNAT --to-destination "127.0.0.1:$port"
        ;;
    *) return 1 ;;
    esac
}

sync_redirect() {
    local entry listen port device backend new="$REDIRECT_CONF.new"
    if [ "$1" = 0 ]; then
        sync_redirect_include 0 && clear_redirect_nft && clear_redirect_iptables || return 1
        rm -f "$REDIRECT_CONF" "$STATE_DIR/redirect_backend"
        return 0
    fi
    entry=$(redirect_ip); listen=$(lan_ip); port=$(redirect_port); device=$(redirect_device); backend=$(redirect_backend) || return 1
    mkdir -p "$REDIRECT_ROOT" "$RUN_DIR" "$STATE_DIR" || return 1
    cp "$SHARE_DIR/redirect.cgi" "$REDIRECT_ROOT/redirect.cgi.new" &&
        chmod 755 "$REDIRECT_ROOT/redirect.cgi.new" && mv "$REDIRECT_ROOT/redirect.cgi.new" "$REDIRECT_ROOT/redirect.cgi" || return 1
    : >"$REDIRECT_HTTPD_CONF" || return 1
    sync_redirect_include 1 || return 1
    apply_redirect_firewall "$entry" "$listen" "$port" "$device" "$backend" || return 1
    printf '%s %s %s %s %s\n' "$listen" "$port" "$entry" "$device" "$backend" >"$new" && mv "$new" "$REDIRECT_CONF"
}

restore_redirect_firewall() {
    local entry listen port device backend
    [ "$(cat "$RUN_DIR/applied_mode" 2>/dev/null)" = redirect ] && cfg_bool enabled 0 && [ -s "$REDIRECT_CONF" ] || return 0
    exec 7>"$RUN_DIR/apply.lock"
    flock -n 7 || return 0
    [ -s "$REDIRECT_CONF" ] || return 0
    read -r listen port entry device backend <"$REDIRECT_CONF"
    apply_redirect_firewall "$entry" "$listen" "$port" "$device" "$backend" || return 1
    passwall_direct "$entry"
}

stop_redirect() {
    local pid before after attempt signal
    for signal in TERM KILL; do
        for pid in $(redirect_pids); do
            [ "$pid" -gt 1 ] || continue
            before=$(sed 's/^[0-9]* (.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}')
            redirect_process "$pid" || continue
            after=$(sed 's/^[0-9]* (.*) //' "/proc/$pid/stat" 2>/dev/null | awk '{print $20}')
            [ -n "$before" ] && [ "$before" = "$after" ] || continue
            kill -"$signal" "$pid" 2>/dev/null || true
        done
        for attempt in 1 2 3; do
            redirect_running || { rm -f "$RUN_DIR/redirect.pid"; return 0; }
            sleep 1
        done
    done
    note '独立重定向进程仍存在，请检查服务停止错误'
    return 1
}
