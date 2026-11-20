'use strict';
// Real Chrome / real LuCI smoke test against a disposable Docker OpenWrt container.
// Usage: node tests/test_browser.js xa-test http://127.0.0.1:18080
// Requires macOS Chrome, Node 24+, and an empty-root-password TEST container.
const {spawn, execFileSync} = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert');

const container = process.argv[2] || 'xa-test';
const base = process.argv[3] || 'http://127.0.0.1:18080';
assert(/^http:\/\/127\.0\.0\.1:[0-9]+$/.test(base), 'Only a localhost test URL is allowed');
const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'xbox-accel-browser-'));
const session = JSON.parse(execFileSync('docker', ['exec', container, 'ubus', 'call', 'session', 'login', '{"username":"root","password":""}'], {encoding:'utf8'})).ubus_rpc_session;
assert(session, 'Could not create test session');
const child = spawn(chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir='+profile,
    '--no-first-run', '--no-default-browser-check', '--disable-background-networking', '--disable-component-update',
    '--disable-sync', '--no-proxy-server', 'about:blank'], {stdio:['ignore','ignore','pipe']});
let ws, seq=0, sid;
const pending=new Map();
const errors=[];
const deadline=setTimeout(()=>{ console.error('browser timeout'); child.kill(); process.exit(1); },240000);
let configurationBackup;

async function call(method, params={}, page=true, retry=0) {
    try { return await rawCall(method, params, page); }
    catch (e) {
        // Native LuCI apply reloads the document; Chrome briefly detaches the active renderer.
        if (page && retry < 15 && /Not attached to an active page|Execution context was destroyed/.test(e.message)) {
            await new Promise(r=>setTimeout(r,200));
            return call(method, params, page, retry+1);
        }
        throw e;
    }
}
function rawCall(method, params={}, page=true) {
    const id=++seq;
    return new Promise((resolve,reject)=>{
        pending.set(id,{resolve,reject,method});
        ws.send(JSON.stringify({id,method,params,...(page&&sid?{sessionId:sid}:{})}));
    });
}
async function evaluate(expression) {
    const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
    if(r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
}
async function until(expression) {
    const limit=Date.now()+40000;
    while(Date.now()<limit) {
        if(await evaluate(expression)) return;
        await new Promise(r=>setTimeout(r,300));
    }
    throw new Error('DOM condition timed out: '+expression+'\n'+await evaluate('document.body.innerText'));
}
async function selectTab(label) {
    const name=await evaluate(`(()=>{const a=Array.from(document.querySelectorAll('.cbi-tabmenu a')).find(a=>a.textContent.trim()===${JSON.stringify(label)}); a.click(); return a.parentElement.getAttribute('data-tab');})()`);
    await until(`(()=>{const p=document.querySelector('div[data-tab='+${JSON.stringify(name)}+']'); return p.getAttribute('data-tab-active')==='true' && Number(getComputedStyle(p).opacity)>0.99;})()`);
}
async function selectValue(name, value) {
    await evaluate(`(()=>{const field=document.querySelector('[data-name='+${JSON.stringify(name)}+']'); const select=field.querySelector('select'); if(select){select.value=${JSON.stringify(value)};select.dispatchEvent(new Event('change',{bubbles:true}));}else{const d=field.querySelector('.cbi-dropdown');(d.querySelector('.open')||d).click(); Array.from(d.querySelectorAll('li[data-value]')).find(li=>li.getAttribute('data-value')===${JSON.stringify(value)}).click();}})()`);
}
function currentOption(name) {
    return execFileSync('docker',['exec',container,'uci','-q','get','xbox-accel.main.'+name],{encoding:'utf8'}).trim();
}
(async()=>{
    const endpoint=await new Promise((resolve,reject)=>{
        let buffer='';
        child.stderr.on('data',chunk=>{buffer+=chunk;const m=buffer.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(m)resolve(m[1]);});
        child.on('error',reject);
        child.on('exit',(code,signal)=>reject(new Error('Chrome exited '+(signal || code)+'\n'+buffer)));
    });
    ws=new WebSocket(endpoint);
    await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
    ws.onmessage=ev=>{
        const msg=JSON.parse(ev.data);
        if(msg.id&&pending.has(msg.id)) {const p=pending.get(msg.id);pending.delete(msg.id);msg.error?p.reject(new Error(p.method+': '+JSON.stringify(msg.error))):p.resolve(msg.result);}
        if(msg.method==='Runtime.exceptionThrown') errors.push(msg.params.exceptionDetails);
    };
    const target=await call('Target.createTarget',{url:'about:blank'},false);
    const attached=await call('Target.attachToTarget',{targetId:target.targetId,flatten:true},false);
    sid=attached.sessionId;
    await call('Page.enable'); await call('Runtime.enable'); await call('Network.enable');
    await call('Emulation.setDeviceMetricsOverride',{width:1280,height:1400,deviceScaleFactor:1,mobile:false});
    await call('Network.setCookie',{name:'sysauth_http',value:session,url:base,path:'/'});
    await call('Page.navigate',{url:base+'/cgi-bin/luci/admin/services/xbox-accel'});
    await until('document.querySelector("textarea[readonly]") || document.querySelector("input[name=luci_password]")');
    if (await evaluate('!!document.querySelector("input[name=luci_password]")')) {
        await evaluate(`document.querySelector('input[name=luci_username]').value='root'; document.querySelector('input[name=luci_password]').value=''; Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='Log in').click();`);
    }
    await until('document.querySelector("textarea[readonly]") && document.querySelectorAll(".cbi-map").length > 0');
    const text=await evaluate('document.body.innerText');
    for(const label of ['Xbox 下载加速','环境检测','运行日志','立即测速','重启服务','高级设置']) assert(text.includes(label),'Missing '+label);
    assert(!text.includes('RPCError'),'RPC failure displayed');
    assert(!text.includes('TypeError'),'JavaScript failure displayed');
    if (process.argv.includes('--inspect-only')) {
        console.log(await evaluate(`JSON.stringify({tabs:Array.from(document.querySelectorAll('[data-tab]')).map(e=>({tag:e.tagName,name:e.getAttribute('data-tab'),active:e.getAttribute('data-tab-active'),display:getComputedStyle(e).display,classes:e.className})), env:document.querySelector('#xbox-accel-environment')?.parentElement?.outerHTML.slice(0,1200), menu:document.querySelector('.cbi-tabmenu')?.outerHTML, fields:Array.from(document.querySelectorAll('.cbi-value')).map(e=>({id:e.id,name:e.getAttribute('data-name'),field:e.getAttribute('data-field')}))})`));
        return;
    }
    assert(await evaluate(`document.querySelector('#xbox-accel-status').getBoundingClientRect().height > 0`), 'Top status must be visible');
    assert(await evaluate(`document.querySelector('#xbox-accel-environment').closest('div[data-tab]').getBoundingClientRect().height === 0 && document.querySelector('#xbox-accel-log').closest('div[data-tab]').getBoundingClientRect().height === 0`), 'Environment/log must be hidden in the default settings tab');
    await selectTab('环境检测');
    assert((await evaluate('document.body.innerText')).includes('DEPENDENCIES_READY'), 'Environment RPC did not produce actual rows');
    assert(await evaluate(`document.querySelector('#xbox-accel-environment').closest('div[data-tab]').getBoundingClientRect().height > 0 && document.querySelector('#xbox-accel-status').getBoundingClientRect().height > 0`));
    await selectTab('运行日志');
    assert(await evaluate(`document.querySelector('#xbox-accel-log').closest('div[data-tab]').getBoundingClientRect().height > 0 && document.querySelector('#xbox-accel-environment').closest('div[data-tab]').getBoundingClientRect().height === 0 && document.querySelector('#xbox-accel-status').getBoundingClientRect().height > 0`));
    await selectTab('基本设置');
    assert.equal(errors.length,0,'Runtime errors: '+JSON.stringify(errors));
    const previousTask = JSON.parse(execFileSync('docker',['exec',container,'ubus','call','xbox-accel','status'],{encoding:'utf8'})).last_task?.id;
    await until(`(()=>{const b=Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='重启服务');return b && !b.disabled;})()`);
    await evaluate(`Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='重启服务').click()`);
    const restartEnd = Date.now()+60000;
    let restarted = false;
    while (Date.now()<restartEnd) {
        const current = JSON.parse(execFileSync('docker',['exec',container,'ubus','call','xbox-accel','status'],{encoding:'utf8'}));
        if(current.last_task?.id!==previousTask && current.last_task?.kind==='restart' && !current.testing && current.last_task?.status==='success') {restarted=true;break;}
        await new Promise(r=>setTimeout(r,300));
    }
    assert(restarted,'Restart button did not complete its tracked service task');
    await until(`document.querySelector('#xbox-accel-status').textContent.includes('重启服务并验收')`);
    if (process.argv.includes('--inspect-only')) {
        console.log(await evaluate(`JSON.stringify({footer:document.querySelector('.cbi-page-actions')?.outerHTML, domestic:Array.from(document.querySelectorAll('.cbi-value')).find(r=>r.querySelector('.cbi-value-title')?.textContent.trim()==='国内 CDN 优选')?.outerHTML})`));
        return;
    }
    // Exercise a real form change and native Save & Apply, while the plugin is disabled.
    configurationBackup = execFileSync('docker',['exec',container,'cat','/etc/config/xbox-accel'],{encoding:'utf8'});
    const wasOptimized = JSON.parse(execFileSync('docker', ['exec',container,'ubus','call','xbox-accel','status'],{encoding:'utf8'})).optimize_cn;
    const wasAutostart = await evaluate(`document.querySelector('[data-name="autostart"] input[type=checkbox]').checked`);
    for (const [kind,time,day] of [['weekly','21:37','3'],['monthly','06:42','28'],['daily','08:09',null]]) {
        await selectTab('自动测速');
        assert(await evaluate(`document.querySelector('[data-name="schedule_type"]').getAttribute('data-widget')==='CBI.ListValue' && !document.querySelector('[data-name="cron"]')`));
        await selectValue('schedule_type',kind);
        await evaluate(`(()=>{const input=document.querySelector('[data-name="schedule_time"] input');input.value=${JSON.stringify(time)};input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
        const dayOption = kind === 'weekly' ? 'schedule_weekday' : 'schedule_monthday';
        if (kind === 'weekly') await selectValue(dayOption,day);
        if (kind === 'monthly') await evaluate(`(()=>{const input=document.querySelector('[data-name="schedule_monthday"] input');input.value=${JSON.stringify(day)};input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
        if (kind === 'weekly') {
            await selectTab('基本设置');
            await evaluate(`document.querySelector('[data-name="optimize_cn"] input[type=checkbox]').click(); document.querySelector('[data-name="autostart"] input[type=checkbox]').click();`);
        }
        await evaluate(`document.querySelector('.cbi-page-actions .cbi-button-apply li[data-value="0"]').click()`);
        const end=Date.now()+30000;
        let changed=false;
        while(Date.now()<end) {
            try {
                if(currentOption('schedule_type')===kind && currentOption('schedule_time')===time && (day===null || currentOption(dayOption)===day)) {changed=true;break;}
            } catch (_) {}
            await new Promise(r=>setTimeout(r,500));
        }
        assert(changed,'Native Save & Apply did not persist '+kind+' schedule');
        await new Promise(r=>setTimeout(r,5000));
        await call('Page.reload',{ignoreCache:true});
        await until('document.querySelector("textarea[readonly]") && document.querySelectorAll(".cbi-map").length > 0');
    }
    assert.strictEqual(currentOption('optimize_cn'),wasOptimized?'0':'1');
    assert.strictEqual(currentOption('autostart'),wasAutostart?'0':'1');
    execFileSync('docker',['exec','-i',container,'sh','-c','cat > /etc/config/xbox-accel; /etc/init.d/xbox-accel reload'],{input:configurationBackup});
    // Wait for native apply UI confirmation, then reload the restored state for the screenshot.
    await new Promise(r=>setTimeout(r,5000));
    await call('Page.reload',{ignoreCache:true});
    await until('document.querySelector("textarea[readonly]") && document.querySelectorAll(".cbi-map").length > 0');
    const conf=execFileSync('docker',['exec',container,'uci','show','xbox-accel'],{encoding:'utf8'});
    assert(!conf.includes('._environment=') && !conf.includes('._logs='), 'Display-only panels must not write UCI');
    // Generate a background report and verify the authenticated CGI/file ACL download.
    const exportResult=JSON.parse(execFileSync('docker',['exec',container,'ubus','call','xbox-accel','action','{"command":"export"}'],{encoding:'utf8'}));
    assert(exportResult.accepted, 'Report request must be admission, not a success claim');
    const reportDeadline=Date.now()+60000;
    let ready=false;
    while(Date.now()<reportDeadline) {
        const current=JSON.parse(execFileSync('docker',['exec',container,'ubus','call','xbox-accel','status'],{encoding:'utf8'}));
        if(!current.testing && current.last_task?.code==='REPORT_READY' && current.report_ready) {ready=true;break;}
        await new Promise(r=>setTimeout(r,500));
    }
    assert(ready,'Diagnostic report did not finish');
    const downloaded=await evaluate(`L.require('fs').then(fs=>fs.read_direct('/tmp/xbox-accel/diagnostic-report.json','blob')).then(blob=>blob.text())`);
    const report=JSON.parse(downloaded);
    assert(report.redacted===true && report.current_checks.summary.xbox==='unknown');
    assert(!/\b(?:\d{1,3}\.){3}\d{1,3}\b/.test(downloaded),'Export must alias raw IP addresses');
    console.log('Authenticated diagnostic download ACL and redacted JSON: PASS');
    for (const [label,file] of [['基本设置','luci-overview.png'],['自动测速','luci-schedule.png'],['环境检测','luci-environment.png'],['运行日志','luci-logs.png']]) {
        await selectTab(label);
        const screenshot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
        const output=path.join(__dirname,'../dist',file);
        fs.mkdirSync(path.dirname(output),{recursive:true});
        fs.writeFileSync(output,Buffer.from(screenshot.data,'base64'));
        console.log(output);
    }
    console.log('Real Chrome/LuCI tabs, fixed status, native save and RPC: PASS');
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{
    clearTimeout(deadline);
    if (configurationBackup) execFileSync('docker',['exec','-i',container,'sh','-c','cat > /etc/config/xbox-accel; /etc/init.d/xbox-accel reload'],{input:configurationBackup});
    if (ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({id:++seq,method:'Browser.close'})); } catch (_) {}
    }
    if(ws)ws.close();
    child.kill('SIGTERM');
    setTimeout(()=>process.exit(process.exitCode || 0), 500).unref();
    // The temporary Chrome profile is left in the OS temp directory; never touch an existing profile.
});
