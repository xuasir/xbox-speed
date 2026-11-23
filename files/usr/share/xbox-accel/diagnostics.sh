#!/bin/sh
# Diagnostic primitives. Sourced after the core helpers; uses stock OpenWrt jshn.
umask 077
TASK_ID=${TASK_ID:-}
TASK_STAGE=${TASK_STAGE:-idle}
TASK_KIND=${TASK_KIND:-test}
TASK_TRIGGER=${TASK_TRIGGER:-manual_cli}
TASK_STATUS=${TASK_STATUS:-running}
TASK_CODE=${TASK_CODE:-RUNNING}
TASK_MESSAGE=${TASK_MESSAGE:-}
TASK_STARTED=0
TASK_FINISHED=0
TASK_EXIT=0
TASK_PERSIST_WARNING=0
TASK_MEMORY_READY=0
TASK_APPLY_STATUS=unknown
TASK_APPLY_CODE=NOT_VERIFIED
TASK_APPLY_MESSAGE='尚未执行路由器链路验收'
T_CN_STATUS=skipped; T_CN_CODE=NOT_REQUESTED; T_CN_MESSAGE='未请求此组'
T_COM_STATUS=skipped; T_COM_CODE=NOT_REQUESTED; T_COM_MESSAGE='未请求此组'
T_CN_IP=; T_CN_SPEED=0; T_CN_CANDIDATES=0; T_CN_VALID=0
T_COM_IP=; T_COM_SPEED=0; T_COM_CANDIDATES=0; T_COM_VALID=0
T_STORE_STATUS=skipped; T_STORE_CODE=NOT_REQUESTED; T_STORE_MESSAGE='未请求此组'
T_PS_STATUS=skipped; T_PS_CODE=NOT_REQUESTED; T_PS_MESSAGE='未请求此组'
T_XBOX_OLD_STATUS=skipped; T_XBOX_OLD_CODE=NOT_REQUESTED; T_XBOX_OLD_MESSAGE='未请求此组'
T_SWITCH_STATUS=skipped; T_SWITCH_CODE=NOT_REQUESTED; T_SWITCH_MESSAGE='未请求此组'
T_STORE_IP=; T_STORE_SPEED=0; T_STORE_CANDIDATES=0; T_STORE_VALID=0
T_PS_IP=; T_PS_SPEED=0; T_PS_CANDIDATES=0; T_PS_VALID=0
T_XBOX_OLD_IP=; T_XBOX_OLD_SPEED=0; T_XBOX_OLD_CANDIDATES=0; T_XBOX_OLD_VALID=0
T_SWITCH_IP=; T_SWITCH_SPEED=0; T_SWITCH_CANDIDATES=0; T_SWITCH_VALID=0
TASK_GROUPS='cn com store ps xbox_old switch'
TASK_HAD_WARNING=0
LOG_INFO_CODES='TASK_STARTED TASK_FINAL STAGE_SUMMARY NODE_SELECTED ROUTER_VERIFIED DISABLE_VERIFIED'

log_event_enabled() {
    [ "${LOG_MODE:-concise}" = detail ] && return 0
    case "$1" in
    WARN|ERROR) return 0 ;;
    INFO)
        [ "${TASK_KIND:-}" = health ] && [ "$2" = TASK_STARTED ] && return 1
        case " $LOG_INFO_CODES " in *" $2 "*) return 0 ;; esac ;;
    esac
    return 1
}

# 正常健康检查的摘要留在内存；告警、修复和其他任务仍保存到闪存。
task_persistence_needed() {
    [ "${LOG_MODE:-concise}" = concise ] && [ "$TASK_KIND" = health ] &&
        [ "$TASK_HAD_WARNING" = 0 ] && [ "$TASK_PERSIST_WARNING" = 0 ] || return 0
    case "$TASK_STATUS" in
    queued|running) return 1 ;;
    success|skipped)
        [ "$TASK_APPLY_CODE" = NO_CHANGE ] || return 0
        case "$TASK_CODE" in HEALTH_COMPLETED|NOT_DUE) return 1 ;; esac ;;
    esac
    return 0
}

# Write flash only at task completion, with a fixed size and one atomic snapshot.
diagnostic_log() {
    if [ -s "$LOG_FILE" ]; then cat "$LOG_FILE";
    elif [ -s "$STATE_DIR/activity-last.log" ]; then cat "$STATE_DIR/activity-last.log"; fi
}

display_log() {
    if [ "${LOG_MODE:-concise}" = detail ]; then
        diagnostic_log | tail -n 300
    else
        diagnostic_log | awk -v codes="$LOG_INFO_CODES" '
            BEGIN {gsub(/ /,"|",codes); summary="\\[INFO\\]\\[(" codes ")\\]"}
            /^[0-9][0-9]-[0-9][0-9] [0-9:]+ \[ (正常|信息|未执行) \]/ {skip_expected=1; next}
            skip_expected && /^[0-9][0-9]-[0-9][0-9] [0-9:]+ +预期=/ {next}
            {skip_expected=0}
            /\[(WARN|ERROR)\]\[/ {print; next}
            /\[(DEBUG|INFO)\]\[/ {if ($0 ~ summary) print; next}
            {print}' | tail -n 300
    fi
}

persist_log() {
    local temporary="$STATE_DIR/activity-last.log.new.$$"
    [ -s "$LOG_FILE" ] || return 0
    mkdir -p "$STATE_DIR" || return 1
    tail -n 500 "$LOG_FILE" >"$temporary" || { rm -f "$temporary"; return 1; }
    if [ "$(wc -c <"$temporary")" -gt 131072 ]; then
        tail -c 131072 "$temporary" >"$temporary.bounded" && mv "$temporary.bounded" "$temporary" || { rm -f "$temporary" "$temporary.bounded"; return 1; }
    fi
    chmod 600 "$temporary" && mv "$temporary" "$STATE_DIR/activity-last.log"
}

# Keep query-string credentials out of both local task records and exported logs.
safe_text() {
    printf '%s' "$1" | tr -d '\001-\010\013\014\016-\037\177' | tr '\r\n' '  ' |
        awk '{gsub(/\?[^[:space:"]*/, "?<redacted>"); print}'
}

task_event() {
    local level=$1 code=$2 message line
    case "$level" in WARN|ERROR) TASK_HAD_WARNING=1 ;; esac
    log_event_enabled "$level" "$code" || return 0
    message=$(safe_text "$3")
    line="$(stamp) [${TASK_ID:--}][$TASK_STAGE][$level][$code] $message"
    printf '%s\n' "$line" >&2
    if [ "$LOG_ACTIVE" = 1 ] && [ "$QUIET" != 1 ]; then
        mkdir -p "$TMP_DIR"
        printf '%s\n' "$line" >>"$LOG_FILE"
    fi
    case "$level" in WARN|ERROR) system_log "$line" ;; esac
    return 0
}
