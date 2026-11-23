#!/bin/sh
# 平台域名与测速资源。来源与适配边界见 docs/PLATFORMS.md。
ALL_GROUPS='cn com store ps xbox_old switch'
PLATFORM_GROUPS='store ps xbox_old switch'
STORE_DOMAINS='dl.delivery.mp.microsoft.com tlu.dl.delivery.mp.microsoft.com 1d.tlu.dl.delivery.mp.microsoft.com'
PS_DOMAINS='gst.prod.dl.playstation.net gs2.ww.prod.dl.playstation.net zeus.dl.playstation.net ares.dl.playstation.net'
XBOX_OLD_DOMAINS='dlassets.xboxlive.cn dlassets2.xboxlive.cn'
SWITCH_TEST_DOMAINS='ctest-dl-lp1.cdn.nintendo.net ctest-dl.p01.lp1.ctest.srv.nintendo.net'
SWITCH_GAME_DOMAINS='atum.hac.lp1.d4c.nintendo.net nemof.p01.lp1.nemo.srv.nintendo.net nemof.hac.lp1.nemo.srv.nintendo.net'
PLATFORM_DOMAINS="$STORE_DOMAINS $PS_DOMAINS $XBOX_OLD_DOMAINS $SWITCH_TEST_DOMAINS $SWITCH_GAME_DOMAINS"
DEF_URL_PS='http://gst.prod.dl.playstation.net/networktest/get_192m'
DEF_URL_PS_GS2='http://gs2.ww.prod.dl.playstation.net/gs2/appkgo/prod/CUSA03962_00/4/f_526a2fab32d369a8ca6298b59686bf823fa9edfe95acb85bc140c27f810842ce/f/UP0102-CUSA03962_00-BH70000000000001_0.pkg'
DEF_URL_PS_ZEUS='http://zeus.dl.playstation.net/cdn/UP1004/NPUB31154_00/eISFknCNDxqSsVVywSenkJdhzOIfZjrqKHcuGBHEGvUxQJksdPvRNYbIyWcxFsvH.pkg'
DEF_URL_XBOX_OLD='http://dlassets.xboxlive.cn/public/content/0044aef3-bd7e-4478-8782-6191546dcd9a/f6585e19-765c-49c3-ba77-326c3766e05a/1.0.4.0.60281fce-b9c0-49f6-a2b6-027abad22637/ACBF_1.0.4.0_x64__b6krnev7r9sf8'
DEF_URL_SWITCH='http://ctest-dl-lp1.cdn.nintendo.net/30m'

group_valid() { case "$1" in cn|com|store|ps|xbox_old|switch) return 0 ;; *) return 1 ;; esac; }

group_label() {
    case "$1" in
    cn) echo 'Xbox 国内' ;; com) echo 'Xbox 国际' ;; store) echo '微软商店' ;;
    ps) echo 'PlayStation' ;; xbox_old) echo 'Xbox 老游戏下载' ;; switch) echo 'Switch' ;;
    esac
}

group_domains() {
    case "$1" in
    cn) echo "$CN_DOMAINS" ;; com) echo "$COM_DOMAINS" ;; store) echo "$STORE_DOMAINS" ;;
    ps) echo "$PS_DOMAINS" ;; xbox_old) echo "$XBOX_OLD_DOMAINS" ;;
    switch) echo "$SWITCH_TEST_DOMAINS $SWITCH_GAME_DOMAINS" ;;
    esac
}

group_user_agent() { [ "$1" = switch ] && echo 'XboxDownload/Nintendo NX'; return 0; }

# URL 仅接受已知平台域名、默认 HTTP(S) 端口和资源路径。
platform_url_valid() {
    local group=$1 url=$2 host cleaned
    [ "${#url}" -le 4096 ] && [ -n "$url" ] || return 1
    cleaned=$(printf '%s' "$url" | tr -d '\000-\040\177')
    [ "$cleaned" = "$url" ] || return 1
    printf '%s\n' "$url" | LC_ALL=C grep -qE '^https?://[a-z0-9][a-z0-9.-]*/[^[:space:]#\\]+$' || return 1
    host=$(url_host "$url")
    case " $(group_domains "$group") " in *" $host "*) return 0 ;; esac
    return 1
}

platform_url() {
    case "$1" in
    store)
        if [ -n "$(cfg test_url_store '')" ]; then cfg test_url_store ''; else store_cached_url; fi ;;
    ps) cfg test_url_ps "$DEF_URL_PS" ;;
    xbox_old) cfg test_url_xbox_old "$DEF_URL_XBOX_OLD" ;;
    switch) cfg test_url_switch "$DEF_URL_SWITCH" ;;
    esac
}

configured_domains() {
    printf '%s\n' $(group_domains "$1") | uniq_keep_order
}

# 每个下载域名使用自己的校验路径；自定义链接按域名覆盖默认资源。
domain_url() {
    local group=$1 host=$2 primary=${3:-} url
    [ -n "$primary" ] || primary=$(group_url "$group")
    case "$group" in
    ps|switch)
        for url in $(cfg "verification_urls_$group" ''); do
            platform_url_valid "$group" "$url" && [ "$(url_host "$url")" = "$host" ] && { echo "$url"; return 0; }
        done ;;
    esac
    if [ -n "$primary" ] && [ "$(url_host "$primary")" = "$host" ]; then echo "$primary"; return 0; fi
    case "$group:$host" in
    cn:*|com:*|store:*|xbox_old:*) [ -n "$primary" ] && printf '%s://%s%s\n' "$(url_scheme "$primary")" "$host" "$(url_path "$primary")" ;;
    ps:gst.prod.dl.playstation.net) echo "$DEF_URL_PS" ;;
    ps:gs2.ww.prod.dl.playstation.net) echo "$DEF_URL_PS_GS2" ;;
    ps:zeus.dl.playstation.net) echo "$DEF_URL_PS_ZEUS" ;;
    switch:ctest-dl-lp1.cdn.nintendo.net|switch:ctest-dl.p01.lp1.ctest.srv.nintendo.net)
        printf '%s://%s%s\n' "$(url_scheme "$primary")" "$host" "$(url_path "$primary")" ;;
    *) return 1 ;;
    esac
}

domain_offset() {
    local group=$1 host=$2
    case "$group" in ps|switch)
        [ "$host" = "$(url_host "$(group_url "$group")")" ] || { echo 0; return; } ;;
    esac
    group_offset "$group"
}

discovery_hosts() {
    local group=$1 url
    case "$group" in
    cn) echo assets1.xboxlive.cn ;;
    com) echo "assets1.xboxlive.com $CDN_HOSTS" ;;
    *)
        url=$(group_url "$group")
        {
            [ -n "$url" ] && url_host "$url"
            configured_domains "$group"
        } | uniq_keep_order | tr '\n' ' ' ;;
    esac
}

resource_hash() {
    if command -v sha256sum >/dev/null 2>&1; then printf '%s' "$1" | sha256sum | awk '{print "sha256:" $1}';
    elif command -v md5sum >/dev/null 2>&1; then printf '%s' "$1" | md5sum | awk '{print "md5:" $1}';
    else return 1; fi
}

# 游戏链接的完整摘要参与接管条件；变更路径或签名后必须重新校验。
switch_game_verified() {
    local host=$1 url signature
    url=$(domain_url switch "$host") || return 1
    signature=$(resource_hash "$url") || return 1
    grep -qxF "$host $signature" "$STATE_DIR/resources_switch" 2>/dev/null
}

# 已验证公开主资源的旧结果也可直接补齐默认游戏下载映射。
switch_public_ready() {
    local host
    best_ip switch >/dev/null || return 1
    host=$(url_host "$(group_url switch)")
    case " $SWITCH_TEST_DOMAINS " in *" $host "*) ;; *) return 1 ;; esac
    printf '%s\n' $(best_domains switch) | grep -qxF "$host"
}

pinned_domains() {
    local group=$1 host configured domains
    configured=$(configured_domains "$group" | tr '\n' ' ')
    domains=$(best_domains "$group")
    if [ "$group" = switch ]; then
        switch_public_ready || return 0
        for host in $SWITCH_GAME_DOMAINS; do
            domain_url switch "$host" >/dev/null || domains="$domains $host"
        done
    fi
    for host in $(printf '%s\n' $domains | uniq_keep_order); do
        case " $configured " in *" $host "*) ;; *) continue ;; esac
        if [ "$group" = switch ]; then
            case " $SWITCH_GAME_DOMAINS " in *" $host "*)
                if domain_url switch "$host" >/dev/null; then switch_game_verified "$host" || continue; fi ;;
            esac
        fi
        printf '%s\n' "$host"
    done
}

group_result_ready() {
    local group=$1 host
    best_ip "$group" >/dev/null || return 1
    if [ "$group" = switch ]; then
        switch_public_ready || return 1
        for host in $SWITCH_GAME_DOMAINS; do
            domain_url switch "$host" >/dev/null || continue
            pinned_domains switch | grep -qxF "$host" || return 1
        done
    fi
    return 0
}

selected_ips() {
    local g
    for g in $(groups_active); do best_ip "$g"; done
    [ "$(com_mode)" = redirect ] && redirect_ip
    return 0
}

probe_resource() {
    probe "$1" "$(url_host "$2")" "$(url_path "$2")" "$3" "$4" "$5" "$(url_scheme "$2")" "$(group_user_agent "$6")"
}

alive_resource() {
    alive "$1" "$(url_host "$2")" "$(url_path "$2")" "$3" "$(url_scheme "$2")" "$(group_user_agent "$4")"
}

group_alive() {
    local group=$1 ip=$2 url host
    url=$(group_url "$group")
    alive_resource "$ip" "$url" "$(group_offset "$group")" "$group" || return 1
    if [ "$group" = switch ]; then
        for host in $(pinned_domains switch); do
            case " $SWITCH_GAME_DOMAINS " in *" $host "*)
                url=$(domain_url switch "$host") || continue
                alive_resource "$ip" "$url" 0 switch || return 1 ;;
            esac
        done
    fi
    return 0
}

store_url_expiry() {
    printf '%s\n' "$1" | awk '
        {n=split($0,a,/[?&]/); found=0; for(i=2;i<=n;i++) if(a[i]~/^P1=/) {
            sub(/^P1=/,"",a[i]); if(a[i]!~/^[0-9]+$/ || length(a[i])>12 || ++found>1) exit 1; expiry=a[i]
        } print found ? expiry : 0}'
}

store_link_valid() {
    local url=$1 size=$2 expiry
    platform_url_valid store "$url" || return 1
    printf '%s\n' "$size" | grep -qE '^[0-9]{1,12}$' || return 1
    [ "$size" -ge 1048576 ] && [ "$size" -le 549755813888 ] || return 1
    expiry=$(store_url_expiry "$url") || return 1
    [ "$expiry" -gt $(($(date +%s)+300)) ] || return 1
    printf '%s\n' "$url" | grep -qE '[?&]P4=[^&]+'
}

# jshn 在子进程中解析，保护正在生成的状态/验收 JSON 命名空间。
store_cached_url() (
    local url size app expiry
    . /usr/share/libubox/jshn.sh
    [ -s "$TMP_DIR/store-resource.json" ] || return 1
    jshn -R "$TMP_DIR/store-resource.json" >/dev/null 2>&1 || return 1
    json_load "$(cat "$TMP_DIR/store-resource.json")" || return 1
    json_get_var url url; json_get_var size size; json_get_var app app; json_get_var expiry expires_at
    [ "$app" = "$(cfg store_test_app store)" ] && [ "$expiry" = "$(store_url_expiry "$url")" ] || return 1
    store_link_valid "$url" "$size" || return 1
    printf '%s\n' "$url"
)

store_resource_prepare() (
    local app guid raw url size expiry code type keys key found=0 temporary manual
    manual=$(cfg test_url_store '')
    if [ -n "$manual" ]; then
        platform_url_valid store "$manual" || { echo STORE_LINK_INVALID; return 1; }
        expiry=$(store_url_expiry "$manual") || { echo STORE_LINK_INVALID; return 1; }
        if [ "$expiry" -gt 0 ] && [ "$expiry" -le $(($(date +%s)+300)) ]; then echo STORE_LINK_EXPIRED; return 1; fi
        echo RESOURCE_READY; return 0
    fi
    if [ "${1:-}" != force ] && store_cached_url >/dev/null; then echo RESOURCE_READY; return 0; fi
    app=$(cfg store_test_app store)
    case "$app" in
    store) guid=64293252-5926-453c-9494-2d4021f1c78d ;;
    xbox) guid=986a47b3-0085-4c0c-b3b3-3b806f969b00 ;;
    *) echo STORE_LINK_INVALID; return 1 ;;
    esac
    umask 077
    mkdir -p "$TMP_DIR" || { echo RESOURCE_CACHE_FAILED; return 1; }
    raw=$(mktemp "$TMP_DIR/store-fetch.XXXXXX") || { echo RESOURCE_CACHE_FAILED; return 1; }
    if ! curl -f -sS --globoff --noproxy '*' --connect-timeout 5 -m 20 --max-filesize 262144 \
        -A XboxDownload -H 'X-Organization: XboxDownload' -H 'X-Author: Devil' \
        -o "$raw" "https://xbox.skydevil.xyz/Game/GetAppPackage?WuCategoryId=$guid" 2>"$raw.err"; then
        task_event WARN STORE_LINK_FETCH_FAILED "商店资源接口请求失败：$(head -c 250 "$raw.err")"
        rm -f "$raw" "$raw.err"; echo STORE_LINK_FETCH_FAILED; return 1
    fi
    . /usr/share/libubox/jshn.sh
    if ! jshn -R "$raw" >/dev/null 2>&1 || ! json_load "$(cat "$raw")"; then
        rm -f "$raw" "$raw.err"; echo STORE_LINK_INVALID; return 1
    fi
    rm -f "$raw" "$raw.err"
    json_get_var code code
    json_get_type type data
    [ "$code" = 200 ] && [ "$type" = array ] && json_select data || { echo STORE_LINK_INVALID; return 1; }
    json_get_keys keys
    for key in $keys; do
        json_select "$key" || continue
        json_get_var url url; json_get_var size size
        if store_link_valid "$url" "$size"; then found=1; json_select ..; break; fi
        json_select ..
    done
    [ "$found" = 1 ] || { echo STORE_LINK_INVALID; return 1; }
    expiry=$(store_url_expiry "$url")
    temporary="$TMP_DIR/store-resource.json.new.$$"
    json_init; json_add_string app "$app"; json_add_string url "$url"
    json_add_int size "$size"; json_add_int expires_at "$expiry"; json_add_int fetched_at "$(date +%s)"
    if ! json_dump >"$temporary" || ! chmod 600 "$temporary" || ! mv "$temporary" "$TMP_DIR/store-resource.json"; then
        rm -f "$temporary"; echo RESOURCE_CACHE_FAILED; return 1
    fi
    task_event INFO STORE_LINK_REFRESHED "app=$app expires_at=$expiry；商店资源链接已刷新"
    echo RESOURCE_READY
)

prepare_group_resource() {
    GROUP_RESOURCE_CODE=RESOURCE_READY; GROUP_RESOURCE_MESSAGE='测速资源已就绪'
    if [ "$1" = store ]; then
        if GROUP_RESOURCE_CODE=$(store_resource_prepare "${2:-}"); then return 0; fi
        case "$GROUP_RESOURCE_CODE" in
        STORE_LINK_FETCH_FAILED) GROUP_RESOURCE_MESSAGE='商店测速链接获取失败；当前节点和 DNS 配置保留' ;;
        STORE_LINK_EXPIRED) GROUP_RESOURCE_MESSAGE='手动商店链接已过期或临近过期；更新链接后重新测速' ;;
        RESOURCE_CACHE_FAILED) GROUP_RESOURCE_MESSAGE='商店资源缓存写入失败；检查内存目录空间与权限' ;;
        *) GROUP_RESOURCE_CODE=STORE_LINK_INVALID; GROUP_RESOURCE_MESSAGE='商店资源接口返回的域名、包大小或签名有效期未通过校验；当前节点保留' ;;
        esac
        return 1
    fi
    return 0
}

platform_config_keys() {
    echo 'optimize_store optimize_ps optimize_xbox_old optimize_switch store_test_app test_url_store test_url_ps test_url_xbox_old test_url_switch test_offset_store test_offset_ps test_offset_xbox_old test_offset_switch verification_urls_ps verification_urls_switch extra_ips_store extra_ips_ps extra_ips_xbox_old extra_ips_switch'
}

validate_platform_config() {
    local g url key count host
    case "$(cfg store_test_app store)" in store|xbox) ;; *) die '商店测速应用请选择 store 或 xbox' ;; esac
    for g in $PLATFORM_GROUPS; do
        case "$(cfg "optimize_$g" 0)" in 0|1) ;; *) die "optimize_$g 必须是 0 或 1" ;; esac
        url=$(cfg "test_url_$g" '')
        [ -z "$url" ] || platform_url_valid "$g" "$url" || die "$(group_label "$g") 测速链接必须使用该平台已知下载域名和 HTTP(S) 资源路径"
        if [ "$g" = switch ] && [ -n "$url" ]; then
            host=$(url_host "$url")
            case " $SWITCH_TEST_DOMAINS " in *" $host "*) ;; *) die 'Switch 公开测速链接须使用 ctest 下载域名；游戏链接填写在独立校验列表' ;; esac
        fi
        key=$(cfg "test_offset_$g" 0)
        printf '%s\n' "$key" | grep -qE '^[0-9]{1,12}$' || die "test_offset_$g 必须是 0..999999999999 的整数"
        for url in $(cfg "extra_ips_$g" ''); do
            [ "$(printf '%s' "$url" | ipv4s | public_only)" = "$url" ] || die "extra_ips_$g 只允许公网 IPv4"
        done
    done
    for g in ps switch; do
        count=0
        for url in $(cfg "verification_urls_$g" ''); do
            count=$((count+1)); [ "$count" -le 8 ] || die "verification_urls_$g 最多配置 8 条链接"
            platform_url_valid "$g" "$url" || die "$(group_label "$g") 校验链接含未知域名或无效资源路径"
            if [ "$g" = switch ]; then
                host=$(url_host "$url")
                case " $SWITCH_GAME_DOMAINS " in *" $host "*) ;; *) die 'Switch 游戏校验链接须使用 atum 或 nemof 下载域名' ;; esac
                resource_hash "$url" >/dev/null || die 'Switch 游戏链接校验需要 sha256sum 或 md5sum'
            fi
        done
    done
}

# 全部新文件先落盘，提交失败时按组恢复原节点、域名与校验摘要。
commit_group_result() {
    local group=$1 ip=$2 domains=$3 transaction="$STATE_DIR/result-$1-$$" file host url signature failed=0 restore_failed=0
    mkdir -p "$STATE_DIR" "$transaction/old" "$transaction/new" || return 1
    for file in "best_$group" "domains_$group" "resources_$group"; do
        if [ -e "$STATE_DIR/$file" ]; then cp "$STATE_DIR/$file" "$transaction/old/$file" || { rm -rf "$transaction"; return 1; }; fi
    done
    printf '%s\n' "$ip" >"$transaction/new/best_$group" && printf '%s\n' "$domains" >"$transaction/new/domains_$group" || { rm -rf "$transaction"; return 1; }
    : >"$transaction/new/resources_$group" || { rm -rf "$transaction"; return 1; }
    if [ "$group" = switch ]; then
        for host in $domains; do
            case " $SWITCH_GAME_DOMAINS " in *" $host "*)
                url=$(domain_url switch "$host") || continue
                signature=$(resource_hash "$url") &&
                    printf '%s %s\n' "$host" "$signature" >>"$transaction/new/resources_$group" || { rm -rf "$transaction"; return 1; } ;;
            esac
        done
    fi
    for file in "resources_$group" "domains_$group" "best_$group"; do
        mv "$transaction/new/$file" "$STATE_DIR/$file" || { failed=1; break; }
    done
    if [ "$failed" = 1 ]; then
        for file in "resources_$group" "domains_$group" "best_$group"; do
            if [ -f "$transaction/old/$file" ]; then cp "$transaction/old/$file" "$STATE_DIR/$file" || restore_failed=1;
            else rm -f "$STATE_DIR/$file" || restore_failed=1; fi
        done
    fi
    if [ "$restore_failed" = 1 ]; then
        TASK_CODE=STATE_ROLLBACK_FAILED
        die "原分组备份保留在 $transaction/old；存储恢复失败，已停止后续应用"
    fi
    rm -rf "$transaction"
    [ "$failed" = 0 ]
}
