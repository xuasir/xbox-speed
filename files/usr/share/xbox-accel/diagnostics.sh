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
        awk '{gsub(/\?[^[:space:]\"]*/, "?<redacted>"); print}'
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

probe_classify() {
    local rc=$1 http=$2 bytes=$3 minimum=$4
    case "$rc" in
    0) ;;
    22) ;; # --fail still leaves a useful HTTP status.
    28) [ "$http" = 206 ] && [ "$bytes" -ge "$minimum" ] && { echo TIME_LIMIT_WITH_DATA; return; }; echo TIMEOUT; return ;;
    5) echo PROXY_RESOLVE_FAILED; return ;;
    6) echo DNS_RESOLVE_FAILED; return ;;
    7) echo CONNECT_FAILED; return ;;
    3) echo URL_INVALID; return ;;
    18) echo PARTIAL_TRANSFER; return ;;
    23) echo LOCAL_WRITE_FAILED; return ;;
    35) echo TLS_HANDSHAKE_FAILED; return ;;
    52) echo EMPTY_REPLY; return ;;
    55) echo NETWORK_WRITE_FAILED; return ;;
    56) echo NETWORK_READ_FAILED; return ;;
    60) echo CERTIFICATE_FAILED; return ;;
    63) echo SIZE_LIMIT_EXCEEDED; return ;;
    92) echo HTTP2_STREAM_FAILED; return ;;
    97) echo PROXY_HANDSHAKE_FAILED; return ;;
    *) echo CURL_ERROR; return ;;
    esac
    case "$http" in
    206) [ "$bytes" -ge "$minimum" ] && echo OK || echo INSUFFICIENT_DATA ;;
    200) echo RANGE_IGNORED ;;
    301|302|303|307|308) echo UNEXPECTED_REDIRECT ;;
    403) echo HTTP_FORBIDDEN ;;
    404) echo HTTP_NOT_FOUND ;;
    416) echo RANGE_INVALID ;;
    429) echo RATE_LIMITED ;;
    5??) echo UPSTREAM_ERROR ;;
    000) echo NO_HTTP_RESPONSE ;;
    *) echo HTTP_ERROR ;;
    esac
}

probe_advice() {
    case "$1" in
    OK|TIME_LIMIT_WITH_DATA) echo '已取得足够测量数据；计时结束后可使用本次样本' ;;
    DNS_RESOLVE_FAILED|PROXY_RESOLVE_FAILED) echo '检查 DNS 上游与代理设置；域名未解析成功' ;;
    CONNECT_FAILED) echo '连接建立失败；结合路由、防火墙和节点可达性检查具体原因' ;;
    TIMEOUT) echo '请求超时且数据不足；可能是连接或传输慢，查看连接耗时和字节数' ;;
    HTTP_NOT_FOUND|RANGE_INVALID) echo '该节点不提供测试资源或字节范围无效；先检查测试 URL/offset' ;;
    HTTP_FORBIDDEN) echo '服务器拒绝请求；检查 Host 与节点是否兼容，不代表整条网络不通' ;;
    RANGE_IGNORED) echo '服务器未按 Range 返回，拒绝以完整文件响应作为测速结果' ;;
    INSUFFICIENT_DATA|PARTIAL_TRANSFER) echo '响应数据不足或中途截断；不能作为有效测速样本' ;;
    TLS_HANDSHAKE_FAILED|CERTIFICATE_FAILED) echo 'TLS/证书校验失败；不要通过关闭校验来规避' ;;
    SIZE_LIMIT_EXCEEDED) echo '响应超过下载上限，已中止以控制流量' ;;
    RATE_LIMITED) echo '服务器限流；降低测试频率，稍后重试' ;;
    URL_INVALID) echo '请求 URL 格式错误；检查测试资源配置' ;;
    INVALID_SPEED) echo '速度数据缺失或为零；检查 curl 测量输出并重新采样' ;;
    *) echo '检查请求错误信息与网络状态；当前不能确认节点有效' ;;
    esac
}

# Copy JSON across namespaces without eval of user data or resetting the outer response.
json_copy_current() {
    local source=$1 target=$2 skip=${3:-} keys key type value
    json_set_namespace "$source"
    json_get_keys keys
    for key in $keys; do
        case " $skip " in *" $key "*) continue ;; esac
        json_set_namespace "$source"
        json_get_type type "$key"
        case "$type" in
        object|array)
            json_select "$key"
            json_set_namespace "$target"
            if [ "$type" = object ]; then json_add_object "$key"; else json_add_array "$key"; fi
            json_copy_current "$source" "$target"
            json_set_namespace "$target"
            if [ "$type" = object ]; then json_close_object; else json_close_array; fi
            json_set_namespace "$source"; json_select ..
            ;;
        *)
            json_get_var value "$key"
            json_set_namespace "$target"
            case "$type" in
            int) json_add_int "$key" "$value" ;;
            boolean) json_add_boolean "$key" "$value" ;;
            double) json_add_double "$key" "$value" ;;
            null) json_add_null "$key" ;;
            *) json_add_string "$key" "$value" ;;
            esac
            ;;
        esac
    done
    json_set_namespace "$target"
}

task_record_running() {
    local id=$1 pid
    task_running || return 1
    pid=$(cat "$RUN_DIR/task.pid" 2>/dev/null)
    case "$id" in *-"$pid") return 0 ;; esac
    tr '\0' '\n' <"/proc/$pid/cmdline" 2>/dev/null | grep -qxF "$id"
}

json_append_document() {
    local name=$1 document=$2 effective=${3:-0} old="${JSON_PREFIX:-}" status queued record_id interrupted=0
    if [ -z "$document" ]; then
        json_add_object "$name"; json_add_string status none; json_add_string code NO_RECORD
        json_add_string message '尚无记录'; json_close_object; return 0
    fi
    json_set_namespace XA_DIAG_READ_
    if ! jshn -r "$document" >/dev/null 2>&1 || ! json_load "$document" 2>/dev/null; then
        json_set_namespace "$old"
        json_add_object "$name"
        json_add_string status unknown
        json_add_string code INVALID_RECORD
        json_add_string message '记录缺失或损坏，不能确认结果'
        json_close_object
        return 0
    fi
    if [ "$effective" = 1 ]; then
        json_get_var status status
        json_get_var record_id id
        json_get_var queued queued_at
        queued=$(to_int "$queued")
        case "$status" in
        running) task_record_running "$record_id" || interrupted=1 ;;
        queued) if ! task_record_running "$record_id" && [ $(($(date +%s) - ${queued:-0})) -gt 15 ]; then interrupted=1; fi ;;
        esac
    fi
    json_set_namespace "$old"
    json_add_object "$name"
    if [ "$interrupted" = 1 ]; then
        json_copy_current XA_DIAG_READ_ "$old" 'status code message'
        json_add_string status interrupted
        json_add_string code PROCESS_MISSING
        json_add_string message '任务没有留下正常结束记录且进程已不存在，不能视为成功；可能重启、崩溃或被强制结束'
    else
        json_copy_current XA_DIAG_READ_ "$old"
    fi
    json_close_object
}

task_group_prefix() {
    case "$1" in
    cn) echo T_CN ;; com) echo T_COM ;; store) echo T_STORE ;; ps) echo T_PS ;;
    xbox_old) echo T_XBOX_OLD ;; switch) echo T_SWITCH ;; *) return 1 ;;
    esac
}

add_task_group() {
    local group=$1 prefix
    prefix=$(task_group_prefix "$group") || return 1
    # eval 的代码只包含白名单前缀与变量引用；消息内容作为变量值展开。
    eval "set -- \"\${${prefix}_STATUS}\" \"\${${prefix}_CODE}\" \"\${${prefix}_MESSAGE}\" \"\${${prefix}_IP}\" \"\${${prefix}_SPEED}\" \"\${${prefix}_CANDIDATES}\" \"\${${prefix}_VALID}\""
    json_add_object "$group"
    json_add_string status "$1"; json_add_string code "$2"; json_add_string message "$(safe_text "$3")"
    json_add_string ip "$4"; json_add_int speed_bps "${5:-0}"
    json_add_int candidates "${6:-0}"; json_add_int valid_samples "${7:-0}"
    json_close_object
}

task_snapshot() {
    [ -n "$TASK_ID" ] || return 0
    local persist=${1:-0} destination="$TMP_DIR/live-task.json" temporary
    if [ "$persist" = 1 ] && ! task_persistence_needed; then persist=0; fi
    mkdir -p "$TMP_DIR"
    chmod 700 "$TMP_DIR"
    if [ "$persist" = 1 ]; then mkdir -p "$STATE_DIR"; chmod 700 "$STATE_DIR"; fi
    temporary="$destination.new.$$"
    (
        . /usr/share/libubox/jshn.sh
        json_init
        json_add_int schema 1; json_add_string id "$TASK_ID"
        json_add_string plugin_version "${VERSION:-unknown}"; json_add_string timezone "$(date +%Z)"
        json_add_string kind "$TASK_KIND"; json_add_string trigger "$TASK_TRIGGER"
        json_add_string status "$TASK_STATUS"; json_add_string stage "$TASK_STAGE"
        json_add_string code "$TASK_CODE"; json_add_string message "$(safe_text "$TASK_MESSAGE")"
        json_add_int queued_at "${TASK_QUEUED:-0}"; json_add_int started_at "$TASK_STARTED"
        json_add_int finished_at "$TASK_FINISHED"; json_add_int exit_code "$TASK_EXIT"
        json_add_string started_text "$( [ "$TASK_STARTED" -gt 0 ] && date -d "@$TASK_STARTED" '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || echo 未启动 )"
        json_add_string finished_text "$( [ "$TASK_FINISHED" -gt 0 ] && date -d "@$TASK_FINISHED" '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || echo 未结束 )"
        json_add_int duration_s "$((TASK_FINISHED > 0 && TASK_STARTED > 0 ? TASK_FINISHED - TASK_STARTED : 0))"
        json_add_boolean persistence_warning "$TASK_PERSIST_WARNING"
        json_add_object groups
        for group in $TASK_GROUPS; do add_task_group "$group"; done
        json_close_object
        json_add_object application
        json_add_string status "$TASK_APPLY_STATUS"; json_add_string code "$TASK_APPLY_CODE"
        json_add_string message "$(safe_text "$TASK_APPLY_MESSAGE")"; json_close_object
        json_add_object xbox
        json_add_string status unknown
        json_add_string message '只验证路由器软件与解析链路，Xbox 是否使用该节点及实际提速仍需实机确认'
        json_close_object
        json_add_object clients
        json_add_string status unknown
        json_add_string message '平台测速与路由器验收见分组明细；设备实际下载节点和稳定速度待实机验证'
        json_close_object
        json_dump
    ) >"$temporary" && mv "$temporary" "$destination" || { TASK_MEMORY_READY=0; return 1; }
    TASK_MEMORY_READY=1
    if [ "$TASK_KIND" = test ]; then
        temporary="$TMP_DIR/last-speed-task.json.new.$$"
        cp "$destination" "$temporary" && mv "$temporary" "$TMP_DIR/last-speed-task.json" || TASK_PERSIST_WARNING=1
    fi
    local persist_failed=0
    if [ "$persist" = 1 ]; then
        temporary="$STATE_DIR/last-task.json.new.$$"
        cp "$destination" "$temporary" && mv "$temporary" "$STATE_DIR/last-task.json" || persist_failed=1
        if [ "$TASK_KIND" = test ]; then
            temporary="$STATE_DIR/last-speed-task.json.new.$$"
            cp "$destination" "$temporary" && mv "$temporary" "$STATE_DIR/last-speed-task.json" || persist_failed=1
        fi
    fi
    if [ "$persist_failed" = 1 ]; then
        TASK_PERSIST_WARNING=1
        task_event WARN PERSIST_FAILED '摘要无法完整持久保存；当前结果仍保留在内存，重启可能丢失'
        task_snapshot 0 || true
        return 1
    fi
    return 0
}

task_prepare() {
    TASK_ID=${1:-$(date '+%Y%m%dT%H%M%S')-$$}
    TASK_KIND=${2:-test}; TASK_TRIGGER=${3:-manual_cli}
    TASK_QUEUED=$(date +%s); TASK_STARTED=0; TASK_FINISHED=0; TASK_EXIT=0
    TASK_STATUS=queued; TASK_STAGE=queued; TASK_CODE=ACCEPTED
    TASK_MESSAGE='任务已受理，但尚未完成；请等待最终摘要'
    task_snapshot 1
}

task_begin() {
    [ -n "$TASK_ID" ] || TASK_ID="$(date '+%Y%m%dT%H%M%S')-$$"
    TASK_STARTED=$(date +%s); TASK_QUEUED=${TASK_QUEUED:-$TASK_STARTED}; TASK_STATUS=running; TASK_STAGE=start; TASK_CODE=RUNNING
    TASK_MESSAGE='任务正在运行'; TASK_FINISHED=0; TASK_EXIT=0
    TASK_HAD_WARNING=0
    task_snapshot 1
    task_event INFO TASK_STARTED "kind=$TASK_KIND trigger=$TASK_TRIGGER"
}

task_stage() {
    TASK_STAGE=$1
    task_snapshot 0
}

group_report() {
    local group=$1 status=$2 code=$3 message=$4 ip=${5:-} speed=${6:-0} count=${7:-0} valid=${8:-0} prefix
    prefix=$(task_group_prefix "$group") || return 1
    eval "${prefix}_STATUS=\$status; ${prefix}_CODE=\$code; ${prefix}_MESSAGE=\$message; ${prefix}_IP=\$ip; ${prefix}_SPEED=\$speed; ${prefix}_CANDIDATES=\$count; ${prefix}_VALID=\$valid"
    task_snapshot 0
}

task_on_exit() {
    local rc=$1 severity=INFO group prefix status
    [ -n "$TASK_ID" ] || return 0
    TASK_EXIT=$rc; TASK_FINISHED=$(date +%s)
    if [ "$TASK_STATUS" = running ] || [ "$TASK_STATUS" = queued ]; then
        TASK_STATUS=failed; TASK_CODE=UNEXPECTED_EXIT
        TASK_MESSAGE="任务在 $TASK_STAGE 阶段退出（exit=$rc），没有完整结束摘要"
    fi
    for group in $TASK_GROUPS; do
        prefix=$(task_group_prefix "$group") || continue
        eval "status=\${${prefix}_STATUS}"
        if [ "$status" = running ]; then
            eval "${prefix}_STATUS=\$TASK_STATUS; ${prefix}_CODE=\$TASK_CODE; ${prefix}_MESSAGE=\$TASK_MESSAGE"
        fi
    done
    case "$TASK_STATUS" in failed) severity=ERROR ;; partial|cancelled|interrupted) severity=WARN ;; esac
    task_event "$severity" TASK_FINAL "status=$TASK_STATUS code=$TASK_CODE exit=$rc $TASK_MESSAGE"
    rotate_log
    if task_persistence_needed && ! persist_log; then
        TASK_PERSIST_WARNING=1
        task_event WARN LOG_PERSIST_FAILED '日志快照保存失败；当前日志可在运行日志页查看，建议立即导出'
    fi
    task_snapshot 1 || true
}

# A queued worker may be cancelled before installing its EXIT trap. Preserve its
# existing record and finish it from the requester only after the worker stops.
finish_cancelled_record() {
    local document old="${JSON_PREFIX:-}" status group prefix
    . /usr/share/libubox/jshn.sh
    document=$(last_task_document)
    json_set_namespace XA_CANCEL_
    if ! json_load "$document" 2>/dev/null; then json_set_namespace "$old"; return 0; fi
    json_get_var status status
    case "$status" in queued|running) ;; *) json_set_namespace "$old"; return 0 ;; esac
    json_get_var TASK_ID id; json_get_var TASK_KIND kind; json_get_var TASK_TRIGGER trigger
    json_get_var TASK_STAGE stage; json_get_var TASK_STARTED started_at; json_get_var TASK_QUEUED queued_at
    TASK_STARTED=$(to_int "$TASK_STARTED"); TASK_QUEUED=$(to_int "$TASK_QUEUED")
    if json_select groups; then
        for group in $TASK_GROUPS; do
            if json_select "$group"; then
                prefix=$(task_group_prefix "$group") || continue
                json_get_var "${prefix}_STATUS" status; json_get_var "${prefix}_CODE" code; json_get_var "${prefix}_MESSAGE" message
                json_get_var "${prefix}_IP" ip; json_get_var "${prefix}_SPEED" speed_bps
                json_get_var "${prefix}_CANDIDATES" candidates; json_get_var "${prefix}_VALID" valid_samples
                json_select ..
            fi
        done
        json_select ..
    fi
    if json_select application; then
        json_get_var TASK_APPLY_STATUS status; json_get_var TASK_APPLY_CODE code; json_get_var TASK_APPLY_MESSAGE message
        json_select ..
    fi
    json_set_namespace "$old"
    TASK_STATUS=cancelled; TASK_CODE=CANCELLED; TASK_MESSAGE='任务已取消'
    LOG_ACTIVE=1
    task_on_exit 130
}

last_task_document() {
    if [ -s "$TMP_DIR/live-task.json" ]; then
        cat "$TMP_DIR/live-task.json"
    elif [ -s "$STATE_DIR/last-task.json" ]; then
        cat "$STATE_DIR/last-task.json"
    else
        printf '%s\n' '{"status":"none","code":"NO_TASK","message":"尚无任务记录"}'
    fi
}

last_speed_document() {
    if [ -s "$TMP_DIR/last-speed-task.json" ]; then cat "$TMP_DIR/last-speed-task.json";
    elif [ -s "$STATE_DIR/last-speed-task.json" ]; then cat "$STATE_DIR/last-speed-task.json"; fi
}
