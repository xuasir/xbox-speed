#!/bin/sh
# Fast GUI preflight: no download probes or many DNS waits. Complete checks run as tasks.
quick_environment() {
    . /usr/share/libubox/jshn.sh
    local work="$TMP_DIR/preflight.$$" value missing key
    mkdir -p "$work" || return 1
    V_PASS=0; V_FAIL=0; V_WARN=0; V_UNKNOWN=0; V_SKIP=0
    json_init; json_add_int checked_at "$(date +%s)"; json_add_string mode preflight
    json_add_array environment
    check_row 系统 info SYSTEM_INFO "$(. /etc/openwrt_release 2>/dev/null; echo "$DISTRIB_DESCRIPTION $DISTRIB_ARCH")" '' context
    check_row 路由器时间 info ROUTER_TIME "$(date '+%Y-%m-%d %H:%M:%S %Z')" '' context
    missing=""
    for key in dnsmasq curl flock nslookup uci ubus netstat; do command -v "$key" >/dev/null 2>&1 || missing="$missing $key"; done
    if [ -n "$missing" ]; then check_row 依赖 fail DEPENDENCY_MISSING "缺少:$missing" '重新安装依赖'; else check_row 依赖 ok DEPENDENCIES_READY '所需命令已安装'; fi
    if value=$( (validate_config) 2>&1); then check_row 配置 ok CONFIG_VALID '参数格式有效，不等于已生效'; else check_row 配置 fail CONFIG_INVALID "$value" '修改对应字段后保存并应用'; fi
    if ! is_enabled; then check_row 解析服务 skipped PLUGIN_DISABLED '插件未启用';
    elif resolver_running; then check_row 解析服务 ok RESOLVER_RUNNING '独立解析进程存在，还需要 DNS 读回验证';
    else check_row 解析服务 fail RESOLVER_NOT_RUNNING '独立解析进程不存在' '检查启动错误和端口冲突'; fi
    value=$(listeners_53)
    if [ "$value" = dnsmasq ]; then check_row 主DNS ok DNSMASQ_LISTENING 'UDP 53 由 dnsmasq 监听；尚未证明 Xbox 实际请求到此';
    elif [ -n "$value" ]; then check_row 主DNS warn NONSTANDARD_LISTENER "监听程序:$value" '确认客户端 DNS 入口';
    else check_row 主DNS unknown LISTENER_UNKNOWN '无法识别 UDP 53 监听程序' '检查服务和读取权限'; fi
    if firewall_snapshot "$work"; then
        [ "$FW_PARTIAL" = 0 ] || check_row 防火墙读取范围 unknown FIREWALL_PARTIAL_READ '部分防火墙后端读取失败，当前仅覆盖成功读取的规则' '检查工具和权限'
        if [ "$FW_DNAT" = 1 ]; then check_row DNS入口 unknown DNS_DNAT_UNRESOLVED '存在 DNAT，未解析完整目标' '检查规则的目标地址/端口';
        elif [ -n "$FW_PORTS" ]; then check_row DNS入口 unknown REDIRECT_NOT_VERIFIED "重定向端口:$FW_PORTS，初查不确认其中域名规则已生效" '运行一键完整验收';
        else check_row DNS入口 info NO_MATCHED_REDIRECT '已读规则，未匹配到 redirect；不能代替客户端流量观测'; fi
        [ "$FW_TRUNCATED" = 0 ] || check_row DNS入口范围 unknown MORE_ENTRIES '入口数量超过本次检查范围' '完整核查 Xbox 所在 LAN 的规则'
    else check_row DNS入口 unknown FIREWALL_UNREADABLE '无法读取规则，不能当成没有 DNS 拦截' '检查工具及权限'; fi
    value=$(uci -q get "dhcp.$DNS_INSTANCE.server" | tr ' ' '\n' | grep -v '^/' | tr '\n' ' ')
    check_row 原有上游 info UPSTREAM_CONFIGURATION "普通上游:${value:-resolvfile或分流设置}；本插件没有替换全局上游" '' context
    if is_enabled; then
        value="$(schedule_cron) /usr/bin/xbox-accel start_test all scheduled >/dev/null 2>&1 $CRON_TAG"
        if ! grep -qxF "$value" "$CRON_FILE" 2>/dev/null; then check_row 定时完整测速 fail CRON_MISMATCH '实际计划不匹配当前配置' '重新应用';
        elif ! pidof crond >/dev/null 2>&1; then check_row 定时完整测速 fail CRON_STOPPED 'cron 进程不存在' '启动 cron';
        else check_row 定时完整测速 ok CRON_CONFIG_VERIFIED "$(schedule_label)；健康检查$(health_label)；执行是否成功看最近测速摘要"; fi
    else check_row 定时任务 skipped PLUGIN_DISABLED '插件未启用'; fi
    check_row 完整链路验收 unknown NOT_RUN_IN_PREFLIGHT '初查不下载资源，也不逐域名验证 DNS/SRV/AAAA/302' '点击“一键完整验收”，后台完成后查看报告'
    check_row Xbox实际效果 unknown XBOX_NOT_OBSERVED '未观测 Xbox 实际下载' '实机比较稳定下载速度及目标节点' xbox
    json_close_array; json_add_object summary
    [ "$V_FAIL" -eq 0 ] && json_add_string router unknown || json_add_string router failed
    json_add_string xbox unknown; json_close_object; json_dump
    rm -rf "$work"
}
