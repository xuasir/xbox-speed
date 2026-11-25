#!/bin/sh
# 从设备连接、插件 DNS 和重定向入口收集证据；采样与日志均有容量上限。

observation_signature() {
    {
        # 页面筛选项随配置保存，连接观测只比较业务设置。
        uci -q export xbox-accel | sed -e '/^[[:space:]]*list download_devices /d' -e '/^[[:space:]]*option download_devices /d'
        cat "$RESOLVER_CONF" "$REDIRECT_CONF" /proc/sys/kernel/random/boot_id 2>/dev/null
        for group in $ALL_GROUPS; do cat "$STATE_DIR/best_$group" "$STATE_DIR/domains_$group" 2>/dev/null; done
    } | {
        if command -v sha256sum >/dev/null 2>&1; then sha256sum;
        elif command -v md5sum >/dev/null 2>&1; then md5sum;
        else cat >/dev/null; printf 'unavailable\n'; fi
    } | awk '{print $1}'
}

observation_devices() {
    local device
    device=$(redirect_device)
    {
        awk 'NF>=4 && $3~/^[0-9.]+$/ {name=$4; if(name=="*") name=$3; gsub(/[[:cntrl:]]/,"",name); print $3 "\t" name}' /tmp/dhcp.leases 2>/dev/null
        ip neigh show dev "$device" 2>/dev/null | awk '$1~/^[0-9a-fA-F:.]+$/ && !/FAILED|INCOMPLETE/ {print $1 "\t" $1}'
    } | awk -F '\t' '!seen[$1]++ && length($1)<=45 && length($2)<=128'
}

observation_connections() {
    if command -v conntrack >/dev/null 2>&1 && conntrack -L -o extended 2>/dev/null; then
        return 0
    fi
    if [ -r /proc/net/nf_conntrack ]; then cat /proc/net/nf_conntrack; return; fi
    if [ -r /proc/net/ip_conntrack ]; then cat /proc/net/ip_conntrack; return; fi
    return 1
}

# orig/reply 元组与下行字节分别解析，端口用于区分并发连接，UDP 443 覆盖 QUIC。
observation_parse_connections() {
    awk '
    {
        ns=nd=np=nq=nb=0; src=dst=sport=dport=rsrc=rport="-"; bytes="-"; mark=0; proto=""; state=""
        for(i=1;i<=NF;i++) {
            if($i=="tcp" || $i=="udp") proto=$i
            if($i=="ESTABLISHED" || $i=="SYN_SENT" || $i=="SYN_RECV" || $i=="CLOSE_WAIT" || $i=="TIME_WAIT") state=$i
            split($i,a,"=")
            if(a[1]=="src") {if(++ns==1) src=a[2]; else if(ns==2) rsrc=a[2]}
            if(a[1]=="dst" && ++nd==1) dst=a[2]
            if(a[1]=="sport") {if(++np==1) sport=a[2]; else if(np==2) rport=a[2]}
            if(a[1]=="dport" && ++nq==1) dport=a[2]
            if(a[1]=="bytes" && ++nb==2) bytes=a[2]
            if(a[1]=="mark") mark=a[2]
        }
        if(proto=="" || src !~ /^[0-9a-fA-F:.]+$/ || dst !~ /^[0-9a-fA-F:.]+$/ || sport !~ /^[0-9]+$/ || dport !~ /^(80|443)$/) next
        if(proto=="tcp" && state!="ESTABLISHED") next
        if(bytes!="-" && bytes !~ /^[0-9]+$/) bytes="-"
        if(mark !~ /^[0-9]+$/) mark=0
        printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n",src,dst,sport,dport,proto,(state?state:"CONNECTED"),rsrc,rport,bytes,mark
    }'
}

observation_host_groups() {
    local group host
    for group in $ALL_GROUPS; do
        for host in $(group_domains "$group"); do printf '%s\t%s\n' "$host" "$group"; done
    done
    for host in $COM_DL_DOMAINS $CDN_HOSTS; do printf '%s\tcom\n' "$host"; done
}

observation_prepare_targets() {
    local group ip old="$TMP_DIR/observation/targets.tsv" now=$1 signature=$2 host lines previous delta active
    if [ "$signature" != "$(cat "$TMP_DIR/observation/signature" 2>/dev/null)" ]; then
        : >"$TMP_DIR/observation/expected.tsv"
        : >"$TMP_DIR/observation/current-targets.tsv"
        active=$(groups_active | tr '\n' ' ')
        for group in $ALL_GROUPS; do
            if [ "$group" = com ] && [ "$(com_mode)" = redirect ]; then
                ip=$(redirect_ip)
            else
                case " $active " in *" $group "*) ;; *) continue ;; esac
                ip=$(best_ip "$group")
            fi
            [ -n "$ip" ] || continue
            printf '%s\t%s\n' "$group" "$ip" >>"$TMP_DIR/observation/expected.tsv"
            printf '%s\t%s\t-\t%s\tconfigured\n' "$group" "$ip" "$now" >>"$TMP_DIR/observation/current-targets.tsv"
        done
        observation_host_groups >"$TMP_DIR/observation/hosts.tsv"
        printf '%s\n' "$signature" >"$TMP_DIR/observation/signature"
    fi
    # 当前配置持续有效，近期节点与 DNS 证据按各自观测时间过期。
    awk -F '\t' -v now="$now" 'BEGIN {OFS="\t"} {$4=now; print}' "$TMP_DIR/observation/current-targets.tsv" >"$TMP_DIR/observation/current-targets.new"
    mv "$TMP_DIR/observation/current-targets.new" "$TMP_DIR/observation/current-targets.tsv"
    : >"$TMP_DIR/observation/dns-targets.tsv"
    lines=$(to_int "$(wc -l <"$TMP_DIR/dns-observation.log" 2>/dev/null)")
    previous=$(to_int "$(cat "$TMP_DIR/observation/dns-lines" 2>/dev/null)")
    [ "$lines" -ge "$previous" ] || previous=0
    delta=$((lines-previous)); [ "$delta" -le 1500 ] || delta=1500
    if [ "$delta" -gt 0 ]; then
        tail -n "$delta" "$TMP_DIR/dns-observation.log" | awk -F '\t' -v now="$now" '
            FNR==NR {groups[$1]=$2; next}
            {
                n=split($0,a,/[[:space:]]+/)
                for(i=1;i<=n-3;i++) if((a[i]=="reply" || a[i]=="config") && a[i+2]=="is" && a[i+3]~/^[0-9a-fA-F:.]+$/ && groups[a[i+1]]!="")
                    print groups[a[i+1]] "\t" a[i+3] "\t" a[i+1] "\t" now "\tdns"
            }' "$TMP_DIR/observation/hosts.tsv" - >"$TMP_DIR/observation/dns-targets.tsv"
    fi
    printf '%s\n' "$lines" >"$TMP_DIR/observation/dns-lines"
    {
        cat "$TMP_DIR/observation/current-targets.tsv" "$TMP_DIR/observation/dns-targets.tsv"
        [ ! -f "$old" ] || cat "$old"
    } | awk -F '\t' -v now="$now" 'now-$4<86400 && !seen[$1 FS $2 FS $3]++' | head -n 200 >"$old.new"
    mv "$old.new" "$old"
    # copytruncate 保留 dnsmasq 已打开的日志描述符。
    if [ "$lines" -gt 1500 ]; then
        tail -n 1000 "$TMP_DIR/dns-observation.log" >"$TMP_DIR/observation/dns-tail"
        cat "$TMP_DIR/observation/dns-tail" >"$TMP_DIR/dns-observation.log"
        printf '1000\n' >"$TMP_DIR/observation/dns-lines"
        rm -f "$TMP_DIR/observation/dns-tail"
    fi
}

observation_firewall_capability() {
    local now=$1 previous complete=0 possible=1 read_failed=0
    previous=$(to_int "$(cat "$TMP_DIR/observation/firewall-time" 2>/dev/null)")
    [ $((now-previous)) -ge 30 ] || return 0
    : >"$TMP_DIR/observation/firewall.raw"
    if command -v nft >/dev/null 2>&1; then
        if nft list ruleset >"$TMP_DIR/observation/nft.raw" 2>/dev/null; then
            complete=1; cat "$TMP_DIR/observation/nft.raw" >>"$TMP_DIR/observation/firewall.raw"
        else read_failed=1; fi
    fi
    if command -v iptables-save >/dev/null 2>&1; then
        if iptables-save >"$TMP_DIR/observation/iptables.raw" 2>/dev/null; then
            complete=1; cat "$TMP_DIR/observation/iptables.raw" >>"$TMP_DIR/observation/firewall.raw"
        else read_failed=1; fi
    elif [ -s /proc/net/ip_tables_names ]; then
        read_failed=1
    fi
    if command -v ip6tables-save >/dev/null 2>&1; then
        if ip6tables-save >"$TMP_DIR/observation/ip6tables.raw" 2>/dev/null; then
            complete=1; cat "$TMP_DIR/observation/ip6tables.raw" >>"$TMP_DIR/observation/firewall.raw"
        else read_failed=1; fi
    elif [ -s /proc/net/ip6_tables_names ]; then
        read_failed=1
    fi
    if [ "$complete" = 1 ] && [ "$read_failed" = 0 ] &&
        ! grep -Ei 'tproxy|redirect|DNAT|REDIRECT|TPROXY' "$TMP_DIR/observation/firewall.raw" >/dev/null; then possible=0; fi
    printf '%s\n' "$possible" >"$TMP_DIR/observation/interception-possible"
    printf '%s\n' "$now" >"$TMP_DIR/observation/firewall-time"
}

observation_path() {
    local source=$1 target=$2 reply=$3 remote_port=$4 reply_port=$5 mark=$6 route device route_device family=-4
    if [ "$reply" != "$target" ] || [ "$reply_port" != "$remote_port" ]; then
        if [ "$target" = "$(redirect_ip)" ] && [ "$reply" = "$(lan_ip)" ] && [ "$reply_port" = "$(redirect_port)" ]; then echo redirect_entry;
        else echo intercepted; fi
        return
    fi
    [ "$mark" = 0 ] && [ "$(cat "$TMP_DIR/observation/interception-possible" 2>/dev/null)" = 0 ] || { echo unknown; return; }
    device=$(redirect_device)
    case "$target" in *:*) family=-6 ;; esac
    route=$(ip "$family" route get "$target" from "$source" iif "$device" 2>/dev/null)
    route_device=$(printf '%s\n' "$route" | awk '{for(i=1;i<NF;i++) if($i=="dev") {print $(i+1); exit}}')
    case "$route_device" in tun*|tap*|utun*|wg*|tailscale*|clash*|mihomo*) echo unknown; return ;; esac
    [ ! -e "/sys/class/net/$route_device/tun_flags" ] || { echo unknown; return; }
    if [ -n "$route" ] && ! printf '%s\n' "$route" | grep -E "(^local |dev (lo|$device)( |$))" >/dev/null; then echo direct;
    else echo unknown; fi
}

observation_step() (
    local now signature available=1 counters=0 file source target sport port proto groups status speed bytes activity shared reply reply_port mark observed path redirect_time redirect_host redirect_target redirect_previous count=0
    mkdir -p "$TMP_DIR/observation" "$RUN_DIR" || return 1
    exec 6>"$RUN_DIR/observation.lock"
    flock -n 6 || return 0
    now=$(date +%s); signature=$(observation_signature)
    observation_devices >"$TMP_DIR/observation/devices.tsv"
    observation_prepare_targets "$now" "$signature"
    observation_firewall_capability "$now"
    observation_connections >"$TMP_DIR/observation/connections.raw" || available=0
    observation_parse_connections <"$TMP_DIR/observation/connections.raw" >"$TMP_DIR/observation/connections.tsv"
    [ -f "$TMP_DIR/observation/samples.tsv" ] || : >"$TMP_DIR/observation/samples.tsv"
    : >"$TMP_DIR/observation/events.tsv"
    awk -F '\t' -v now="$now" -v signature="$signature" -v samples="$TMP_DIR/observation/samples.new" -v events="$TMP_DIR/observation/events.tsv" '
    FILENAME==ARGV[1] {expected[$1]=$2; next}
    FILENAME==ARGV[2] {if(expected[$1]!="") {if(!seen[$2 FS $1]++) target_groups[$2]=target_groups[$2] (target_groups[$2]!=""?",":"") $1}; next}
    FILENAME==ARGV[3] {devices[$1]=1; next}
    FILENAME==ARGV[4] {old_bytes[$1]=$2; old_time[$1]=$3; first_signature[$1]=$4; previous[$1]=$5; last_active[$1]=$6; next}
    {
        if(!devices[$1]) next
        key=$1 "|" $2 "|" $3 "|" $4 "|" $5
        seen_flow[key]=1; known=($9!="-"); delta=0; rate=0
        if(known && key in old_bytes && old_bytes[key]!="-" && $9<old_bytes[key]) {first_signature[key]=signature; previous[key]=""}
        if(known && key in old_bytes && old_bytes[key]!="-" && $9>=old_bytes[key] && now>old_time[key] && now-old_time[key]<=15) {delta=$9-old_bytes[key]; rate=int(delta/(now-old_time[key]))}
        active=(delta>0?now:last_active[key]); if(!active) active=now
        activity=(delta>0?"downloading":known?"connected":"counters_unavailable")
        status="unidentified"; groups=target_groups[$2]; shared=0
        if(groups!="") {
            n=split(groups,g,","); matching=""
            for(i=1;i<=n;i++) if(expected[g[i]]==$2) matching=matching (matching?",":"") g[i]
            if(matching!="") {status="matched"; groups=matching}
            else if(key in first_signature && first_signature[key]!=signature && (previous[key]=="matched" || previous[key]=="previous")) status="previous"
            else status="different"
            shared=(split(groups,g,",")>1)
        }
        print key "\t" $9 "\t" now "\t" (key in first_signature?first_signature[key]:signature) "\t" status "\t" active >samples
        if(status=="unidentified" && delta<65536) next
        if(++count>32) next
        printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%d\t%s\t%s\t%d\t%s\t%s\t%s\n",$1,$2,$3,$4,$5,(groups?groups:"-"),status,rate,$9,activity,shared,$7,$8,$10
        if(previous[key]!=status && status!="unidentified") print (status=="different"?"WARN":"INFO") "\t" (status=="matched"?"DOWNLOAD_MATCHED":status=="previous"?"DOWNLOAD_PREVIOUS_NODE":"DOWNLOAD_TARGET_DIFFERENT") "\t" $1 " → " $2 " groups=" groups >events
    }
    END {for(key in previous) if(!seen_flow[key] && previous[key]!="unidentified") print "INFO\tDOWNLOAD_CONNECTION_ENDED\t" key >events}
    ' "$TMP_DIR/observation/expected.tsv" "$TMP_DIR/observation/targets.tsv" "$TMP_DIR/observation/devices.tsv" "$TMP_DIR/observation/samples.tsv" "$TMP_DIR/observation/connections.tsv" >"$TMP_DIR/observation/flows.tsv"
    [ -f "$TMP_DIR/observation/samples.new" ] || : >"$TMP_DIR/observation/samples.new"
    mv "$TMP_DIR/observation/samples.new" "$TMP_DIR/observation/samples.tsv"
    . /usr/share/libubox/jshn.sh
    json_init
    json_add_int observed_at "$now"; json_add_string configuration_signature "$signature"
    json_add_boolean available "$available"; json_add_boolean enabled 1
    json_add_array devices
    while IFS="$(printf '\t')" read -r source observed; do
        [ -n "$source" ] || continue
        json_add_object ""; json_add_string ip "$source"; json_add_string name "$observed"; json_close_object
    done <"$TMP_DIR/observation/devices.tsv"
    json_close_array
    json_add_array flows
    while IFS="$(printf '\t')" read -r source target sport port proto groups status speed bytes activity shared reply reply_port mark; do
        [ -n "$source" ] || continue
        path=$(observation_path "$source" "$target" "$reply" "$port" "$reply_port" "$mark")
        json_add_object ""
        json_add_string device_ip "$source"; json_add_string target_ip "$target"
        json_add_string groups "$([ "$groups" = - ] || printf '%s' "$groups")"
        json_add_string status "$status"; json_add_string path "$path"
        json_add_string protocol "$proto"; json_add_int target_port "$port"
        json_add_boolean shared_node "$shared"; json_add_string activity "$activity"
        json_add_int observed_at "$now"; json_add_int speed_bps "$speed"
        if [ "$bytes" != - ]; then counters=1; json_add_int bytes_received "$bytes"; fi
        json_close_object
    done <"$TMP_DIR/observation/flows.tsv"
    json_close_array
    json_add_boolean counters_available "$counters"
    json_add_array redirects
    if [ -f "$TMP_DIR/redirect-observation.log" ]; then
        redirect_previous=$(to_int "$(cat "$TMP_DIR/observation/redirect-time" 2>/dev/null)")
        tail -n 100 "$TMP_DIR/redirect-observation.log" | awk -v now="$now" 'NF==4 && now-$1>=0 && now-$1<=120' >"$TMP_DIR/observation/redirects.tsv"
        while read -r redirect_time source redirect_host redirect_target; do
            json_add_object ""; json_add_string device_ip "$source"; json_add_int observed_at "$redirect_time"
            json_add_string host "$redirect_host"; json_add_string target_host "$redirect_target"; json_close_object
            if [ "$redirect_time" -gt "$redirect_previous" ] &&
                awk -F '\t' -v source="$source" '$1==source {found=1} END {exit !found}' "$TMP_DIR/observation/devices.tsv"; then
                record_event INFO HTTP_REDIRECT_OBSERVED "$source $redirect_host → $redirect_target" download || true
            fi
        done <"$TMP_DIR/observation/redirects.tsv"
        tail -n 1 "$TMP_DIR/observation/redirects.tsv" | awk '{print $1}' >"$TMP_DIR/observation/redirect-time.new"
        if [ -s "$TMP_DIR/observation/redirect-time.new" ]; then mv "$TMP_DIR/observation/redirect-time.new" "$TMP_DIR/observation/redirect-time";
        else rm -f "$TMP_DIR/observation/redirect-time.new"; fi
        records_bound "$TMP_DIR/redirect-observation.log" 500 65536
    fi
    json_close_array
    json_dump >"$TMP_DIR/observation.json.new" && mv "$TMP_DIR/observation.json.new" "$TMP_DIR/observation.json"
    while IFS="$(printf '\t')" read -r status observed source; do
        [ -n "$status" ] || continue
        record_event "$status" "$observed" "$source" download || true
    done <"$TMP_DIR/observation/events.tsv"
    records_flush_due || true
    rm -f "$TMP_DIR/observation/connections.raw" "$TMP_DIR/observation/firewall.raw" "$TMP_DIR/observation/nft.raw" "$TMP_DIR/observation/iptables.raw" "$TMP_DIR/observation/ip6tables.raw"
)

observation_document() (
    local signature now observed old="${JSON_PREFIX:-}" source name
    . /usr/share/libubox/jshn.sh
    mkdir -p "$TMP_DIR"
    now=$(date +%s)
    if is_enabled && [ -s "$TMP_DIR/observation.json" ] && json_load "$(cat "$TMP_DIR/observation.json")" 2>/dev/null; then
        json_get_var observed observed_at; json_get_var signature configuration_signature
        json_add_boolean fresh "$( [ $((now-$(to_int "$observed"))) -ge 0 ] && [ $((now-$(to_int "$observed"))) -le 15 ] && [ "$signature" = "$(observation_signature)" ] && echo 1 || echo 0 )"
        json_dump
    else
        json_init
        json_add_boolean enabled "$(is_enabled && echo 1 || echo 0)"
        json_add_boolean available 0; json_add_boolean fresh 0
        json_add_array devices
        observation_devices >"$TMP_DIR/devices-status-$$.tsv"
        while IFS="$(printf '\t')" read -r source name; do
            [ -n "$source" ] || continue
            json_add_object ""; json_add_string ip "$source"; json_add_string name "$name"; json_close_object
        done <"$TMP_DIR/devices-status-$$.tsv"
        rm -f "$TMP_DIR/devices-status-$$.tsv"
        json_close_array
        json_add_array flows; json_close_array; json_add_array redirects; json_close_array
        json_dump
    fi
)

observation_loop() {
    trap 'records_flush_due 1; exit 0' INT TERM
    record_event INFO OBSERVATION_STARTED '下载连接观测已启动' download || true
    while is_enabled; do
        observation_step
        sleep 5 & wait $!
    done
    records_flush_due 1
}
