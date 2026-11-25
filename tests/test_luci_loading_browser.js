'use strict';
// 真实 LuCI 加载冒烟：临时会话、只读页面、一或两次请求超时，退出时清理浏览器与会话。
// node tests/test_luci_loading_browser.js xa-kwrt-r66s http://127.0.0.1:18088 [normal|status|config|reload|comparison|ui]
const assert = require('assert');
const {spawn, execFileSync} = require('child_process');
const fs = require('fs'), os = require('os'), path = require('path'), crypto = require('crypto');

const container = process.argv[2] || 'xa-kwrt-r66s';
const base = process.argv[3] || 'http://127.0.0.1:18088';
const modes = process.argv[4] === 'comparison' ? ['baseline', 'status', 'reload'] :
    process.argv[4] ? [process.argv[4]] : ['normal', 'status', 'config', 'reload'];
assert(/^xa-[a-z0-9-]+$/.test(container), '仅允许 xa- 前缀的测试容器');
assert(/^http:\/\/127\.0\.0\.1:[0-9]+$/.test(base), '仅允许本机测试地址');
assert(modes.every(mode => ['normal', 'status', 'config', 'reload', 'baseline', 'ui'].includes(mode)), '加载场景无效');
const screenshotDir = process.env.XBOX_ACCEL_SCREENSHOTS;
if (screenshotDir) assert(path.isAbsolute(screenshotDir), '截图输出路径应为绝对路径');
const source = fs.readFileSync(path.join(__dirname, '../files/www/luci-static/resources/view/xbox-accel/overview.js'));
const baselineSource = modes.includes('baseline') ? execFileSync('git',
    ['show', 'f7ad0381463a2c9203a10687c99d6ac7c00ef349:files/www/luci-static/resources/view/xbox-accel/overview.js'],
    {cwd:path.join(__dirname, '..')}) : null;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const ubus = (method, data) => {
    const output = execFileSync('docker', ['exec', container, 'ubus', 'call', 'session', method,
        JSON.stringify(data)], {encoding:'utf8', timeout:5000}).trim();
    return output ? JSON.parse(output) : {};
};
let session, profile, child, ws, sid, sequence = 0, guard, mode, injected = 0, reportRequests = 0;
let reportDirectory, reportFile, reportBytes = 0;
let originalConfiguration;
const pending = new Map();
const runtimeErrors = [];

function call(method, params = {}, page = true) {
    return new Promise((resolve, reject) => {
        const id = ++sequence;
        pending.set(id, {resolve, reject});
        ws.send(JSON.stringify({id, method, params, ...(page && sid ? {sessionId:sid} : {})}));
    });
}
async function evaluate(expression) {
    const result = await call('Runtime.evaluate', {expression, returnByValue:true, awaitPromise:true});
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
}
async function until(expression, timeout = 12000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
        const value = await evaluate(expression);
        if (value) return value;
        await pause(100);
    }
    throw new Error('界面冒烟等待超时：' + expression + '\n' + await evaluate('document.body.innerText.slice(-2000)'));
}
function readConfiguration() {
    return JSON.parse(execFileSync('docker', ['exec', container, 'ubus', 'call', 'uci', 'get',
        JSON.stringify({ubus_rpc_session:session, config:'xbox-accel', section:'main'})],
        {encoding:'utf8', timeout:5000})).values;
}
async function screenshot(name) {
    if (!screenshotDir) return;
    fs.mkdirSync(screenshotDir, {recursive:true});
    const result = await call('Page.captureScreenshot', {format:'png', captureBeyondViewport:false});
    fs.writeFileSync(path.join(screenshotDir, name + '.png'), Buffer.from(result.data, 'base64'));
}
async function checkUi() {
    assert.strictEqual(readConfiguration().enabled, '0', '界面保存冒烟使用关闭加速的测试容器');
    const selected = ['192.168.1.20', '192.168.1.21'];
    const fixture = {fresh:true, available:true, devices:[
        {ip:selected[0], name:'Xbox'}, {ip:selected[1], name:'Switch'}, {ip:'192.168.1.22', name:'PC'}
    ], flows:[], redirects:[]};
    const updateDevices = 'L.require("view.xbox-accel.overview").then(view=>{' +
        'view.updateStatus(Object.assign({},view.latestStatus,{observation:' + JSON.stringify(fixture) + '}));' +
        'view.nextStatusPoll=Date.now()+60000;' +
        '})';
    await evaluate(updateDevices);
    await evaluate('L.require("view.xbox-accel.overview").then(view=>view.selectDevices([]))');
    await evaluate('document.querySelector("#xbox-accel-device .cbi-dropdown").click()');
    for (const ip of selected) {
        const selector = '#xbox-accel-device .cbi-dropdown > ul.dropdown > li[data-value="' + ip + '"]';
        await evaluate('document.querySelector(' + JSON.stringify(selector) + ').click()');
    }
    await evaluate('document.querySelector("#xbox-accel-device .cbi-dropdown > span.open").click()');
    assert.deepStrictEqual(await evaluate('L.require("view.xbox-accel.overview").then(view=>view.deviceOption.formvalue("main"))'),
        selected, '下拉多选应同时选中两台设备');
    await evaluate('document.querySelector(".cbi-page-actions .cbi-button-save").click()');
    const saveDeadline = Date.now() + 12000;
    while (Date.now() < saveDeadline && JSON.stringify(readConfiguration().download_devices) !== JSON.stringify(selected))
        await pause(100);
    assert.deepStrictEqual(readConfiguration().download_devices, selected, '原生保存应写入设备选择');
    await evaluate('window.xaUiApplied=0;document.addEventListener("uci-applied",()=>window.xaUiApplied++);' +
        'document.querySelector(' + JSON.stringify('.cbi-page-actions .cbi-button-apply li[data-value="0"]') + ').click()');
    await until('window.xaUiApplied>0', 15000);
    await call('Page.navigate', {url:base + '/cgi-bin/luci/admin/services/xbox-accel?xa_load_smoke=ui'});
    await until('document.querySelector("#xbox-accel-status")?.innerText.includes("当前插件版本")');
    const restored = await evaluate('L.require("view.xbox-accel.overview").then(view=>({' +
        'selected:view.selectedDevices, widget:view.deviceOption.formvalue("main")}))');
    assert.deepStrictEqual(restored.selected, selected, '重新打开应恢复保存的设备');
    assert.deepStrictEqual(restored.widget, selected, '重新打开应恢复下拉框勾选');
    await evaluate(updateDevices);
    await screenshot('overview');
    await evaluate('document.querySelector(".cbi-tabmenu [data-tab=records] a").click()');
    await evaluate('L.require("view.xbox-accel.overview").then(view=>{' +
        'view.updateStatus(Object.assign({},view.latestStatus,{log:Array.from({length:100},(_,i)=>"运行日志 "+i).join("\\n")}));' +
        'view.nextStatusPoll=Date.now()+60000;' +
        '})');
    await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    const layout = await evaluate('(()=>{' +
        'const actions=document.getElementById("xbox-accel-record-actions"),log=document.getElementById("xbox-accel-raw-log"),' +
        'acceptance=document.getElementById("xbox-accel-diagnostics"),window=document.getElementById("xbox-accel-log");' +
        'return {actions_visible:!!actions.getClientRects().length&&!actions.closest("details"),' +
        'log_visible:!!window.getClientRects().length&&!log.closest("details"),acceptance_collapsed:!acceptance.open,' +
        'actions_before_log:!!(actions.compareDocumentPosition(log)&Node.DOCUMENT_POSITION_FOLLOWING),' +
        'log_before_acceptance:!!(log.compareDocumentPosition(acceptance)&Node.DOCUMENT_POSITION_FOLLOWING),' +
        'scrolling:window.scrollHeight>window.clientHeight,' +
        'at_end:window.scrollTop+window.clientHeight>=window.scrollHeight-2};})()');
    assert(Object.values(layout).every(Boolean), '运行记录布局和滚动日志：' + JSON.stringify(layout));
    assert.strictEqual(reportRequests, 0, '默认折叠的验收区只显示摘要');
    await screenshot('records');
    console.log('Device dropdown / Save / Save & Apply / reopen: PASS');
    console.log('Records actions / scrolling log / collapsed acceptance: PASS');
}
function matches(request, target) {
    if (target === 'baseline') target = 'status';
    if (target === 'reload') target = 'config';
    if (target === 'status') return request.params?.[1] === 'xbox-accel' && request.params[2] === 'status';
    return target === 'config' && request.params?.[1] === 'uci' && request.params[2] === 'get' &&
        request.params[3]?.config === 'xbox-accel';
}
async function intercept(params) {
    if ((params.request.postData || '').includes('last-application.json')) reportRequests++;
    if (new URL(params.request.url).pathname.endsWith('/view/xbox-accel/overview.js')) {
        // LuCI 用请求选项里的 timeout 判断超时。注入同时修改该选项与实际 XHR 时限。
        const timeoutPrelude = `
            const originalRequest = request.request;
            request.request = function(target, options) {
                const mode = new URL(location.href).searchParams.get('xa_load_smoke');
                const messages = [].concat(options?.content || []);
                const inject = options?.nobatch && (window.xaTimeoutCount || 0) < (mode === 'reload' ? 2 : 1) &&
                    messages.some(message => (${matches.toString()})(message, mode));
                if (inject) {
                    window.xaTimeoutCount = (window.xaTimeoutCount || 0) + 1;
                    options = Object.assign({}, options, {timeout:300});
                }
                return originalRequest.call(this, target, options).catch(error => {
                    if (inject) (window.xaTimeoutErrors ||= []).push(error.message);
                    throw error;
                });
            };
        `;
        const reportPrelude = mode === 'normal' ? [
            'const originalReadDirect = fs.read_direct;',
            'fs.read_direct = function(file, type) {',
            '  if (file !== "/tmp/xbox-accel/last-application.json") return originalReadDirect.apply(this, arguments);',
            '  const started = performance.now();',
            '  return originalReadDirect.call(this, ' + JSON.stringify(reportFile) + ', type).then(blob => {',
            '    window.xaReportReadMs = performance.now() - started; return blob;',
            '  });',
            '};'
        ].join('\n') : '';
        const originalSource = (mode === 'baseline' ? baselineSource : source).toString();
        assert(originalSource.includes("'require fs';"), '视图的依赖声明应存在');
        const viewSource = Buffer.from(originalSource.replace("'require fs';",
            "'require fs';\n'require request';\n" + timeoutPrelude + reportPrelude));
        await call('Fetch.fulfillRequest', {requestId:params.requestId, responseCode:200,
            responseHeaders:[{name:'Content-Type', value:'application/javascript'}],
            body:viewSource.toString('base64')});
        return;
    }
    let requests;
    try { requests = [].concat(JSON.parse(params.request.postData || 'null') || []); } catch (_) { requests = []; }
    if (injected < (mode === 'reload' ? 2 : 1) && requests.some(request => matches(request, mode))) {
        injected++;
        // 按场景留住一或两次请求，触发真实 XHR 超时；后续请求正常放行。
        return;
    }
    await call('Fetch.continueRequest', {requestId:params.requestId});
}

(async () => {
    if (modes.includes('ui'))
        originalConfiguration = execFileSync('docker', ['exec', container, 'cat', '/etc/config/xbox-accel'], {timeout:5000});
    if (modes.includes('normal')) {
        // 独立报告文件走真实 cgi-io 下载；保留容器现有验收记录。
        reportDirectory = execFileSync('docker', ['exec', container, 'mktemp', '-d',
            '/tmp/xa-luci-report.XXXXXX'], {encoding:'utf8', timeout:5000}).trim();
        assert(/^\/tmp\/xa-luci-report\.[a-zA-Z0-9]+$/.test(reportDirectory), '报告临时路径无效');
        reportFile = reportDirectory + '/last-application.json';
        const report = JSON.stringify({
            checked_at:Math.floor(Date.now() / 1000),
            summary:{router:'verified', passed:192},
            environment:Array.from({length:192}, (_, index) => ({
                name:'fixture ' + index, level:'ok', code:'VERIFIED', scope:'router',
                detail:'x'.repeat(160), expected:'expected', observed:'observed'
            }))
        });
        reportBytes = Buffer.byteLength(report);
        execFileSync('docker', ['exec', '-i', container, 'sh', '-c', 'cat > "$1"', 'fixture-write',
            reportFile], {input:report, stdio:['pipe', 'ignore', 'pipe'], timeout:5000});
    }
    session = ubus('create', {timeout:60}).ubus_rpc_session;
    assert(session, '创建测试会话失败');
    ubus('set', {ubus_rpc_session:session, values:{username:'root', token:crypto.randomBytes(16).toString('hex')}});
    for (const scope of ['ubus', 'uci'])
        ubus('grant', {ubus_rpc_session:session, scope, objects:[['*', '*']]});
    ubus('grant', {ubus_rpc_session:session, scope:'file', objects:[['/tmp/xbox-accel/last-application.json', 'read']]});
    if (reportFile) ubus('grant', {ubus_rpc_session:session, scope:'file', objects:[[reportFile, 'read']]});
    ubus('grant', {ubus_rpc_session:session, scope:'cgi-io', objects:[['download', 'read']]});
    ubus('grant', {ubus_rpc_session:session, scope:'access-group', objects:[
        ['luci-base', 'read'], ['luci-base', 'write'],
        ['luci-app-xbox-accel', 'read'], ['luci-app-xbox-accel', 'write']
    ]});
    profile = fs.mkdtempSync(path.join(os.tmpdir(), 'xa-luci-loading-'));
    child = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', [
        '--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + profile,
        '--no-first-run', '--no-default-browser-check', '--disable-background-networking', '--no-proxy-server', 'about:blank'
    ], {stdio:['ignore', 'ignore', 'pipe']});
    guard = setTimeout(() => {
        for (const waiter of pending.values()) waiter.reject(new Error('加载冒烟超时'));
        pending.clear(); child.kill('SIGTERM');
    }, 40000);
    const endpoint = await new Promise((resolve, reject) => {
        let buffer = '';
        child.stderr.on('data', chunk => {
            buffer += chunk;
            const match = buffer.match(/DevTools listening on (ws:\/\/[^\s]+)/);
            if (match) resolve(match[1]);
        });
        child.on('error', reject);
        child.once('exit', (code, signal) => reject(new Error('Chrome 启动时退出：' + (signal || code) + '\n' + buffer.slice(-1500))));
    });
    ws = new WebSocket(endpoint);
    await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
    ws.onmessage = event => {
        const message = JSON.parse(event.data), waiter = pending.get(message.id);
        if (waiter) {
            pending.delete(message.id);
            message.error ? waiter.reject(new Error(message.error.message)) : waiter.resolve(message.result);
        } else if (message.method === 'Fetch.requestPaused') {
            intercept(message.params).catch(error => { console.error(error.message); process.exitCode = 1; });
        } else if (message.method === 'Runtime.exceptionThrown') {
            runtimeErrors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
        }
    };
    ws.onclose = () => {
        for (const waiter of pending.values()) waiter.reject(new Error('浏览器连接已关闭'));
        pending.clear();
    };
    const target = await call('Target.createTarget', {url:'about:blank'}, false);
    sid = (await call('Target.attachToTarget', {targetId:target.targetId, flatten:true}, false)).sessionId;
    await call('Page.enable'); await call('Runtime.enable'); await call('Network.enable');
    if (modes.includes('ui')) await call('Emulation.setDeviceMetricsOverride',
        {width:1440, height:1100, deviceScaleFactor:1, mobile:false});
    await call('Network.setCacheDisabled', {cacheDisabled:true});
    await call('Network.setCookie', {name:'sysauth_http', value:session, url:base, path:'/'});
    await call('Fetch.enable', {patterns:[{urlPattern:'*', requestStage:'Request'}]});
    for (mode of modes) {
        injected = 0;
        reportRequests = 0;
        const start = Date.now();
        await call('Page.navigate', {url:base + '/cgi-bin/luci/admin/services/xbox-accel?xa_load_smoke=' + mode});
        const deadline = Date.now() + 12000;
        let outcome;
        while (Date.now() < deadline) {
            outcome = await evaluate(`({ready:!!document.querySelector('#xbox-accel-status'),
                failure:document.querySelector('#xbox-accel-load-error')?.innerText,
                status:document.querySelector('#xbox-accel-status')?.innerText,
                tabs:document.querySelectorAll('.cbi-tabmenu a').length})`);
            if (outcome.failure || (outcome.ready && outcome.status.includes('当前插件版本'))) break;
            await pause(100);
        }
        if (mode === 'baseline') {
            assert(outcome.failure?.includes('XHR request timed out'),
                '旧版应在同一状态超时下进入失败页：' + JSON.stringify(outcome) + '\n' +
                await evaluate('JSON.stringify({count:window.xaTimeoutCount,errors:window.xaTimeoutErrors,body:document.body.innerText.slice(0,1000)})') +
                '\n' + runtimeErrors.join('\n'));
            assert((await evaluate('window.xaTimeoutErrors || []')).includes('XHR request timed out'),
                'LuCI 应明确判定为请求超时');
            console.log('baseline status timeout: REPRODUCED');
            continue;
        }
        if (mode === 'reload') {
            assert(outcome.failure?.includes('XHR request timed out') && injected === 2,
                '连续两次配置超时应显示失败页');
            await evaluate('document.getElementById("xbox-accel-reload").click()');
            while (Date.now() < deadline) {
                outcome = await evaluate(`({ready:!!document.querySelector('#xbox-accel-status'),
                    failure:document.querySelector('#xbox-accel-load-error')?.innerText,
                    status:document.querySelector('#xbox-accel-status')?.innerText,
                    tabs:document.querySelectorAll('.cbi-tabmenu a').length})`);
                if (outcome.ready && outcome.status.includes('当前插件版本')) break;
                await pause(100);
            }
        }
        assert(!outcome.failure, mode + '：' + outcome.failure);
        assert(outcome.ready && outcome.tabs === 4 && outcome.status.includes('当前插件版本'),
            mode + '：页面及状态应恢复；' + JSON.stringify(outcome) + '\n' + await evaluate('document.body.innerText.slice(0,1000)'));
        assert(['normal', 'ui'].includes(mode) || injected, mode + '：应注入一次真实超时');
        if (!['normal', 'ui'].includes(mode)) assert((await evaluate('window.xaTimeoutErrors || []')).includes('XHR request timed out'),
            mode + '：应验证 LuCI 的请求超时处理');
        if (mode === 'ui') await checkUi();
        if (mode === 'normal') {
            assert.strictEqual(reportRequests, 0, '总览显示验收摘要');
            const available = await evaluate('L.require("view.xbox-accel.overview").then(view=>!!view.latestStatus.last_application?.available)');
            await evaluate('document.querySelector(".cbi-tabmenu [data-tab=records] a").click()');
            assert.strictEqual(reportRequests, 0, '验收区收起时保留摘要');
            assert(available, '验收详情冒烟需要已有验收摘要');
            await evaluate('L.require("view.xbox-accel.overview").then(view=>{' +
                'const original = view.updateEnvironment;' +
                'view.updateEnvironment = function(result) {' +
                '  const started = performance.now(); const returned = original.apply(this, arguments);' +
                '  if (result.environment?.length === 192) {' +
                '    window.xaReportRenderMs = performance.now() - started;' +
                '    requestAnimationFrame(()=>requestAnimationFrame(()=>{' +
                '      window.xaReportDisplayMs = performance.now() - window.xaReportStarted;' +
                '    }));' +
                '  } return returned;' +
                '};' +
                'window.xaReportStarted = performance.now();' +
                'document.getElementById("xbox-accel-diagnostics").open = true;' +
                '})');
            while (Date.now() < deadline && !(await evaluate('window.xaReportDisplayMs')))
                await pause(100);
            assert.strictEqual(reportRequests, 1, '展开验收区后读取一次完整报告');
            assert(await evaluate('document.getElementById("xbox-accel-diagnostics").innerText.includes("完整验收记录于")'),
                '完整验收报告应显示：' + await evaluate('document.getElementById("xbox-accel-diagnostics").innerText.slice(0,1000)'));
            const timing = await evaluate('L.require("view.xbox-accel.overview").then(view=>({' +
                'rows:view.envNode.querySelectorAll("tr").length,' +
                'read_ms:window.xaReportReadMs, render_ms:window.xaReportRenderMs, display_ms:window.xaReportDisplayMs}))');
            assert.strictEqual(timing.rows, 192, '完整显示全部验收条目');
            assert(timing.display_ms < 3000, '详情读取和显示应在三秒内完成：' + JSON.stringify(timing));
            console.log('Application report on disclosure: PASS ' + JSON.stringify({bytes:reportBytes, ...timing}));
        }
        console.log(mode + ' loading: PASS (' + (Date.now() - start) + ' ms)');
    }
})().catch(error => { console.error(error.message); process.exitCode = 1; }).finally(async () => {
    clearTimeout(guard);
    if (ws?.readyState === 1) {
        try { await Promise.race([call('Browser.close', {}, false), pause(1000)]); } catch (_) {}
        ws.close();
    }
    if (child && child.exitCode === null && child.signalCode === null) {
        child.kill('SIGTERM');
        await Promise.race([new Promise(resolve => child.once('exit', resolve)), pause(1000)]);
        if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    }
    try {
        if (originalConfiguration) execFileSync('docker', ['exec', '-i', container, 'sh', '-c',
            'cat > /etc/config/xbox-accel'], {input:originalConfiguration, timeout:5000});
    } finally {
        try { if (session) ubus('destroy', {ubus_rpc_session:session}); }
        finally {
            try { if (profile) fs.rmSync(profile, {recursive:true, force:true}); }
            finally {
                if (reportDirectory) execFileSync('docker', ['exec', container, 'rm', '-rf', reportDirectory],
                    {timeout:5000});
            }
        }
    }
});
