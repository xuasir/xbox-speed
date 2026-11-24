#!/bin/sh
# Application/verification orchestration. Task success is not Xbox performance success.

application_report_document() {
    if [ -s "$TMP_DIR/last-application.json" ]; then cat "$TMP_DIR/last-application.json";
    elif [ -s "$STATE_DIR/last-application.json" ]; then cat "$STATE_DIR/last-application.json";
    else printf '%s\n' '{"status":"none","code":"NO_REPORT"}'; fi
}

append_application_summary() {
    local old="${JSON_PREFIX:-}" document checked signature status router measurement direct_set traffic clients passed failed unknown warnings stale=1
    if [ ! -s "$TMP_DIR/last-application.json" ] && [ -s "$STATE_DIR/last-application.json" ]; then
        mkdir -p "$TMP_DIR"
        cp "$STATE_DIR/last-application.json" "$TMP_DIR/last-application.json" 2>/dev/null || true
    fi
    document=$(application_report_document)
    . /usr/share/libubox/jshn.sh
    json_set_namespace XA_APPLICATION_META_
    if jshn -r "$document" >/dev/null 2>&1 && json_load "$document" 2>/dev/null; then
        json_get_var checked checked_at; json_get_var signature configuration_signature
        if json_select summary >/dev/null 2>&1; then
            json_get_var status router; json_get_var passed passed; json_get_var failed failed
            json_get_var unknown unknown; json_get_var warnings warnings
            json_get_var measurement measurement; json_get_var direct_set direct_set
            json_get_var traffic traffic; json_get_var clients clients
            json_select ..
        fi
    fi
    router=${status:-none}
    if [ "$status" = verified ]; then
        if [ "$measurement" = failed ] || [ "$direct_set" = failed ]; then status=failed;
        elif [ "$measurement" = unknown ] || [ "$direct_set" = unknown ]; then status=partial; fi
    fi
    if [ -n "$signature" ] && [ "$signature" = "$(configuration_signature)" ] && [ $(($(date +%s) - $(to_int "$checked"))) -ge 0 ] && [ $(($(date +%s) - $(to_int "$checked"))) -lt 60 ]; then stale=0; fi
    json_set_namespace "$old"
    json_add_object last_application
    json_add_string status "${status:-none}"; json_add_int checked_at "$(to_int "$checked")"
    json_add_string router "$router"; json_add_string measurement "${measurement:-skipped}"; json_add_string direct_set "${direct_set:-skipped}"
    json_add_string traffic "${traffic:-unknown}"; json_add_string clients "${clients:-unknown}"
    json_add_boolean stale "$stale"; json_add_boolean available "$([ -s "$TMP_DIR/last-application.json" ] && echo 1 || echo 0)"
    json_add_boolean same_configuration "$( [ -n "$signature" ] && [ "$signature" = "$(configuration_signature)" ] && echo 1 || echo 0 )"
    json_set_namespace XA_APPLICATION_META_
    if json_select summary >/dev/null 2>&1 && json_select groups >/dev/null 2>&1; then
        json_set_namespace "$old"
        json_add_object groups; json_copy_current XA_APPLICATION_META_ "$old"; json_close_object
    fi
    json_set_namespace "$old"
    json_add_int passed "$(to_int "$passed")"; json_add_int failed "$(to_int "$failed")"
    json_add_int unknown "$(to_int "$unknown")"; json_add_int warnings "$(to_int "$warnings")"
    json_add_string file "$TMP_DIR/last-application.json"
    json_add_string limitation '配置验收对应检测时刻；设备连接实时更新，速度效果需同资源对比'
    json_close_object
}

record_application_report() {
    local report=$1 temporary="$TMP_DIR/last-application.json.new.$$"
    mkdir -p "$TMP_DIR" "$STATE_DIR"
    printf '%s\n' "$report" >"$temporary" && mv "$temporary" "$TMP_DIR/last-application.json" || { TASK_PERSIST_WARNING=1; task_event ERROR APPLICATION_REPORT_WRITE_FAILED '验收执行完成但报告无法保存'; return 1; }
    temporary="$STATE_DIR/last-application.json.new.$$"
    cp "$TMP_DIR/last-application.json" "$temporary" && mv "$temporary" "$STATE_DIR/last-application.json" || {
        TASK_PERSIST_WARNING=1
        task_event WARN APPLICATION_REPORT_NOT_PERSISTED '验收报告只保存在内存；重启可能丢失'
        return 0
    }
}

application_from_report() {
    local report=$1 old="${JSON_PREFIX:-}" status measurement direct_set keys key level scope name detail hint actual_code found=0
    . /usr/share/libubox/jshn.sh
    json_set_namespace XA_APP_READ_
    if ! jshn -r "$report" >/dev/null 2>&1 || ! json_load "$report" 2>/dev/null; then
        json_set_namespace "$old"
        TASK_APPLY_STATUS=unknown; TASK_APPLY_CODE=INVALID_VERIFICATION_REPORT
        TASK_APPLY_MESSAGE='未取得完整验收报告，不能确认成功'
        return 2
    fi
    json_select summary; json_get_var status router; json_get_var measurement measurement; json_get_var direct_set direct_set; json_select ..
    if [ "$status" = verified ]; then
        if [ "$measurement" = failed ] || [ "$direct_set" = failed ]; then status=failed;
        elif [ "$measurement" = unknown ] || [ "$direct_set" = unknown ]; then status=partial; fi
    fi
    TASK_APPLY_STATUS=$status
    case "$status" in
    verified) TASK_APPLY_CODE=ROUTER_VERIFIED; TASK_APPLY_MESSAGE='配置和 DNS 验收通过；设备下载证据见总览与验收详情' ;;
    disabled) TASK_APPLY_STATUS=skipped; TASK_APPLY_CODE=DISABLED; TASK_APPLY_MESSAGE='插件未启用，未验证下载链路' ;;
    failed) TASK_APPLY_CODE=ROUTER_FAILED; TASK_APPLY_MESSAGE='路由器验收失败，查看失败项' ;;
    *) TASK_APPLY_STATUS=partial; TASK_APPLY_CODE=ROUTER_NOT_FULLY_VERIFIED; TASK_APPLY_MESSAGE='存在警告、未覆盖或未知项，不能宣称完全成功' ;;
    esac
    if [ "$status" != verified ] && [ "$status" != disabled ]; then
        json_select environment; json_get_keys keys
        for key in $keys; do
            json_select "$key"; json_get_var level level; json_get_var scope scope
            if [ "$scope" = router ] || [ "$scope" = measurement ] || [ "$scope" = direct_set ]; then
                if [ "$status" != failed ] || [ "$level" = fail ] || { [ "$scope" = measurement ] && [ "$level" = warn ]; }; then
                    case "$level" in fail|unknown|warn)
                        json_get_var name name; json_get_var detail detail; json_get_var hint remediation; json_get_var actual_code code
                        TASK_APPLY_CODE=${actual_code:-$TASK_APPLY_CODE}
                        TASK_APPLY_MESSAGE="$name: $detail；$hint"; found=1 ;;
                    esac
                fi
            fi
            json_select ..
            [ "$found" = 0 ] || break
        done
    fi
    json_set_namespace "$old"
    case "$TASK_APPLY_STATUS" in verified|skipped) return 0 ;; failed) return 1 ;; *) return 2 ;; esac
}

verify_router() {
    local report rc
    task_stage verification
    report=$(diagnostic_environment); rc=$?
    if [ "$rc" != 0 ] || [ -z "$report" ]; then
        TASK_APPLY_STATUS=unknown; TASK_APPLY_CODE=VERIFICATION_EXECUTION_FAILED
        TASK_APPLY_MESSAGE='验收未正常完成，不能确认成功'
        task_event ERROR "$TASK_APPLY_CODE" "$TASK_APPLY_MESSAGE"
        return 2
    fi
    record_application_report "$report" || true
    application_from_report "$report"; rc=$?
    task_event INFO "$TASK_APPLY_CODE" "$TASK_APPLY_MESSAGE"
    return "$rc"
}

verify_disabled() {
    local previous_instance=$1 previous_rules=$2 report remaining="" rule old="${JSON_PREFIX:-}"
    . /usr/share/libubox/jshn.sh
    V_PASS=0; V_FAIL=0; V_WARN=0; V_UNKNOWN=0; V_SKIP=0
    json_set_namespace XA_DISABLE_
    json_init; json_add_string plugin_version "$VERSION"; json_add_int checked_at "$(date +%s)"
    json_add_array environment
    if resolver_running; then check_row 停用解析实例 fail RESOLVER_STILL_RUNNING '解析实例仍存在' '检查 procd 停止错误';
    else check_row 停用解析实例 ok RESOLVER_STOPPED '插件解析进程已停止'; fi
    if [ -n "$previous_rules" ]; then
        if ! remaining=$(uci -q get "dhcp.$previous_instance.server"); then
            if uci -q get "dhcp.$previous_instance" >/dev/null; then remaining="";
            else check_row 撤销上游规则 unknown PREVIOUS_INSTANCE_MISSING '原实例已不存在，无法确认运行中旧配置已撤销' '检查 dnsmasq 实际进程与配置'; remaining=UNREADABLE; fi
        fi
        if [ "$remaining" != UNREADABLE ]; then
            rule=""
            for item in $previous_rules; do echo "$remaining" | tr ' ' '\n' | grep -qxF "$item" && rule="$rule $item"; done
            if [ -n "$rule" ]; then check_row 撤销上游规则 fail RULES_REMAIN "仍存在:$rule" '检查 UCI 写入/重载失败';
            else check_row 撤销上游规则 ok RULES_REMOVED '原来记录的规则已撤销'; fi
        fi
    else check_row 撤销上游规则 skipped NO_OWNED_RULES '没有记录的规则需要撤销'; fi
    if [ "$(uci -q get uhttpd.main.error_page)" = "$CGI_URL" ]; then check_row 恢复HTTP fail REDIRECT_HANDLER_REMAINS 'error_page 仍指向插件' '检查 Web 配置回滚';
    else check_row 恢复HTTP ok REDIRECT_HANDLER_REMOVED 'Web 配置不再指向插件重定向入口'; fi
    if redirect_running; then check_row 停用重定向服务 fail REDIRECT_STILL_RUNNING '独立重定向进程仍存在' '检查 procd 停止错误';
    else check_row 停用重定向服务 ok REDIRECT_STOPPED '独立重定向进程已停止'; fi
    if redirect_firewall_present || [ -n "$(uci -q get firewall.xbox_accel_redirect)" ]; then
        check_row 撤销重定向转发 fail REDIRECT_RULES_REMAIN '专用转发规则或防火墙重载入口仍存在' '重新停用并检查清理错误';
    else check_row 撤销重定向转发 ok REDIRECT_RULES_REMOVED '专用转发规则与重载入口已清理'; fi
    if grep -qF "$CRON_TAG" "$CRON_FILE" 2>/dev/null; then check_row 移除计划 fail CRON_REMAINS '插件计划仍存在' '检查 cron 配置写入权限';
    else check_row 移除计划 ok CRON_REMOVED '插件计划已移除'; fi
    if passwall_has_stale_rules "$previous_rules
$(pending_cleanup_rules)"; then
        check_row 撤销DNS副本 fail DNS_COPY_RULES_REMAIN 'Passwall 的 DNS 副本仍保留已撤销的转发规则' '检查 Passwall 重载日志并重新应用';
    else check_row 撤销DNS副本 ok DNS_COPY_RULES_REMOVED '已识别的 Passwall DNS 副本已清理转发规则'; fi
    check_row 下载速度效果 unknown PERFORMANCE_COMPARISON_PENDING '加速已关闭，下载速度可由设备使用同一资源对比' '' xbox
    json_close_array; json_add_object summary
    if [ "$V_FAIL" -gt 0 ]; then json_add_string router failed;
    elif [ "$V_UNKNOWN" -gt 0 ]; then json_add_string router partial;
    else json_add_string router verified; fi
    json_add_string traffic unknown; json_add_string xbox unknown
    json_close_object; report=$(json_dump)
    json_set_namespace "$old"
    record_application_report "$report" || true
    application_from_report "$report"; local rc=$?
    if [ "$rc" = 0 ]; then TASK_APPLY_CODE=DISABLE_VERIFIED; TASK_APPLY_MESSAGE='停用清理验收通过：解析实例、转发规则、HTTP 入口和计划已撤销'; fi
    return "$rc"
}

apply_and_verify() {
    local previous_instance previous_rules rc action=${1:-reload}
    previous_instance=$(cat "$STATE_DIR/main_instance" 2>/dev/null); previous_instance=${previous_instance:-$DNS_INSTANCE}
    previous_rules=$(cat "$STATE_DIR/main_rules" 2>/dev/null)
    task_stage application
    if is_enabled; then
        local validation_error
        if ! validation_error=$( (validate_config) 2>&1); then
            TASK_APPLY_STATUS=failed; TASK_APPLY_CODE=CONFIG_INVALID; TASK_APPLY_MESSAGE="$validation_error"
            task_event ERROR CONFIG_INVALID "$validation_error"
            return 1
        fi
    fi
    if ! "$INIT" "$action" 7>&- 8>&- 9>&-; then
        TASK_APPLY_STATUS=failed
        if [ "$action" = restart ]; then
            TASK_APPLY_CODE=RESTART_FAILED
            TASK_APPLY_MESSAGE='服务重启失败；查看系统及任务日志'
        else
            TASK_APPLY_CODE=RELOAD_FAILED
            TASK_APPLY_MESSAGE='重载命令失败，未开始链路验收；查看系统及任务日志'
        fi
        task_event ERROR "$TASK_APPLY_CODE" "$TASK_APPLY_MESSAGE"
        return 1
    fi
    if is_enabled; then
        # procd updates asynchronously; wait only for the local resolver, not arbitrary network traffic.
        for attempt in 1 2 3 4 5 6 7 8; do
            if resolver_running && [ "$(listeners_53)" = dnsmasq ]; then break; fi
            sleep 1
        done
        verify_router
    else
        verify_disabled "$previous_instance" "$previous_rules"
    fi
}

cmd_application_task() {
    local kind=$1 rc
    TASK_KIND=$kind
    lock; task_begin
    [ "$TASK_MEMORY_READY" = 1 ] || die '无法记录任务，已停止'
    case "$kind" in
    verify) verify_router; rc=$? ;;
    restart) apply_and_verify restart; rc=$? ;;
    *) apply_and_verify; rc=$? ;;
    esac
    case "$rc" in
    0) TASK_STATUS=success; [ "$TASK_APPLY_STATUS" = skipped ] && TASK_STATUS=skipped
       TASK_CODE="$TASK_APPLY_CODE"; TASK_MESSAGE="$TASK_APPLY_MESSAGE" ;;
    1) TASK_STATUS=failed; TASK_CODE="$TASK_APPLY_CODE"; TASK_MESSAGE="$TASK_APPLY_MESSAGE" ;;
    *) TASK_STATUS=partial; TASK_CODE=ROUTER_NOT_FULLY_VERIFIED; TASK_MESSAGE="$TASK_APPLY_MESSAGE" ;;
    esac
    TASK_STAGE=finished
    return "$rc"
}
