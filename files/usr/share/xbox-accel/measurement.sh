#!/bin/sh
# 手动测速保存独立候选；采用结果时才提交运行节点与功能配置。

measurement_groups() {
    local requested=${1:-all} group result=""
    [ "$requested" = all ] && { printf '%s\n' "$ALL_GROUPS"; return; }
    case "$requested" in ''|,*|*,|*,,*|*[!a-z_,]*) return 1 ;; esac
    for group in $(printf '%s' "$requested" | tr ',' ' '); do
        group_valid "$group" || return 1
        case " $result " in *" $group "*) ;; *) result="$result $group" ;; esac
    done
    [ -n "$result" ] || return 1
    printf '%s\n' "${result# }"
}

validate_measurement_config() {
    local groups=$1 spec key min max value group url host
    command -v flock >/dev/null || die '测速需要 flock'
    for spec in 'quick_size_mb:1:128:4' 'full_size_mb:1:256:64' 'quick_time:1:30:4' 'full_time:1:60:10' 'top_n:1:10:3' 'max_candidates:1:100:60' 'switch_threshold:0:100:15'; do
        key=${spec%%:*}; spec=${spec#*:}; min=${spec%%:*}; spec=${spec#*:}; max=${spec%%:*}
        value=$(cfg "$key" "${spec#*:}")
        case "$value" in ''|*[!0-9]*) die "$key 必须是整数" ;; esac
        [ "$value" -ge "$min" ] && [ "$value" -le "$max" ] || die "$key 超出范围 $min..$max"
    done
    for group in $groups; do
        group_valid "$group" || die '测速平台无效'
        case "$group" in
        cn|com)
            url=$(group_url "$group"); host=$(url_host "$url")
            [ "$(url_scheme "$url")" = http ] && [ "$host" = "assets1.xboxlive.$group" ] && platform_url_valid "$group" "$url" || die "测速链接须使用 http://assets1.xboxlive.$group/ 路径"
            ;;
        *) validate_platform_group "$group" ;;
        esac
    done
}

measurement_signature() {
    local group=$1 value
    value=$(
        printf '%s\n' "$group"
        if [ "$group" = store ]; then
            printf '%s\n' "$(cfg test_url_store '')" "$(cfg store_test_app store)"
        else
            printf '%s\n' "$(group_url "$group")"
        fi
        printf '%s\n' "$(cfg "test_offset_$group" 0)" "$(cfg "verification_urls_$group" '')"
    )
    resource_hash "$value"
}

save_measurement() (
    local group=$1 ip=$2 speed=$3 domains=$4 candidates=$5 valid=$6 partial=${7:-0} temporary signature
    signature=$(measurement_signature "$group") || return 1
    mkdir -p "$STATE_DIR/measurements" || return 1
    temporary="$STATE_DIR/measurements/$group.json.new.$$"
    . /usr/share/libubox/jshn.sh
    json_init
    json_add_int schema 1; json_add_string id "$TASK_ID"; json_add_string group "$group"
    json_add_string ip "$ip"; json_add_int speed_bps "$speed"
    json_add_string speed "$(fmt_speed "$speed")"; json_add_string domains "$domains"
    json_add_int tested_at "$(date +%s)"; json_add_string resource_signature "$signature"
    json_add_int candidates "$candidates"; json_add_int valid_samples "$valid"
    json_add_boolean partial "$partial"
    json_dump >"$temporary" && chmod 600 "$temporary" && mv "$temporary" "$STATE_DIR/measurements/$group.json" || { rm -f "$temporary"; return 1; }
)

append_measurements() {
    local group document old="${JSON_PREFIX:-}" signature
    json_add_object measurements
    for group in $ALL_GROUPS; do
        document=$(cat "$STATE_DIR/measurements/$group.json" 2>/dev/null)
        [ -n "$document" ] || continue
        json_set_namespace XA_MEASUREMENT_
        if ! json_load "$document" 2>/dev/null; then json_set_namespace "$old"; continue; fi
        json_get_var signature resource_signature
        json_set_namespace "$old"
        json_add_object "$group"
        json_copy_current XA_MEASUREMENT_ "$old"
        json_add_boolean compatible "$( [ -n "$signature" ] && [ "$signature" = "$(measurement_signature "$group")" ] && echo 1 || echo 0 )"
        json_close_object
    done
    json_close_object
}

load_measurement() {
    local group=$1 requested=$2 old="${JSON_PREFIX:-}" document recorded_group signature
    . /usr/share/libubox/jshn.sh
    document=$(cat "$STATE_DIR/measurements/$group.json" 2>/dev/null)
    json_set_namespace XA_ADOPT_
    if ! json_load "$document" 2>/dev/null; then json_set_namespace "$old"; return 1; fi
    json_get_var MEASURED_ID id; json_get_var recorded_group group; json_get_var signature resource_signature
    json_get_var MEASURED_IP ip; json_get_var MEASURED_SPEED speed_bps; json_get_var MEASURED_TIME tested_at
    json_get_var MEASURED_DOMAINS domains; json_get_var MEASURED_PARTIAL partial
    json_set_namespace "$old"
    [ "$recorded_group" = "$group" ] && [ -n "$requested" ] && [ "$MEASURED_ID" = "$requested" ] || return 1
    [ "$(printf '%s\n' "$MEASURED_IP" | ipv4s | public_only)" = "$MEASURED_IP" ] || return 1
    [ "$(to_int "$MEASURED_SPEED")" -gt 0 ] || return 1
    [ -n "$signature" ] && [ "$signature" = "$(measurement_signature "$group")" ]
}

validate_adoption_config() (
    local adoption_group=$1
    # 在进程内校验采用后的开关，保持 UCI 与运行节点原样。
    cfg() {
        local value
        case "$1" in
        enabled|"optimize_$adoption_group") printf '1\n'; return ;;
        redirect_cn) [ "$adoption_group" != com ] || { printf '0\n'; return; } ;;
        esac
        value=$(uci -q get "$NAME.main.$1") || value=
        printf '%s\n' "${value:-$2}"
    }
    validate_config
)

measurement_rollback() {
    local group=$MEASUREMENT_GROUP file failed=0
    cp "$MEASUREMENT_TRANSACTION/config" "/etc/config/$NAME" || failed=1
    uci revert "$NAME" || failed=1
    for file in "best_$group" "domains_$group" "resources_$group"; do
        if [ -e "$MEASUREMENT_TRANSACTION/$file" ]; then cp "$MEASUREMENT_TRANSACTION/$file" "$STATE_DIR/$file" || failed=1;
        else rm -f "$STATE_DIR/$file" || failed=1; fi
    done
    if [ "$failed" = 1 ]; then
        MEASUREMENT_RECOVERY=1; TASK_STATUS=failed; TASK_CODE=CONFIG_ROLLBACK_FAILED
        TASK_MESSAGE="配置恢复失败，备份保留在 $MEASUREMENT_TRANSACTION；修复存储后恢复并重新应用"
        return 1
    fi
    MEASUREMENT_PENDING=0
}

cmd_adopt_measurement() {
    local group=$1 requested=$2 domains rc configuration_before validation_error file
    TASK_KIND=adopt
    lock; task_begin
    [ "$TASK_MEMORY_READY" = 1 ] || die '无法记录采用结果任务'
    group_valid "$group" || die '采用结果的平台无效'
    if ! load_measurement "$group" "$requested"; then
        TASK_STATUS=failed; TASK_CODE=MEASUREMENT_CHANGED; TASK_MESSAGE='测速结果或资源配置已更新，请刷新结果后重新选择'
        return 1
    fi
    if ! validation_error=$(validate_adoption_config "$group" 2>&1); then
        TASK_STATUS=failed; TASK_CODE=CONFIG_INVALID; TASK_MESSAGE="$validation_error"
        return 1
    fi
    configuration_before=$(configuration_signature)
    task_stage "domains_$group"
    if ! prepare_group_resource "$group"; then
        TASK_STATUS=failed; TASK_CODE="$GROUP_RESOURCE_CODE"; TASK_MESSAGE="$GROUP_RESOURCE_MESSAGE"
        return 1
    fi
    domains=$(verify_domains "$group" "$MEASURED_IP")
    if ! printf '%s\n' $domains | grep -qxF "$(url_host "$(group_url "$group")")"; then
        TASK_STATUS=failed; TASK_CODE=PRIMARY_DOMAIN_FAILED; TASK_MESSAGE='候选节点的主资源校验失败，请重新测速'
        return 1
    fi
    if [ "$configuration_before" != "$(configuration_signature)" ]; then
        TASK_STATUS=failed; TASK_CODE=CONFIG_CHANGED; TASK_MESSAGE='资源检查期间配置发生变化，请刷新后采用结果'
        return 1
    fi
    # 配置与节点备份用于存储提交失败的回滚，服务应用随后使用现有验收流程。
    MEASUREMENT_TRANSACTION="$STATE_DIR/adopt-$TASK_ID"
    MEASUREMENT_GROUP=$group; MEASUREMENT_PENDING=0; MEASUREMENT_RECOVERY=0
    mkdir -p "$MEASUREMENT_TRANSACTION" || die '无法创建节点备份'
    cp "/etc/config/$NAME" "$MEASUREMENT_TRANSACTION/config" || die '无法保存配置备份'
    for file in "best_$group" "domains_$group" "resources_$group"; do
        [ ! -e "$STATE_DIR/$file" ] || cp "$STATE_DIR/$file" "$MEASUREMENT_TRANSACTION/$file" || die '无法保存节点备份'
    done
    MEASUREMENT_PENDING=1
    if ! commit_group_result "$group" "$MEASURED_IP" "$domains"; then
        TASK_STATUS=failed; TASK_CODE=STATE_COMMIT_FAILED; TASK_MESSAGE='运行节点保存失败，原节点已恢复'
        measurement_rollback || return 1
        return 1
    fi
    if ! uci set "$NAME.main.optimize_$group=1" || ! uci set "$NAME.main.enabled=1" ||
        { [ "$group" = com ] && ! uci set "$NAME.main.redirect_cn=0"; } || ! uci commit "$NAME"; then
        TASK_STATUS=failed; TASK_CODE=CONFIG_COMMIT_FAILED; TASK_MESSAGE='功能配置保存失败，已恢复原配置与节点'
        measurement_rollback || return 1
        return 1
    fi
    MEASUREMENT_PENDING=0
    printf '%s %s %s\n' "$MEASURED_IP" "$MEASURED_SPEED" "$MEASURED_TIME" >"$TMP_DIR/last_$group"
    group_report "$group" success ADOPTED '已采用测速节点，正在应用并检查生效' "$MEASURED_IP" "$MEASURED_SPEED"
    task_event INFO NODE_SELECTED "group=$group selected_ip=$MEASURED_IP source_measurement=$MEASURED_ID"
    apply_and_verify; rc=$?
    case "$rc" in
    0) TASK_STATUS=success ;;
    2) TASK_STATUS=partial ;;
    *) TASK_STATUS=failed ;;
    esac
    TASK_CODE="$TASK_APPLY_CODE"; TASK_MESSAGE="$TASK_APPLY_MESSAGE"; TASK_STAGE=finished
    rm -rf "$MEASUREMENT_TRANSACTION"; MEASUREMENT_TRANSACTION=
    return "$rc"
}

measurement_cleanup() {
    [ -n "${MEASUREMENT_TRANSACTION:-}" ] || return 0
    [ "${MEASUREMENT_PENDING:-0}" != 1 ] || measurement_rollback || return 1
    [ "${MEASUREMENT_RECOVERY:-0}" = 1 ] || rm -rf "$MEASUREMENT_TRANSACTION"
    return 0
}
