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
const deadline=setTimeout(()=>{ console.error('browser timeout'); child.kill(); process.exit(1); },90000);

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
(async()=>{
    const endpoint=await new Promise((resolve,reject)=>{
        let buffer='';
        child.stderr.on('data',chunk=>{buffer+=chunk;const m=buffer.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(m)resolve(m[1]);});
        child.on('error',reject);
        child.on('exit',code=>reject(new Error('Chrome exited '+code)));
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
    for(const label of ['Xbox 下载加速','环境检测','运行日志','立即测速','高级设置']) assert(text.includes(label),'Missing '+label);
    assert(!text.includes('RPCError'),'RPC failure displayed');
    assert(!text.includes('TypeError'),'JavaScript failure displayed');
    if (process.argv.includes('--inspect-only')) {
        console.log(await evaluate(`JSON.stringify({tabs:Array.from(document.querySelectorAll('[data-tab]')).map(e=>({tag:e.tagName,name:e.getAttribute('data-tab'),active:e.getAttribute('data-tab-active'),display:getComputedStyle(e).display,classes:e.className})), env:document.querySelector('#xbox-accel-environment')?.parentElement?.outerHTML.slice(0,1200), menu:document.querySelector('.cbi-tabmenu')?.outerHTML})`));
        return;
    }
    assert(await evaluate(`document.querySelector('#xbox-accel-status').getBoundingClientRect().height > 0`), 'Top status must be visible');
    assert(await evaluate(`document.querySelector('#xbox-accel-environment').closest('div[data-tab]').getBoundingClientRect().height === 0 && document.querySelector('#xbox-accel-log').closest('div[data-tab]').getBoundingClientRect().height === 0`), 'Environment/log must be hidden in the default settings tab');
    await selectTab('环境检测');
    assert((await evaluate('document.body.innerText')).includes('依赖组件'), 'Environment RPC did not produce actual rows');
    assert(await evaluate(`document.querySelector('#xbox-accel-environment').closest('div[data-tab]').getBoundingClientRect().height > 0 && document.querySelector('#xbox-accel-status').getBoundingClientRect().height > 0`));
    await selectTab('运行日志');
    assert(await evaluate(`document.querySelector('#xbox-accel-log').closest('div[data-tab]').getBoundingClientRect().height > 0 && document.querySelector('#xbox-accel-environment').closest('div[data-tab]').getBoundingClientRect().height === 0 && document.querySelector('#xbox-accel-status').getBoundingClientRect().height > 0`));
    await selectTab('基本设置');
    assert.equal(errors.length,0,'Runtime errors: '+JSON.stringify(errors));
    if (process.argv.includes('--inspect-only')) {
        console.log(await evaluate(`JSON.stringify({footer:document.querySelector('.cbi-page-actions')?.outerHTML, domestic:Array.from(document.querySelectorAll('.cbi-value')).find(r=>r.querySelector('.cbi-value-title')?.textContent.trim()==='国内 CDN 优选')?.outerHTML})`));
        return;
    }
    // Exercise a real form change and native Save & Apply, while the plugin is disabled.
    const wasOptimized = JSON.parse(execFileSync('docker', ['exec',container,'ubus','call','xbox-accel','status'],{encoding:'utf8'})).optimize_cn;
    const wasSchedule = execFileSync('docker',['exec',container,'uci','get','xbox-accel.main.cron'],{encoding:'utf8'}).trim();
    const selectedSchedule = wasSchedule === '0 */6 * * *' ? '0 */3 * * *' : '0 */6 * * *';
    await selectTab('自动测速');
    assert(await evaluate(`document.querySelector('[data-name="cron"]').getAttribute('data-widget')==='CBI.ListValue'`), 'Schedule must be a list, not an expression input');
    await evaluate(`(()=>{const field=document.querySelector('[data-name="cron"]'); const select=field.querySelector('select'); if(select){select.value=${JSON.stringify(selectedSchedule)};select.dispatchEvent(new Event('change',{bubbles:true}));}else{const d=field.querySelector('.cbi-dropdown');(d.querySelector('.open')||d).click(); Array.from(d.querySelectorAll('li[data-value]')).find(li=>li.getAttribute('data-value')===${JSON.stringify(selectedSchedule)}).click();}})()`);
    await selectTab('基本设置');
    await evaluate(`const row=Array.from(document.querySelectorAll('.cbi-value')).find(r=>r.querySelector('.cbi-value-title')?.textContent.trim()==='国内 CDN 优选'); row.querySelector('input[type=checkbox]').click(); document.querySelector('.cbi-page-actions .cbi-button-apply li[data-value="0"]').click();`);
    const end=Date.now()+30000;
    let changed=false;
    while(Date.now()<end) {
        const current=JSON.parse(execFileSync('docker',['exec',container,'ubus','call','xbox-accel','status'],{encoding:'utf8'}));
        const currentSchedule=execFileSync('docker',['exec',container,'uci','get','xbox-accel.main.cron'],{encoding:'utf8'}).trim();
        if(current.optimize_cn!==wasOptimized && currentSchedule===selectedSchedule) { changed=true; break; }
        await new Promise(r=>setTimeout(r,500));
    }
    assert(changed, 'Native LuCI Save & Apply did not persist the form change');
    execFileSync('docker',['exec',container,'uci','set','xbox-accel.main.cron='+wasSchedule]);
    execFileSync('docker',['exec',container,'sh','-c',`uci set xbox-accel.main.optimize_cn=${wasOptimized?1:0}; uci commit xbox-accel; /etc/init.d/xbox-accel reload`]);
    // Wait for native apply UI confirmation, then reload the restored state for the screenshot.
    await new Promise(r=>setTimeout(r,5000));
    await call('Page.reload',{ignoreCache:true});
    await until('document.querySelector("textarea[readonly]") && document.querySelectorAll(".cbi-map").length > 0');
    const conf=execFileSync('docker',['exec',container,'uci','show','xbox-accel'],{encoding:'utf8'});
    assert(!conf.includes('._environment=') && !conf.includes('._logs='), 'Display-only panels must not write UCI');
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
    if (ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({id:++seq,method:'Browser.close'})); } catch (_) {}
    }
    if(ws)ws.close();
    child.kill('SIGTERM');
    setTimeout(()=>process.exit(process.exitCode || 0), 500).unref();
    // The temporary Chrome profile is left in the OS temp directory; never touch an existing profile.
});
