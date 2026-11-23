#!/bin/sh
# Read-only router verification. No sniffing and no changes to proxy/DNS rules.

check_row() {
    local name=$1 level=$2 code=$3 detail=$4 hint=${5:-} scope=${6:-router} expected=${7:-} observed=${8:-}
    json_add_object ""
    json_add_string name "$name"; json_add_string level "$level"; json_add_string code "$code"
    json_add_string detail "$(safe_text "$detail")"; json_add_string remediation "$hint"
    json_add_string scope "$scope"; json_add_string expected "$expected"; json_add_string observed "$observed"
    json_close_object
    if [ -n "$TASK_ID" ]; then
        local severity=INFO
        [ "$level" = fail ] && severity=ERROR
        case "$level" in unknown|warn) severity=WARN ;; esac
        task_event "$severity" "$code" "$name: $detail expected=$expected observed=$observed"
    fi
    if [ "$scope" = router ]; then
        case "$level" in
        ok) V_PASS=$((V_PASS+1)) ;;
        fail) V_FAIL=$((V_FAIL+1)) ;;
        warn) V_WARN=$((V_WARN+1)) ;;
        unknown) V_UNKNOWN=$((V_UNKNOWN+1)) ;;
        skipped) V_SKIP=$((V_SKIP+1)) ;;
        esac
    elif [ "$scope" = measurement ]; then
        case "$level" in ok) M_PASS=$((M_PASS+1)) ;; fail|warn) M_FAIL=$((M_FAIL+1)) ;; unknown) M_UNKNOWN=$((M_UNKNOWN+1)) ;; esac
    elif [ "$scope" = direct_set ]; then
        case "$level" in ok) D_PASS=$((D_PASS+1)) ;; fail|warn) D_FAIL=$((D_FAIL+1)) ;; unknown) D_UNKNOWN=$((D_UNKNOWN+1)) ;; esac
    fi
}

firewall_snapshot() {
    local directory=$1
    FW_READABLE=0; FW_PORTS=; FW_DNAT=0; FW_TRUNCATED=0; FW_PARTIAL=0
    : >"$directory/firewall.raw"
    if command -v nft >/dev/null 2>&1 && nft -t list ruleset >"$directory/nft.raw" 2>"$directory/nft.err"; then
        FW_READABLE=1; cat "$directory/nft.raw" >>"$directory/firewall.raw"
    elif command -v nft >/dev/null 2>&1; then
        FW_PARTIAL=1
    fi
    if command -v iptables-save >/dev/null 2>&1 && iptables-save -t nat >"$directory/iptables.raw" 2>"$directory/iptables.err"; then
        FW_READABLE=1; cat "$directory/iptables.raw" >>"$directory/firewall.raw"
    elif command -v iptables-save >/dev/null 2>&1; then
        FW_PARTIAL=1
    fi
    [ "$FW_READABLE" = 1 ] || return 1
    FW_PORTS=$({
        grep -E 'dport 53 ' "$directory/firewall.raw" | grep -oE 'redirect to :[0-9]+' | grep -oE '[0-9]+$'
        grep -E -- '--dport 53 ' "$directory/firewall.raw" | grep -oE -- '--to-ports [0-9]+' | grep -oE '[0-9]+$'
    } | grep -vx 53 | uniq_keep_order)
    [ "$(echo "$FW_PORTS" | grep -c .)" -le 3 ] || FW_TRUNCATED=1
    FW_PORTS=$(printf '%s\n' "$FW_PORTS" | head -n 3 | tr '\n' ' ' | sed 's/ *$//')
    grep -Ei 'dport 53.*dnat|--dport 53.*DNAT' "$directory/firewall.raw" >/dev/null && FW_DNAT=1
    return 0
}

dns_capture() {
    local type=$1 host=$2 server=$3 prefix=$4 rc
    nslookup -type="$type" -timeout=1 -retry=1 "$host" "$server" >"$prefix.raw" 2>&1
    rc=$?; printf '%s\n' "$rc" >"$prefix.rc"
    return 0
}

dns_parse() {
    local type=$1 prefix=$2 rc raw
    rc=$(cat "$prefix.rc" 2>/dev/null); raw=$(cat "$prefix.raw" 2>/dev/null)
    DNS_STATUS=unknown; DNS_CODE=DNS_UNPARSEABLE; DNS_VALUES=
    if [ -z "$raw" ]; then DNS_CODE=DNS_NO_RESPONSE; return 0; fi
    if printf '%s' "$raw" | grep -qi 'timed out\|Connection refused\|no servers could be reached'; then
        DNS_CODE=DNS_REQUEST_FAILED; return 0
    fi
    if printf '%s' "$raw" | grep -qi 'SERVFAIL\|REFUSED'; then DNS_STATUS=failed; DNS_CODE=DNS_SERVER_ERROR; return 0; fi
    if printf '%s' "$raw" | grep -qi 'NXDOMAIN'; then DNS_STATUS=failed; DNS_CODE=DNS_NXDOMAIN; return 0; fi
    case "$type" in
    a) DNS_VALUES=$(printf '%s\n' "$raw" | awk '/^Name:/{found=1} found && /^Address/{print $NF}' | ipv4s | uniq_keep_order | tr '\n' ' ' | sed 's/ $//') ;;
    aaaa) DNS_VALUES=$(printf '%s\n' "$raw" | awk '/^Name:/{found=1} found && /^Address/{print $NF}' | grep ':' | uniq_keep_order | tr '\n' ' ' | sed 's/ $//') ;;
    srv) DNS_VALUES=$(printf '%s\n' "$raw" | awk '/service =/{target=$NF;sub(/\.$/,"",target); print $(NF-1) ":" target}' | uniq_keep_order | tr '\n' ' ' | sed 's/ $//') ;;
    esac
    if [ -n "$DNS_VALUES" ]; then DNS_STATUS=ok; DNS_CODE=DNS_ANSWER; return 0; fi
    if [ "${rc:-1}" = 0 ] && printf '%s' "$raw" | grep -q 'Non-authoritative answer\|Authoritative answer\|No answer'; then
        DNS_STATUS=nodata; DNS_CODE=DNS_NODATA
    fi
}

# Parallel batches of four: bounded load and a short timeout per DNS request.
dns_batch() {
    local server=$1 directory=$2 host count=0
    shift 2
    for host in "$@"; do
        (
            dns_capture a "$host" "$server" "$directory/$host.a"
            dns_capture aaaa "$host" "$server" "$directory/$host.aaaa"
        ) &
        count=$((count+1))
        if [ "$count" -ge 4 ]; then wait; count=0; fi
    done
    wait
}

verify_dns_row() {
    local host=$1 server=$2 expected=$3 directory=$4 level
    dns_parse a "$directory/$host.a"
    if [ "$DNS_STATUS" = unknown ]; then
        check_row "A $host @$server" unknown "$DNS_CODE" '无法取得可解析的 DNS 响应；没有证据确认成功' '检查该 DNS 入口是否响应、插件与防火墙链路' router "$expected" ""
    elif [ "$DNS_STATUS" = ok ] && [ "$DNS_VALUES" = "$expected" ]; then
        check_row "A $host @$server" ok DNS_MATCH '实际解析地址与预期一致' '' router "$expected" "$DNS_VALUES"
    else
        check_row "A $host @$server" fail DNS_MISMATCH "实际解析不符合预期（$DNS_CODE）" '重新应用并检查 DNS 副本、缓存、重绑定保护及冲突规则' router "$expected" "$DNS_VALUES"
    fi
    dns_parse aaaa "$directory/$host.aaaa"
    if [ "$DNS_STATUS" = nodata ]; then
        check_row "AAAA $host @$server" ok AAAA_SUPPRESSED '收到无 AAAA 数据的正常响应；该域名走 IPv4' '' router 'NODATA' 'NODATA'
    elif [ "$DNS_STATUS" = ok ]; then
        check_row "AAAA $host @$server" fail IPV6_BYPASS_POSSIBLE '仍返回 IPv6 地址，可能绕过 IPv4 优选' '检查实际 DNS 入口与该域名的 IPv6 规则，不要先全局关闭 IPv6' router 'NODATA' "$DNS_VALUES"
    elif [ "$DNS_STATUS" = failed ] && [ "$DNS_CODE" = DNS_NXDOMAIN ]; then
        check_row "AAAA $host @$server" warn AAAA_NXDOMAIN '该入口对 AAAA 返回 NXDOMAIN，而非正常无数据；需核查' '核查 DNS 过滤；A 正确也不能把异常 AAAA 响应当成正常' router 'NODATA' 'NXDOMAIN'
    else
        check_row "AAAA $host @$server" unknown "$DNS_CODE" '查询没有正常完成，无法确认 IPv6 已抑制' '检查 DNS 入口响应；超时不等于没有 AAAA' router 'NODATA' ""
    fi
}

configuration_signature() {
    {
        for key in enabled autostart optimize_cn optimize_com redirect_cn resolver_port dnsmasq_instance ttl isp cron schedule_type schedule_time schedule_weekday schedule_monthday health_interval passwall_direct passwall_restart override_cdn_hosts fallback_dns redirect_ip redirect_port test_url_cn test_url_com test_offset_cn test_offset_com; do
            printf '%s=%s\n' "$key" "$(cfg "$key" '')"
        done
        for key in $(platform_config_keys); do printf '%s=%s\n' "$key" "$(cfg "$key" '')"; done
        for key in $ALL_GROUPS; do printf '%s=%s:%s\n' "$key" "$(best_ip "$key")" "$(best_domains "$key")"; done
        printf 'switch_mappings=%s\n' "$(pinned_domains switch)"
        cat "$STATE_DIR/resources_switch" 2>/dev/null
        main_rules_now
        uci -q get uhttpd.main.error_page
        grep -F "$CRON_TAG" "$CRON_FILE" 2>/dev/null
        cat /proc/sys/kernel/random/boot_id "$RESOLVER_CONF" "$REDIRECT_CONF" 2>/dev/null
    } | {
        if command -v sha256sum >/dev/null 2>&1; then sha256sum | awk '{print "sha256:" $1}';
        elif command -v md5sum >/dev/null 2>&1; then md5sum | awk '{print "md5:" $1}';
        else cat >/dev/null; echo unavailable; fi
    }
}

verify_resource_row() {
    local g=$1 host=$2 url=$3 ip=$4 work=$5 directory got classification title
    title="测试资源 $(group_label "$g") $host"
    case "$g" in cn|com) title="测试资源 $g" ;;
    switch) case " $SWITCH_GAME_DOMAINS " in *" $host "*) title="增强校验资源 $host" ;; *) title="公开测试资源 $host" ;; esac ;;
    esac
    if [ -z "$ip" ]; then
        directory="$work/resource-$g-$host"
        dns_capture a "$host" "$(cfg fallback_dns 223.5.5.5 | awk '{print $1}')" "$directory"
        dns_parse a "$directory"; ip=$(printf '%s\n' "$DNS_VALUES" | ipv4s | public_only | head -n 1)
    fi
    if [ -z "$ip" ]; then check_row "$title" unknown RESOURCE_DNS_UNKNOWN '没有可用测试地址，资源可用性待确认' '检查上游 DNS 与节点发现结果' measurement; return; fi
    got=$(probe_resource "$ip" "$url" "$(domain_offset "$g" "$host")" 1024 3 "$g")
    set -- $got
    classification=$(probe_classify "${4:-0}" "${1:-000}" "$(to_int "$2")" 1024)
    case "$classification" in
    OK|TIME_LIMIT_WITH_DATA) check_row "$title" ok RESOURCE_AVAILABLE "host=$host ip=$ip HTTP=$1 bytes=$2；该资源可取样，设备实际速度待实机测量" '' measurement ;;
    HTTP_*|RANGE_INVALID|RANGE_IGNORED|INSUFFICIENT_DATA) check_row "$title" warn "$classification" "host=$host ip=$ip HTTP=$1 bytes=$2；该节点资源校验失败" "$(probe_advice "$classification")" measurement ;;
    *) check_row "$title" unknown "$classification" "curl_exit=$4 HTTP=$1 bytes=$2 connect_s=${5:-0} total_s=${6:-0} error=$(head -c 250 "$TMP_DIR/probe-error-$$.txt" 2>/dev/null)" "$(probe_advice "$classification")" measurement ;;
    esac
}

diagnostic_environment() {
    . /usr/share/libubox/jshn.sh
    local work="$TMP_DIR/verification.$$" enabled=0 mode port d missing value g ip host hosts_group url expected hosts="" mappings="" servers server index=0 directory rc got classification sample_count=0 signature_before signature_after resources
    mkdir -p "$work" || return 1
    chmod 700 "$work"
    V_PASS=0; V_FAIL=0; V_WARN=0; V_UNKNOWN=0; V_SKIP=0
    M_PASS=0; M_FAIL=0; M_UNKNOWN=0; D_PASS=0; D_FAIL=0; D_UNKNOWN=0
    is_enabled && enabled=1
    mode=$(com_mode); port=$(cfg resolver_port 5335)
    signature_before=$(configuration_signature)
    json_init
    json_add_string configuration_signature "$signature_before"
    json_add_string plugin_version "$VERSION"; json_add_int checked_at "$(date +%s)"
    json_add_array environment
    check_row 系统 info SYSTEM_INFO "$(. /etc/openwrt_release 2>/dev/null; echo "$DISTRIB_DESCRIPTION $DISTRIB_ARCH")" '' context
    check_row 路由器当前时间 info ROUTER_TIME "$(date '+%Y-%m-%d %H:%M:%S %Z')；使用路由器系统时区" '' context
    missing=""
    for d in dnsmasq curl flock nslookup uci ubus netstat; do command -v "$d" >/dev/null 2>&1 || missing="$missing $d"; done
    if [ -n "$missing" ]; then check_row 依赖 fail DEPENDENCY_MISSING "缺少:$missing" '重新安装必要依赖'; else check_row 依赖 ok DEPENDENCIES_READY '所需命令已安装'; fi
    if value=$( (validate_config) 2>&1); then check_row 配置 ok CONFIG_VALID '配置参数通过校验'; else check_row 配置 fail CONFIG_INVALID "$value" '修正对应配置字段后重新应用'; fi
    value=$(listeners_53)
    if [ "$value" = dnsmasq ]; then check_row '主 DNS 服务' ok DNSMASQ_LISTENING '53 端口由 dnsmasq 监听';
    elif [ -z "$value" ]; then check_row '主 DNS 服务' unknown LISTENER_UNKNOWN '未取得可识别的 UDP 53 监听进程' '检查服务、监听地址和读取进程信息的权限';
    else check_row '主 DNS 服务' warn NONSTANDARD_LISTENER "检测到:$value" '确认客户端实际使用哪个 DNS 服务'; fi
    if firewall_snapshot "$work"; then
        [ "$FW_PARTIAL" = 0 ] || check_row 防火墙读取范围 unknown FIREWALL_PARTIAL_READ '部分防火墙后端读取失败，当前结果覆盖已成功读取的规则' '检查失败后端的工具与权限'
        if [ "$FW_DNAT" = 1 ]; then check_row 'DNS 入口' unknown DNS_DNAT_UNRESOLVED '检测到 DNS DNAT，但没有解析其完整目标，不能断言客户端先经过主 dnsmasq' '检查 DNAT 规则目标与客户端 DNS 设置';
        elif [ -z "$FW_PORTS" ]; then check_row 'DNS 入口' info NO_MATCHED_REDIRECT '已读取规则，未匹配到 53 端口 redirect；这是规则检查，不是客户端抓包证明';
        else check_row 'DNS 入口' info REDIRECT_PORTS "匹配到重定向端口:$FW_PORTS" '下方读回查询会检查这些入口'; fi
        [ "$FW_TRUNCATED" = 0 ] || check_row 'DNS 入口范围' unknown MORE_ENTRIES '发现超过三个入口，本次仅核查前三个' '检查剩余入口与 Xbox 所在 LAN 的匹配规则'
    else
        check_row 'DNS 入口' unknown FIREWALL_UNREADABLE '规则读取失败或工具不可用，不能把未知当成没有重定向' '检查 nft/iptables 工具及权限'
    fi
    if [ "$enabled" = 0 ]; then
        check_row 解析实例 skipped PLUGIN_DISABLED '插件未启用，未执行应用验收' '启用后再检测'
    elif resolver_running; then check_row 解析实例 ok RESOLVER_RUNNING "独立解析进程运行，127.0.0.1:$port";
    else check_row 解析实例 fail RESOLVER_NOT_RUNNING '独立解析进程未运行' '查看系统日志与端口冲突，重新应用'; fi

    # Build the exact domain/address list that is configured, with missing coverage explicitly flagged.
    if [ "$enabled" = 1 ] && [ "$mode" = optimize ]; then
        for host in $COM_DL_DOMAINS; do check_row "覆盖 $host" skipped REDIRECT_ONLY_DOMAIN '旧版游戏下载域名在重定向模式接管，当前保持默认解析'; done
    fi
    if [ "$enabled" = 1 ] && [ "$mode" != off ] && ! cfg_bool override_cdn_hosts 1; then
        for host in $CDN_HOSTS; do check_row "覆盖 $host" skipped FEATURE_DISABLED '备用 CDN 接管已关闭，当前保持默认解析'; done
    fi
    for g in $ALL_GROUPS; do
        expected=""; hosts_group=""
        if [ "$g" = cn ]; then
            if ! cfg_bool optimize_cn 1; then check_row 国内优选 skipped FEATURE_DISABLED '国内优选未开启'; continue; fi
            hosts_group=$CN_DOMAINS; ip=$(best_ip cn)
        elif [ "$g" = com ] && [ "$mode" = off ]; then check_row 国际处理 skipped FEATURE_DISABLED '国际域名不处理'; continue
        elif [ "$g" = com ] && [ "$mode" = redirect ]; then
            hosts_group="$COM_DOMAINS $COM_DL_DOMAINS"; cfg_bool override_cdn_hosts 1 && hosts_group="$hosts_group $CDN_HOSTS"; ip=$(redirect_ip)
        elif [ "$g" = com ]; then
            hosts_group=$COM_DOMAINS; cfg_bool override_cdn_hosts 1 && hosts_group="$hosts_group $CDN_HOSTS"; ip=$(best_ip com)
        else
            if ! cfg_bool "optimize_$g" 0; then check_row "$(group_label "$g")" skipped FEATURE_DISABLED '该平台优选已关闭'; continue; fi
            hosts_group=$(configured_domains "$g"); ip=$(best_ip "$g")
        fi
        [ "$enabled" = 1 ] || continue
        if [ -z "$ip" ]; then check_row "节点 $g" unknown NO_SPEED_RESULT '尚无可用的节点结果，不能宣称固定 IP 已生效' '先完成有效测速'; continue; fi
        for host in $hosts_group; do
            case "$g" in store|ps|xbox_old|switch)
                if [ "$g" = ps ] && ! url=$(domain_url "$g" "$host"); then
                    check_row "覆盖 $host" skipped RESOURCE_NOT_CONFIGURED '该域名的独立校验资源待配置；当前使用上游解析' '添加该域名的下载资源后重新测速'
                    continue
                fi
                if ! pinned_domains "$g" | grep -qxF "$host"; then
                    check_row "覆盖 $host" warn DOMAIN_NOT_OPTIMIZED '该域名尚待通过节点或增强资源校验；当前使用上游解析' '检查测速与校验链接并重新运行该平台测速'
                    continue
                fi ;;
            esac
            if [ "$g" = cn ] || { [ "$mode" = optimize ] && echo "$COM_DOMAINS" | grep -qwF "$host"; }; then
                if ! best_domains "$g" | grep -qwF "$host"; then
                    check_row "覆盖 $host" warn DOMAIN_NOT_OPTIMIZED '该域名兼容性校验失败，当前走默认上游' '检查该下载域名与选中节点的资源兼容性'
                    continue
                fi
            fi
            hosts="$hosts $host"; mappings="$mappings $host=$ip"
        done
    done
    if [ "$enabled" = 1 ] && [ -n "$hosts" ]; then
        servers="127.0.0.1:$port 127.0.0.1:53"
        for d in $FW_PORTS; do servers="$servers 127.0.0.1:$d"; done
        for server in $(echo "$servers" | tr ' ' '\n' | uniq_keep_order); do
            index=$((index+1)); directory="$work/dns-$index"; mkdir -p "$directory"
            # Each endpoint's primary probe gates timeouts so one dead endpoint does not cause dozens of waits.
            host=$(echo "$hosts" | awk '{print $1}')
            dns_capture a "$host" "$server" "$directory/gate"
            dns_parse a "$directory/gate"
            if [ "$DNS_STATUS" = unknown ]; then
                check_row "DNS 验收 @$server" unknown "$DNS_CODE" '入口查询未正常完成，跳过其批量读回；不是验证通过' '检查监听、拦截和服务器状态'
                continue
            fi
            dns_batch "$server" "$directory" $hosts
            for d in $mappings; do host=${d%=*}; expected=${d#*=}; verify_dns_row "$host" "$server" "$expected" "$directory"; done
            if [ "$mode" != off ] && { [ "$mode" = redirect ] || [ -n "$(best_ip com)" ]; }; then
                for host in $SRV_NAMES; do
                    dns_capture srv "$host" "$server" "$directory/$host.srv"; dns_parse srv "$directory/$host.srv"
                    if [ "$DNS_STATUS" = ok ] && [ "$DNS_VALUES" = '80:assets1.xboxlive.com' ]; then
                        check_row "SRV $host @$server" ok SRV_MATCH '备用服务指向受控下载域名和 80 端口' '' router '80:assets1.xboxlive.com' "$DNS_VALUES"
                    elif [ "$DNS_STATUS" = unknown ]; then
                        check_row "SRV $host @$server" unknown "$DNS_CODE" '无法取得可识别 SRV 响应' '检查 DNS 服务及 SRV 接管规则'
                    else check_row "SRV $host @$server" fail SRV_MISMATCH '备用服务记录不符合预期，可能绕过加速' '检查 SRV 转发与 DNS 入口' router '80:assets1.xboxlive.com' "$DNS_VALUES"; fi
                done
            fi
        done
    fi
    if cfg_bool redirect_cn 0; then
        value=$(redirect_blocker)
        if [ -n "$value" ]; then check_row 'HTTP 302' fail REDIRECT_UNAVAILABLE "$value" '检查独立服务端口、专用地址和防火墙工具';
        elif [ "$enabled" = 0 ]; then check_row 'HTTP 302' skipped PLUGIN_DISABLED '未启用，不做运行验收';
        else
            if ! redirect_running; then check_row 重定向服务 fail REDIRECT_NOT_RUNNING '独立重定向进程缺失' '检查 procd 和启动日志';
            elif redirect_ready; then check_row 重定向服务 ok REDIRECT_RUNNING "独立进程的 127.0.0.1 与 $(lan_ip):$(redirect_port) 监听已确认";
            else check_row 重定向服务 fail REDIRECT_LISTENER_MISSING '独立进程的 LAN 或回环监听缺失' '检查端口绑定错误与 LAN 地址'; fi
            if redirect_firewall_ready; then check_row 重定向转发 ok REDIRECT_RULES_READY "LAN 的 $(redirect_ip):80 与路由器自测入口均有精确转发规则";
            else check_row 重定向转发 fail REDIRECT_RULES_MISSING '转发规则与当前配置不一致' '重新应用或检查防火墙重载'; fi
            got=$(curl -sS --noproxy '*' -o /dev/null -m 3 -H 'Host: assets1.xboxlive.com' -w '%{http_code} %{redirect_url}' "http://$(redirect_ip)/xbox-accel/selftest?probe=1" 2>"$work/redirect.err"); rc=$?
            if [ "$rc" = 0 ] && [ "$got" = '302 http://assets1.xboxlive.cn/xbox-accel/selftest?probe=1' ]; then check_row 'HTTP 302' ok REDIRECT_VERIFIED '专用地址 80 入口、独立服务及路径查询参数已验证；LAN 客户端路径需实测';
            elif [ "$rc" != 0 ]; then check_row 'HTTP 302' unknown REDIRECT_REQUEST_FAILED "curl_exit=$rc error=$(head -c 250 "$work/redirect.err")" '检查独立 HTTP 服务和专用地址转发';
            else check_row 'HTTP 302' fail REDIRECT_MISMATCH "实际响应:$got" '检查独立 Web 目录中的重定向脚本'; fi
        fi
    else check_row 'HTTP 302' skipped FEATURE_DISABLED '未开启'; fi

    # Resource availability is a separate layer, not proof of client routing or throughput.
    resources=$(groups_active)
    [ "$mode" = redirect ] && resources="$resources cn"
    for g in $(echo "$resources" | tr ' ' '\n' | uniq_keep_order); do
        if ! prepare_group_resource "$g"; then
            check_row "测速链接 $(group_label "$g")" unknown "$GROUP_RESOURCE_CODE" "$GROUP_RESOURCE_MESSAGE" '检查商店链接接口、路由器时钟与自定义资源配置' measurement
            continue
        fi
        url=$(group_url "$g"); ip=$(best_ip "$g")
        case "$g" in cn|com) hosts_group=$(url_host "$url") ;; *) hosts_group=$(configured_domains "$g") ;; esac
        for host in $hosts_group; do
            if ! value=$(domain_url "$g" "$host" "$url"); then
                if [ "$g" = switch ]; then
                    case " $SWITCH_GAME_DOMAINS " in *" $host "*)
                        check_row "游戏下载资源 $host" skipped OPTIONAL_GAME_RESOURCE '默认采用公开测速选中的节点映射；实际游戏资源增强校验可选' '可选填写该域名的实际游戏下载链接并保存应用' measurement ;;
                    esac
                fi
                continue
            fi
            verify_resource_row "$g" "$host" "$value" "$ip" "$work"
        done
    done

    # Check exact configured schedules, not merely that a line has our comment.
    if [ "$enabled" = 1 ]; then
        expected="$(schedule_cron) /usr/bin/xbox-accel start_test all scheduled >/dev/null 2>&1 $CRON_TAG"
        if ! grep -qxF "$expected" "$CRON_FILE" 2>/dev/null; then check_row 定时完整测速 fail CRON_MISMATCH '实际 cron 条目缺失或与当前配置不同' '重新应用；不要以有任意一条注释作为成功';
        elif ! pidof crond >/dev/null 2>&1; then check_row 定时完整测速 fail CRON_STOPPED 'cron 进程未运行' '启用 cron 服务';
        else check_row 定时完整测速 ok CRON_CONFIG_VERIFIED "$(schedule_label)；仅验证配置，实际执行历史看任务摘要"; fi
        d=$(to_int "$(cfg health_interval 10)")
        if [ "$d" = 0 ]; then check_row 健康检查计划 skipped FEATURE_DISABLED '健康检查计划关闭';
        else
            expected="*/$d * * * * /usr/bin/xbox-accel health >/dev/null 2>&1 $CRON_TAG"
            grep -qxF "$expected" "$CRON_FILE" 2>/dev/null && check_row 健康检查计划 ok CRON_CONFIG_VERIFIED "$(health_label)" || check_row 健康检查计划 fail CRON_MISMATCH '健康检查条目与设置不同' '重新应用'
        fi
    else check_row 定时任务 skipped PLUGIN_DISABLED '插件未启用'; fi
    case "$(passwall_state)" in
    running)
        ip=""; for g in $(groups_active); do ip="$ip $(best_ip "$g")"; done; sample_count=$(echo "$ip" | wc -w)
        if [ "$enabled" = 0 ]; then check_row Passwall集合 skipped PLUGIN_DISABLED '插件已停用，集合成员待启用后验收' '' direct_set;
        elif [ "$sample_count" = 0 ]; then check_row Passwall集合 skipped NO_SELECTED_NODE '当前尚无选中节点，集合成员待测速后验收' '' direct_set;
        elif ! cfg_bool passwall_direct 1; then check_row Passwall集合 skipped FEATURE_DISABLED '自动加入直连集合未开启' '自行确认下载域名直连' direct_set;
        else
            missing=""; for d in $ip; do in_passwall_direct "$d" || missing="$missing $d"; done
            if [ -n "$missing" ]; then check_row Passwall集合 fail DIRECT_SET_MISSING "集合里缺少节点:$missing" '检查写入失败和直连列表是否启用' direct_set;
            else check_row Passwall集合 ok DIRECT_SET_MEMBERSHIP '选中 IP 在集合中；实际流量路径待实机观察' '' direct_set; fi
        fi
        check_row 实际下载路由 unknown TRAFFIC_NOT_OBSERVED '未抓取或观测 Xbox 实际连接，不能证明流量没有经过代理' '实机观察目标 IP、规则命中及代理流量；集合存在不等于规则被使用' traffic
        ;;
    passwall2) check_row Passwall适配 unknown PASSWALL2_UNSUPPORTED '没有适配 Passwall2 直连集合' '自行核查其分流规则' traffic ;;
    stopped) check_row Passwall skipped SERVICE_STOPPED '已安装但未运行' '' traffic ;;
    *) check_row Passwall skipped NOT_INSTALLED '未检测到 Passwall' '' traffic ;;
    esac
    signature_after=$(configuration_signature)
    if [ "$signature_before" = unavailable ]; then check_row 配置签名 unknown HASH_TOOL_UNAVAILABLE '无法记录配置签名' '检查 sha256sum 或 md5sum 工具'; fi
    if [ "$signature_before" != "$signature_after" ]; then
        check_row 验收配置一致性 unknown CONFIG_CHANGED_DURING_CHECK '验收期间配置或节点发生变化，不能把混合结果视为当前配置成功' '保存配置后重新验收，不要在任务期间修改设置'
    fi
    check_row Xbox实际效果 unknown XBOX_NOT_OBSERVED '未连接 Xbox 的实际下载观测；路由器检测全通过也不等于提速' '在 Xbox 下载同一游戏，对比前后稳定速度与实际目标节点' xbox
    check_row 平台设备实际效果 unknown CLIENTS_NOT_OBSERVED '微软商店、PlayStation 与 Switch 的实际下载节点及稳定速度待设备观测' '在对应设备下载同一资源，对比节点与稳定下载速度' clients
    json_close_array
    json_add_object summary
    if [ "$V_FAIL" -gt 0 ]; then value=failed;
    elif [ "$enabled" = 0 ]; then value=disabled;
    elif [ "$V_UNKNOWN" -gt 0 ] || [ "$V_WARN" -gt 0 ]; then value=partial;
    elif [ -z "$hosts" ]; then value=unknown;
    else value=verified; fi
    json_add_string router "$value"; json_add_string traffic unknown; json_add_string xbox unknown; json_add_string clients unknown
    if [ "$M_FAIL" -gt 0 ]; then value=failed; elif [ "$M_UNKNOWN" -gt 0 ]; then value=unknown; elif [ "$M_PASS" -gt 0 ]; then value=verified; else value=skipped; fi
    json_add_string measurement "$value"
    if [ "$D_FAIL" -gt 0 ]; then value=failed; elif [ "$D_UNKNOWN" -gt 0 ]; then value=unknown; elif [ "$D_PASS" -gt 0 ]; then value=verified; else value=skipped; fi
    json_add_string direct_set "$value"
    json_add_int passed "$V_PASS"; json_add_int failed "$V_FAIL"; json_add_int warnings "$V_WARN"
    json_add_int unknown "$V_UNKNOWN"; json_add_int skipped "$V_SKIP"
    json_close_object
    json_dump
    rm -rf "$work"
}
