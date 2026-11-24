'use strict';
// Real Chrome / real LuCI workflow test against an idle disposable OpenWrt container.
// Usage: node tests/test_browser.js xa-test http://127.0.0.1:18080
// Requires macOS Chrome, Node 24+, Python 3, and an empty root password.
const {spawn, execFileSync} = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert');

const container = process.argv[2] || 'xa-test';
const base = process.argv[3] || 'http://127.0.0.1:18080';
assert(/^http:\/\/127\.0\.0\.1:[0-9]+$/.test(base), 'Only a localhost test URL is allowed');
const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const fixtureScript = path.join(__dirname, 'workflow_fixture.py');
const screenshotDir = process.env.XBOX_ACCEL_SCREENSHOTS || fs.mkdtempSync(path.join(os.tmpdir(), 'xbox-accel-browser-shots-'));
assert(path.isAbsolute(screenshotDir), 'Screenshot directory must be absolute');
fs.mkdirSync(screenshotDir, {recursive:true});
let profile, fixtureWork, child, ws, sid, sequence=0, timer, defaultContext, loginContext;
const pending = new Map();
const errors = [];
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const quote = JSON.stringify;
const docker = (...args) => execFileSync('docker', ['exec', container, ...args], {encoding:'utf8'});
const status = () => JSON.parse(docker('ubus', 'call', 'xbox-accel', 'status'));
const option = name => docker('uci', '-q', 'get', 'xbox-accel.main.' + name).trim();

function rawCall(method, params={}, page=true) {
    const id = ++sequence;
    return new Promise((resolve, reject) => {
        pending.set(id, {resolve, reject, method});
        ws.send(JSON.stringify({id, method, params, ...(page && sid ? {sessionId:sid} : {})}));
    });
}
async function call(method, params={}, page=true, retry=0) {
    try { return await rawCall(method, params, page); }
    catch (error) {
        if (page && retry < 15 && /Not attached to an active page|Execution context was destroyed/.test(error.message)) {
            await pause(200);
            return call(method, params, page, retry+1);
        }
        throw error;
    }
}
async function evaluate(expression) {
    const result = await call('Runtime.evaluate', {expression, awaitPromise:true, returnByValue:true});
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
}
async function until(expression, timeout=40000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
        if (await evaluate(expression)) return;
        await pause(300);
    }
    throw new Error('DOM condition timed out: ' + expression + '\n' + await evaluate('document.body.innerText'));
}
async function selectTab(label) {
    const name = await evaluate('(()=>{const a=Array.from(document.querySelectorAll(".cbi-tabmenu a")).find(a=>a.textContent.trim()===' +
        quote(label) + ');a.click();return a.parentElement.getAttribute("data-tab");})()');
    await until('(()=>{const p=document.querySelector("div[data-tab=\\""+' + quote(name) +
        '+"\\"]");return p.getAttribute("data-tab-active")==="true" && Number(getComputedStyle(p).opacity)>0.99;})()');
}
async function selectValue(name, value) {
    await evaluate('(()=>{const f=document.querySelector("[data-name=\\""+' + quote(name) +
        '+"\\"]");const s=f.querySelector("select");if(s){s.value=' + quote(value) +
        ';s.dispatchEvent(new Event("change",{bubbles:true}));}else{const d=f.querySelector(".cbi-dropdown");(d.querySelector(".open")||d).click();Array.from(d.querySelectorAll("li[data-value]")).find(li=>li.getAttribute("data-value")===' +
        quote(value) + ').click();}})()');
}
async function setInput(name, value) {
    await evaluate('(()=>{const i=document.querySelector("[data-name=\\""+' + quote(name) +
        '+"\\"] input[type=text]");i.value=' + quote(value) + ';i.dispatchEvent(new Event("change",{bubbles:true}));})()');
}
async function openDetails(id) {
    await evaluate('document.getElementById(' + quote(id) + ').open=true');
}
async function click(label, scope='document') {
    await until('Array.from(' + scope + '.querySelectorAll("button")).some(b=>b.textContent.trim()===' + quote(label) + ' && !b.disabled)');
    await evaluate('Array.from(' + scope + '.querySelectorAll("button")).find(b=>b.textContent.trim()===' + quote(label) + ').click()');
}
function measurementRow(label) {
    return 'Array.from(document.querySelectorAll("#xbox-accel-measurements tr")).find(r=>r.firstElementChild.textContent.trim()===' + quote(label) + ')';
}
async function waitTask(previousId, kind, timeout=60000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
        const current = status(), task = current.last_task || {};
        if (task.id && task.id !== previousId && (!kind || task.kind === kind) && !current.testing &&
            !['queued','running','none'].includes(task.status)) return current;
        await pause(300);
    }
    throw new Error('Task did not finish: ' + JSON.stringify(status().last_task));
}
function runtimeSignature() {
    return docker('sh', '-c', 'sha256sum /etc/config/xbox-accel /etc/config/dhcp; ' +
        'for group in cn com store ps xbox_old switch; do for kind in best domains resources; do ' +
        'file="/etc/xbox-accel/${kind}_$group"; [ ! -f "$file" ] || sha256sum "$file"; done; done');
}
async function reload() {
    await call('Page.reload', {ignoreCache:true});
    await until('document.querySelector("#xbox-accel-log") && document.querySelectorAll(".cbi-tabmenu a").length===4');
}
async function screenshot(name) {
    const result = await call('Page.captureScreenshot', {format:'png', captureBeyondViewport:false});
    const output = path.join(screenshotDir, name);
    fs.writeFileSync(output, Buffer.from(result.data, 'base64'));
    if (process.env.XBOX_ACCEL_SCREENSHOTS) console.log(output);
}
function applicationErrors() {
    return errors.filter(error => !(error.executionContextId === loginContext &&
        /RPC call to uci\/get failed.*Access denied/.test(error.exception?.description || '')));
}

(async () => {
    fixtureWork = execFileSync('python3', [fixtureScript, container, 'setup'],
        {encoding:'utf8', env:{...process.env, PYTHONDONTWRITEBYTECODE:'1'}}).trim();
    const session = JSON.parse(docker('ubus','call','session','login','{"username":"root","password":""}')).ubus_rpc_session;
    assert(session, 'Could not create test session');
    profile = fs.mkdtempSync(path.join(os.tmpdir(), 'xbox-accel-browser-'));
    child = spawn(chrome, ['--headless=new','--remote-debugging-port=0','--user-data-dir='+profile,
        '--no-first-run','--no-default-browser-check','--disable-background-networking',
        '--disable-component-update','--disable-sync','--no-proxy-server','about:blank'],
        {stdio:['ignore','ignore','pipe']});
    timer = setTimeout(() => {
        child.kill('SIGTERM');
        for (const value of pending.values()) value.reject(new Error('Browser workflow timed out'));
        pending.clear();
    }, 360000);
    const endpoint = await new Promise((resolve,reject) => {
        let buffer = '';
        child.stderr.on('data', chunk => {
            buffer += chunk;
            const match = buffer.match(/DevTools listening on (ws:\/\/[^\s]+)/);
            if (match) resolve(match[1]);
        });
        child.on('error', reject);
        child.on('exit', (code,signal) => reject(new Error('Chrome exited '+(signal || code)+'\n'+buffer)));
    });
    ws = new WebSocket(endpoint);
    await new Promise((resolve,reject) => { ws.onopen=resolve; ws.onerror=reject; });
    ws.onmessage = event => {
        const message = JSON.parse(event.data);
        if (message.id && pending.has(message.id)) {
            const waiter = pending.get(message.id);
            pending.delete(message.id);
            message.error ? waiter.reject(new Error(waiter.method+': '+JSON.stringify(message.error))) : waiter.resolve(message.result);
        }
        if (message.method === 'Runtime.executionContextCreated' && message.params.context.auxData?.isDefault)
            defaultContext = message.params.context.id;
        if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails);
        if (message.method === 'Page.javascriptDialogOpening')
            call('Page.handleJavaScriptDialog', {accept:true}).catch(error => errors.push({exception:{description:error.message}}));
    };
    ws.onclose = () => {
        for (const waiter of pending.values()) waiter.reject(new Error('Browser connection closed'));
        pending.clear();
    };
    const target = await call('Target.createTarget',{url:'about:blank'},false);
    sid = (await call('Target.attachToTarget',{targetId:target.targetId,flatten:true},false)).sessionId;
    await call('Page.enable'); await call('Runtime.enable'); await call('Network.enable');
    await call('Page.addScriptToEvaluateOnNewDocument',{source:'document.addEventListener("uci-applied",()=>sessionStorage.setItem("xa-native-applied",String(Number(sessionStorage.getItem("xa-native-applied")||0)+1)))'});
    await call('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:path.join(profile,'downloads')},false);
    await call('Emulation.setDeviceMetricsOverride',{width:1280,height:1200,deviceScaleFactor:1,mobile:false});
    await call('Network.setCookie',{name:'sysauth_http',value:session,url:base,path:'/'});
    await call('Page.navigate',{url:base+'/cgi-bin/luci/admin/services/xbox-accel'});
    await until('document.querySelector("#xbox-accel-log") || document.querySelector("input[name=luci_password]")');
    if (await evaluate('!!document.querySelector("input[name=luci_password]")')) {
        loginContext = defaultContext;
        await evaluate('document.querySelector("input[name=luci_username]").value="root";document.querySelector("input[name=luci_password]").value="";Array.from(document.querySelectorAll("button")).find(b=>b.textContent.trim()==="Log in").click()');
    }
    await until('document.querySelector("#xbox-accel-log") && document.querySelectorAll(".cbi-tabmenu a").length===4');
    assert.deepEqual(await evaluate('Array.from(document.querySelectorAll(".cbi-tabmenu a")).map(a=>a.textContent.trim())'),
                     ['总览','加速功能','测速','运行记录']);
    assert(await evaluate('document.querySelector("#xbox-accel-status").getBoundingClientRect().height>0'));
    assert(await evaluate('["_automatic","_compatibility","_sampling","_resource_switch","xbox-accel-diagnostics","xbox-accel-raw-log"].every(id=>!document.getElementById(id).open)'));
    await selectTab('加速功能');
    assert(await evaluate('["enabled","optimize_cn","optimize_store","optimize_ps","optimize_xbox_old","optimize_switch"].every(n=>!!document.querySelector("[data-name=\\""+n+"\\"] input[type=checkbox]"))'));
    assert(await evaluate('!!document.querySelector("[data-name=\\"_international_mode\\"]")'));
    console.log('Real LuCI four tabs, platform controls and folded advanced settings: PASS');

    await selectTab('测速');
    const before = runtimeSignature();
    let previous = status().last_task?.id;
    await click('单独测', measurementRow('PlayStation'));
    let measured = await waitTask(previous, 'measure');
    assert.equal(measured.last_task.status,'success');
    assert.equal(measured.measurements.ps.ip,'9.9.9.9');
    assert.equal(runtimeSignature(),before);
    assert.equal(option('enabled'),'0');
    assert.equal(option('optimize_ps'),'0');
    await until('document.querySelector("#xbox-accel-measurements").textContent.includes("9.9.9.9")');
    await evaluate('Array.from(document.querySelectorAll("#xbox-accel-test-groups input")).forEach(i=>{i.checked=["ps","switch"].includes(i.value)})');
    previous = measured.last_task.id;
    await click('开始测速');
    measured = await waitTask(previous, 'measure');
    assert.equal(measured.last_task.groups.ps.status,'success');
    assert.equal(measured.last_task.groups.switch.status,'success');
    assert.equal(measured.last_task.groups.com.code,'NOT_REQUESTED');
    assert.equal(runtimeSignature(),before);
    console.log('Real LuCI disabled single-platform and multi-platform measurements preserve runtime: PASS');

    await click('启用并应用', measurementRow('PlayStation'));
    const adopted = await waitTask(measured.last_task.id, 'adopt');
    assert.equal(adopted.last_task.status,'success');
    assert.equal(option('enabled'),'1');
    assert.equal(option('optimize_ps'),'1');
    assert.equal(adopted.last_application.groups.ps.status,'verified');
    await selectTab('加速功能');
    await until('document.querySelector("[data-name=\\"enabled\\"] input[type=checkbox]").checked && document.querySelector("[data-name=\\"optimize_ps\\"] input[type=checkbox]").checked');
    await selectTab('总览');
    await until('document.querySelector("#xbox-accel-downloads").textContent.includes("已命中当前节点")');
    assert(await evaluate('document.querySelector("#xbox-accel-downloads").textContent.includes("TEST_DEVICE_NAME")'));
    await screenshot('luci-overview.png');
    console.log('Real LuCI adoption, control synchronization and device evidence: PASS');

    // Persist a signed optional resource through LuCI's native DynamicList and Save & Apply.
    const gameUrl = 'https://atum.hac.lp1.d4c.nintendo.net/browser-game.pkg?token=TEST_BROWSER_SIGNATURE&expires=1999999999';
    await selectTab('测速');
    await openDetails('_resource_switch');
    await evaluate('(()=>{const f=document.querySelector("[data-name=\\"verification_urls_switch\\"]");const i=f.querySelector(".add-item input");i.value=' +
        quote(gameUrl) + ';i.dispatchEvent(new Event("keyup",{bubbles:true}));f.querySelector(".cbi-button-add").click();})()');
    assert(await evaluate('Array.from(document.querySelectorAll("[data-name=\\"verification_urls_switch\\"] .item input[type=hidden]")).some(i=>i.value===' + quote(gameUrl) + ')'));

    for (const [kind,clock,day,mode] of [['weekly','21:37','3','redirect'],['monthly','06:42','28','optimize'],['off',null,null,'off'],['daily','08:09',null,'off']]) {
        await selectTab('加速功能');
        await selectValue('_international_mode',mode);
        await openDetails('_automatic');
        await selectValue('schedule_type',kind);
        if (clock) await setInput('schedule_time',clock);
        if (kind === 'weekly') await selectValue('schedule_weekday',day);
        if (kind === 'monthly') await setInput('schedule_monthday',day);
        const logMode = kind === 'weekly' ? 'detail' : 'concise';
        await selectTab('运行记录');
        await openDetails('_logging');
        await selectValue('log_mode',logMode);
        const previousApply = Number(await evaluate('sessionStorage.getItem("xa-native-applied")||0'));
        await until('!!document.querySelector(".cbi-page-actions .cbi-button-apply li[data-value=\\"0\\"]")');
        await evaluate('document.querySelector(".cbi-page-actions .cbi-button-apply li[data-value=\\"0\\"]").click()');
        await until('Number(sessionStorage.getItem("xa-native-applied")||0)>'+previousApply,60000);
        await pause(1000);
        const deadline = Date.now()+60000;
        let saved = false;
        while (Date.now()<deadline) {
            const current = status();
            if (option('schedule_type')===kind && (!clock || option('schedule_time')===clock) &&
                option('log_mode')===logMode && (!day || option(kind==='weekly'?'schedule_weekday':'schedule_monthday')===day) &&
                current.mode===mode && current.running && !current.testing && !['queued','running'].includes(current.last_task?.status)) {saved=true;break;}
            await pause(500);
        }
        assert(saved, 'Native Save & Apply did not persist '+kind+' / '+mode+'\n'+JSON.stringify(status().last_task)+'\n'+docker('uci','show','xbox-accel'));
        assert.equal(option('enabled'),'1');
        assert.equal(option('optimize_ps'),'1');
        if (mode==='redirect') assert.equal(option('optimize_cn'),'1');
        if (mode==='optimize') assert.equal(option('optimize_com'),'1');
        if (kind==='off') assert(!docker('cat','/etc/crontabs/root').includes('start_test all scheduled'));
        await pause(1500);
        await reload();
        await until('Array.from(document.querySelectorAll("button")).some(b=>b.textContent.trim()==="开始测速" && !b.disabled)');
    }
    assert(option('verification_urls_switch').includes(gameUrl));
    assert(!docker('uci','show','xbox-accel').includes('._international_mode='));
    console.log('Real LuCI native save, international mode, schedules and optional signed resource: PASS');

    await selectTab('运行记录');
    await until('document.querySelector("#xbox-accel-task-details").textContent.includes("采用测速结果")');
    await openDetails('xbox-accel-diagnostics');
    await click('查看最近验收');
    await until('document.querySelector("#xbox-accel-environment").textContent.includes("DEPENDENCIES_READY")');
    assert(await evaluate('document.querySelectorAll("#xbox-accel-environment details").length>=1'));
    await screenshot('luci-records.png');
    previous = status().last_task.id;
    await click('导出诊断报告');
    const exported = await waitTask(previous, 'report');
    assert.equal(exported.last_task.code,'REPORT_READY');
    const downloaded = await evaluate('L.require("fs").then(fs=>fs.read_direct("/tmp/xbox-accel/diagnostic-report.json","blob")).then(blob=>blob.text())');
    const report = JSON.parse(downloaded);
    assert(report.redacted && report.current_checks.summary.xbox==='unknown');
    assert(!/\b(?:\d{1,3}\.){3}\d{1,3}\b/.test(downloaded), 'Export must alias raw IP addresses');
    assert(!downloaded.includes('TEST_DEVICE_NAME'), 'Export must omit device names');
    assert(!downloaded.includes('TEST_BROWSER_SIGNATURE'), 'Export must omit resource signatures');
    console.log('Real LuCI history, acceptance details and authenticated redacted export: PASS');

    for (const [label,file] of [['加速功能','luci-functions.png'],['测速','luci-measurements.png']]) {
        await selectTab(label);
        await screenshot(file);
    }
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    for (const [label,file] of [['总览','luci-mobile.png'],['加速功能','luci-mobile-functions.png'],
                              ['测速','luci-mobile-measurements.png'],['运行记录','luci-mobile-records.png']]) {
        await selectTab(label);
        if (label === '测速') await openDetails('_resource_switch');
        await screenshot(file);
        assert(await evaluate('document.documentElement.scrollWidth<=window.innerWidth+2'), label+' mobile layout has page overflow');
    }
    assert.equal(applicationErrors().length,0,'Runtime errors: '+JSON.stringify(applicationErrors()));
    console.log('Real Chrome/LuCI workflow and mobile layout: PASS');
})().catch(error => {
    console.error(error);
    process.exitCode=1;
}).finally(async () => {
    clearTimeout(timer);
    if (ws && ws.readyState===WebSocket.OPEN) {
        try { ws.send(JSON.stringify({id:++sequence,method:'Browser.close'})); } catch (_) {}
        ws.close();
    }
    if (child && child.exitCode===null) {
        child.kill('SIGTERM');
        await Promise.race([new Promise(resolve=>child.once('exit',resolve)),pause(5000)]);
        if (child.exitCode===null && child.signalCode===null) child.kill('SIGKILL');
    }
    if (profile) fs.rmSync(profile,{recursive:true,force:true});
    if (fixtureWork) {
        try { execFileSync('python3',[fixtureScript,container,'restore',fixtureWork],
            {env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:'pipe'}); }
        catch (error) {console.error('Fixture restore failed: '+error.message);process.exitCode=1;}
    }
    if (!process.env.XBOX_ACCEL_SCREENSHOTS) fs.rmSync(screenshotDir,{recursive:true,force:true});
});
