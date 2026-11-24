#!/bin/sh
# 有界事件与任务历史。日常事件批量保存，异常和关键任务结束及时保存。

records_prepare() {
    local file
    mkdir -p "$TMP_DIR/records/tasks" "$RUN_DIR" || return 1
    [ -e "$TMP_DIR/records/ready" ] && return 0
    # 上次目录交换中断时恢复完整的旧快照，再开始接收新事件。
    if [ ! -d "$STATE_DIR/records" ] && [ -d "$STATE_DIR/records.previous" ]; then
        mv "$STATE_DIR/records.previous" "$STATE_DIR/records" || return 1
    fi
    for file in events.jsonl errors.jsonl; do
        if [ -f "$STATE_DIR/records/$file" ]; then
            cp "$STATE_DIR/records/$file" "$TMP_DIR/records/$file" || return 1
        else
            : >"$TMP_DIR/records/$file" || return 1
        fi
    done
    for file in "$STATE_DIR/records/tasks"/*.json; do
        [ -f "$file" ] || continue
        cp "$file" "$TMP_DIR/records/tasks/" || return 1
    done
    : >"$TMP_DIR/records/ready"
    rm -rf "$STATE_DIR"/records.new.* "$STATE_DIR/records.previous"
}

records_bound() {
    local file=$1 count=$2 bytes=$3 temporary="$1.new.$$"
    [ -f "$file" ] || return 0
    tail -n "$count" "$file" >"$temporary" || return 1
    while [ "$(wc -c <"$temporary")" -gt "$bytes" ]; do
        count=$((count/2))
        if [ "$count" -lt 1 ]; then : >"$temporary"; break; fi
        tail -n "$count" "$temporary" >"$temporary.bounded" && mv "$temporary.bounded" "$temporary" || { rm -f "$temporary" "$temporary.bounded"; return 1; }
    done
    mv "$temporary" "$file"
}

# 调用者持有 records.lock；以临时目录交换整份记录，保留旧快照至提交成功。
records_flush_locked() {
    local force=${1:-0} now last transaction file count=0
    now=$(date +%s); last=$(to_int "$(cat "$TMP_DIR/records/saved_at" 2>/dev/null)")
    [ "$force" = 1 ] || [ $((now-last)) -ge 600 ] || return 0
    mkdir -p "$STATE_DIR" || return 1
    transaction="$STATE_DIR/records.new.$$"
    mkdir -p "$transaction/tasks" || return 1
    for file in events.jsonl errors.jsonl; do
        cp "$TMP_DIR/records/$file" "$transaction/$file" || { rm -rf "$transaction"; return 1; }
    done
    for file in "$TMP_DIR/records/tasks"/*.json; do
        [ -f "$file" ] || continue
        cp "$file" "$transaction/tasks/" || { rm -rf "$transaction"; return 1; }
    done
    chmod -R go-rwx "$transaction"
    rm -rf "$STATE_DIR/records.previous"
    if [ -d "$STATE_DIR/records" ]; then
        mv "$STATE_DIR/records" "$STATE_DIR/records.previous" || { rm -rf "$transaction"; return 1; }
    fi
    if ! mv "$transaction" "$STATE_DIR/records"; then
        [ ! -d "$STATE_DIR/records.previous" ] || mv "$STATE_DIR/records.previous" "$STATE_DIR/records"
        rm -rf "$transaction"; return 1
    fi
    rm -rf "$STATE_DIR/records.previous"
    printf '%s\n' "$now" >"$TMP_DIR/records/saved_at"
}

record_event() (
    local level=$1 code=$2 message=$3 kind=${4:-${TASK_KIND:-service}} document force=0
    mkdir -p "$RUN_DIR" || return 1
    exec 6>"$RUN_DIR/records.lock"
    flock 6 || return 1
    records_prepare || return 1
    document=$(
        . /usr/share/libubox/jshn.sh
        json_init
        json_add_int time "$(date +%s)"; json_add_string level "$level"
        json_add_string code "$code"; json_add_string kind "$kind"
        json_add_string task_id "${TASK_ID:-}"; json_add_string message "$(safe_text "$message")"
        json_dump
    ) || return 1
    printf '%s\n' "$document" >>"$TMP_DIR/records/events.jsonl" || return 1
    records_bound "$TMP_DIR/records/events.jsonl" 300 131072 || return 1
    case "$level" in
    WARN|ERROR)
        printf '%s\n' "$document" >>"$TMP_DIR/records/errors.jsonl" || return 1
        records_bound "$TMP_DIR/records/errors.jsonl" 100 65536 || return 1
        force=1 ;;
    esac
    records_flush_locked "$force"
)

record_task_complete() (
    local file name count=0 force=0
    [ -f "$TMP_DIR/live-task.json" ] || return 0
    mkdir -p "$RUN_DIR" || return 1
    exec 6>"$RUN_DIR/records.lock"
    flock 6 || return 1
    records_prepare || return 1
    name="$TASK_ID"
    [ "$TASK_KIND" != health ] || name=health
    cp "$TMP_DIR/live-task.json" "$TMP_DIR/records/tasks/$name.json.new.$$" &&
        mv "$TMP_DIR/records/tasks/$name.json.new.$$" "$TMP_DIR/records/tasks/$name.json" || return 1
    for file in $(LC_ALL=C ls "$TMP_DIR/records/tasks" 2>/dev/null | sort -r); do
        [ "$file" != health.json ] || continue
        count=$((count+1))
        [ "$count" -le 20 ] || rm -f "$TMP_DIR/records/tasks/$file"
    done
    task_persistence_needed && force=1
    records_flush_locked "$force"
)

records_flush_due() (
    [ -e "$TMP_DIR/records/ready" ] || return 0
    exec 6>"$RUN_DIR/records.lock"
    flock -n 6 || return 0
    records_flush_locked "${1:-0}"
)

clear_event_records() (
    mkdir -p "$RUN_DIR" || return 1
    exec 6>"$RUN_DIR/records.lock"
    flock 6 || return 1
    records_prepare || return 1
    : >"$TMP_DIR/records/events.jsonl"
    : >"$TMP_DIR/records/errors.jsonl"
    records_flush_locked 1
)

json_records() (
    local directory="$TMP_DIR/records" file document
    . /usr/share/libubox/jshn.sh
    mkdir -p "$RUN_DIR" || return 1
    exec 6>"$RUN_DIR/records.lock"
    flock -s 6 || return 1
    [ -e "$directory/ready" ] || directory="$STATE_DIR/records"
    [ -d "$directory" ] || directory="$STATE_DIR/records.previous"
    json_init
    json_add_string source "$([ "$directory" = "$TMP_DIR/records" ] && echo live || echo persisted)"
    json_add_array events
    if [ -f "$directory/events.jsonl" ]; then
        while IFS= read -r document; do
            [ -z "$document" ] || json_append_document "" "$document"
        done <"$directory/events.jsonl"
    fi
    json_close_array
    json_add_array errors
    if [ -f "$directory/errors.jsonl" ]; then
        while IFS= read -r document; do
            [ -z "$document" ] || json_append_document "" "$document"
        done <"$directory/errors.jsonl"
    fi
    json_close_array
    json_add_array tasks
    for file in $(LC_ALL=C ls "$directory/tasks" 2>/dev/null | sort -r); do
        case "$file" in *.json) ;; *) continue ;; esac
        document=$(cat "$directory/tasks/$file" 2>/dev/null)
        [ -z "$document" ] || json_append_document "" "$document" 1
    done
    json_close_array
    json_dump
)
