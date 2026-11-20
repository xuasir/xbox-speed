#!/bin/sh
# Export only whitelisted plugin data; never dump proxy configs, Wi-Fi keys or system identifiers.
REPORT_FILE="$TMP_DIR/diagnostic-report.json"

redact_report() {
    awk '
    function mask_urls(s, out,token,host,p) {
        out="";
        while(match(s,/https?:\/\/[^\\"[:space:]]+/)) {
            out=out substr(s,1,RSTART-1); token=substr(s,RSTART,RLENGTH);
            p=index(token,"://"); host=substr(token,p+3); sub(/[\/?#].*$/, "",host); sub(/^.*@/, "",host);
            out=out substr(token,1,p+2) host "/<path-redacted>";
            s=substr(s,RSTART+RLENGTH);
        }
        return out s;
    }
    function mask_ipv4(s,out,ip,start,len) {
        out="";
        while(match(s,/[0-9][0-9]?[0-9]?\.[0-9][0-9]?[0-9]?\.[0-9][0-9]?[0-9]?\.[0-9][0-9]?[0-9]?/)) {
            start=RSTART;len=RLENGTH;ip=substr(s,start,len);
            if(!(ip in ipv4)) ipv4[ip]="IP_" ++n4;
            out=out substr(s,1,start-1) ipv4[ip]; s=substr(s,start+len);
        }
        return out s;
    }
    function mask_ipv6(s,out,addr,start,len,colons,key) {
        out="";
        while(match(s,/[0-9a-fA-F]*:[0-9a-fA-F]*:[0-9a-fA-F:.]+(%[A-Za-z0-9_.-]+)?/)) {
            start=RSTART;len=RLENGTH;addr=substr(s,start,len);
            key=tolower(addr); colons=gsub(/:/,":",key);
            if(colons==5 && length(key)==17 && key~/^[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]:[0-9a-f][0-9a-f]$/) {
                if(!(key in macs)) macs[key]="MAC_" ++nm;
                addr=macs[key];
            } else if(index(key,"::") || colons>=7 || (colons>=6 && index(key,"."))) {
                if(!(key in addresses)) addresses[key]="ADDR_" ++n6;
                addr=addresses[key];
            }
            out=out substr(s,1,start-1) addr;s=substr(s,start+len);
        }
        return out s;
    }
    function mask_credentials(s,out,item,start,len) {
        out="";
        while(match(tolower(s),/(password|passwd|token|secret|authorization|sig)=[^\\"[:space:],;]+/)) {
            start=RSTART;len=RLENGTH;item=substr(s,start,len);sub(/=.*/,"=<redacted>",item);
            out=out substr(s,1,start-1) item;s=substr(s,start+len);
        }
        return out s;
    }
    {
        # JSON permits unescaped /; normalize it before URL matching.
        gsub(/\\\//,"/");
        value=mask_urls($0);
        gsub(/\?[^\\"[:space:]]*/,"?<redacted>",value);
        value=mask_credentials(value);
        print mask_ipv4(mask_ipv6(value));
    }'
}

report_available() { [ -s "$REPORT_FILE" ]; }

build_redacted_report() {
    local checks=$1 raw="$TMP_DIR/diagnostic-report.raw.$$" temporary="$REPORT_FILE.new.$$" key
    mkdir -p "$TMP_DIR"; chmod 700 "$TMP_DIR"
    (
        . /usr/share/libubox/jshn.sh
        json_init
        json_add_int schema 1; json_add_string plugin_version "$VERSION"
        json_add_int generated_at "$(date +%s)"; json_add_string timezone "$(date +%Z)"
        json_add_boolean redacted 1
        json_add_string privacy 'IPv4/IPv6/MAC 地址使用报告内一致别名；URL 路径和参数隐藏；未收集代理、Wi-Fi密码、主机名或序列号。分享前仍请自行检查。'
        json_append_document source_task "$(cat "$TMP_DIR/export-target-task.json" 2>/dev/null)" 1
        json_append_document last_test "$(last_speed_document)" 1
        json_append_document current_checks "$checks"
        json_append_document last_application "$(application_report_document)"
        json_add_object configuration
        for key in enabled autostart optimize_cn optimize_com redirect_cn isp resolver_port dnsmasq_instance ttl cron schedule_type schedule_time schedule_weekday schedule_monthday health_interval quick_size_mb quick_time full_size_mb full_time max_candidates top_n switch_threshold passwall_direct passwall_restart override_cdn_hosts; do
            json_add_string "$key" "$(cfg "$key" '')"
        done
        json_close_object
        json_add_string recent_plugin_log "$(diagnostic_log | tail -n 300)"
        json_add_string previous_task_log "$(tail -n 300 "$STATE_DIR/activity-last.log" 2>/dev/null)"
        json_add_string limitation '没有抓包，也没有 Xbox 实际速度数据；软件检查成功不能证明 Xbox 已加速。'
        json_dump
    ) >"$raw" || return 1
    redact_report <"$raw" >"$temporary" || return 1
    if ! jshn -R "$temporary" >/dev/null 2>&1; then
        task_event ERROR REPORT_JSON_INVALID '脱敏报告不是有效 JSON，未公开下载'
        rm -f "$raw" "$temporary"
        return 1
    fi
    chmod 600 "$temporary"
    mv "$temporary" "$REPORT_FILE" || return 1
    rm -f "$raw"
    return 0
}

cmd_report_task() {
    local checks rc
    TASK_KIND=report
    lock; task_begin; task_stage diagnostic_export
    [ "$TASK_MEMORY_READY" = 1 ] || die '无法记录诊断任务'
    checks=$(diagnostic_environment); rc=$?
    if [ "$rc" != 0 ] || [ -z "$checks" ]; then
        TASK_STATUS=failed; TASK_CODE=DIAGNOSTIC_EXECUTION_FAILED; TASK_MESSAGE='诊断未正常完成，没有生成新报告'
        return 1
    fi
    application_from_report "$checks" || true
    TASK_APPLY_MESSAGE="本次仅诊断未修改配置：$TASK_APPLY_MESSAGE"
    if ! build_redacted_report "$checks"; then
        TASK_STATUS=failed; TASK_CODE=REPORT_WRITE_FAILED; TASK_MESSAGE='报告生成、验证或保存失败；不能下载新报告'
        return 1
    fi
    TASK_STATUS=success; TASK_CODE=REPORT_READY
    TASK_MESSAGE='脱敏报告已生成；这是导出成功，不代表检查全通过或 Xbox 已提速'
    TASK_STAGE=finished
    return 0
}
