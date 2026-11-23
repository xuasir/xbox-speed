'use strict';
// 用最小的 LuCI 替身加载视图，检查渲染、按钮状态、日志按纯文本显示，以及“保存并应用”的时序。
// 不能代替在真实 LuCI 里的验证。
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const source = fs.readFileSync(path.join(__dirname, '../files/www/luci-static/resources/view/xbox-accel/overview.js'), 'utf8');
const calls = [];
const listeners = new Map();
const options = [];
const tabs = [];
const downloads = [];
const fileApi = { read_direct(file) { downloads.push(file); return Promise.resolve({text:()=>Promise.resolve('{}')}); } };
const urlApi = {createObjectURL:()=> 'blob:diagnostics', revokeObjectURL() {}};

String.prototype.format = function(...args) {
	let i = 0;
	return this.replace(/%[sd]/g, () => String(args[i++]));
};

const status = {
	version: '0.2.0', enabled: true, running: true, testing: false, mode: 'redirect', optimize_cn: true,
	lan_ip: '192.168.1.1', cn_ip: '1.2.3.4', cn_speed: '10.00 MB/s（80 Mbps）', cn_time: 1700000000,
	redirect_ip: '192.0.2.1', redirect_port: '18081', redirect_running: true,
	com_ip: '', log: '<script>alert(1)</script>'
};
const environment = { environment: [ { name: '解析', level: 'fail', detail: '<b>x</b>' }, { name: '系统', level: 'info', detail: 'OpenWrt' } ] };

function E(tag, attrs, children) {
	return { tag, attrs: attrs || {}, children: children || [], value: '', textContent: '', disabled: false,
		scrollTop: 0, clientHeight: 0, scrollHeight: 0 };
}

class MapMock {
	section() {
		return {
			tab(name, label) { tabs.push({name, label}); },
		taboption(tab, type, name, title, description) { const o = { tab, type, name, title, description, choices: [], depends() {}, value(key,label) { this.choices.push([key,label]); } }; options.push(o); return o; }
		};
	}
	render() { return Promise.resolve(E('form', {}, options.filter(o => o.render).map(o => o.render()))); }
}

const view = {
	extend(def) {
		def.super = function(name) {
			calls.push([ 'super', name ]);
			return Promise.resolve();
		};
		return def;
	}
};
const rpc = {
	declare(spec) {
		return (...args) => {
			calls.push([ spec.method, ...args ]);
			if (spec.method == 'status') return Promise.resolve(status);
			if (spec.method == 'environment') return Promise.resolve(environment);
			return Promise.resolve({ ok: true, message: '' });
		};
	}
};
const ui = {
	createHandlerFn(ctx, fn) { return () => (typeof fn == 'string' ? ctx[fn]() : fn.call(ctx)); },
	addNotification() { calls.push([ 'notify' ]); }
};
const dom = { content(node, children) { node.children = children; } };
const document = {
	createElement() { return {click(){calls.push(['download']);}}; },
	addEventListener(name, fn) { listeners.set(name, fn); },
	removeEventListener(name, fn) { if (listeners.get(name) === fn) listeners.delete(name); }
};
const L = { resolveDefault: p => Promise.resolve(p), bind: (fn, ctx) => fn.bind(ctx) };
const form = { Map: MapMock, NamedSection: 1, Flag: 2, Value: 3, ListValue: 4, DynamicList: 5 };
let pollStatus;
const poll = { add(fn, interval) { pollStatus = fn; assert.strictEqual(interval, 5); } };
const uci = { load() { return Promise.resolve(); }, get(config,section,key) { return {cron:'0 1 * * 1', health_interval:'40'}[key]; } };
const window = { confirm: () => true };

const body = source.replace(/^'require [^']+';$/gm, '');
const v = new Function('view', 'form', 'uci', 'rpc', 'poll', 'ui', 'dom', 'E', 'L', 'document', 'window', 'fs', 'URL', body)(
	view, form, uci, rpc, poll, ui, dom, E, L, document, window, fileApi, urlApi);

(async () => {
	const data = await v.load();
	const page = await v.render(data);
	assert.strictEqual(page.children.length, 2, '顶部状态之外只能有一个统一标签表单');
	assert.deepStrictEqual(tabs.map(t => t.name), ['basic', 'platforms', 'test', 'advanced', 'environment', 'logs']);
	assert(options.find(o => o.name === '_environment').tab === 'environment');
	assert(options.find(o => o.name === '_logs').tab === 'logs');
	assert.strictEqual(v.buttons.cancel.disabled, true, '空闲时停止按钮应禁用');
	assert.strictEqual(v.buttons.restart.children[0], '重启服务');
	assert.strictEqual(v.buttons.restart.disabled, false);
	calls.length = 0;
	await v.buttons.restart.attrs.click();
	assert(calls.some(c => c[0] === 'action' && c[1] === 'restart'));

	// 日志进入 textarea 的 value，不会被当作 HTML 解析
	assert.strictEqual(v.logNode.value, status.log);
	// 环境检测的内容作为文本节点传入
	assert.strictEqual(v.envNode.children[0].children[2].children[0], '<b>x</b>');
	assert.strictEqual(v.envNode.children[0].children[1].children[0], '失败');

	// 按入口折叠成功域名，异常组展开，刷新时保留手动选择。
	const dnsReport = { environment: [
		{ name: '依赖', level: 'ok', detail: '就绪' },
		{ name: 'A assets1.xboxlive.cn @127.0.0.1:5335', level: 'ok', expected: '9.9.9.9', observed: '9.9.9.9' },
		{ name: 'AAAA assets1.xboxlive.cn @127.0.0.1:5335', level: 'ok', expected: 'NODATA', observed: 'NODATA' },
		{ name: 'SRV assets-srvs.xboxlive.com @127.0.0.1:53', level: 'fail', detail: '<b>异常</b>' },
		{ name: 'DNS 验收 @127.0.0.1:1053', level: 'unknown', detail: '超时' },
		{ name: '覆盖 dlassets.xboxlive.com', level: 'skipped', detail: '默认解析' }
	] };
	v.updateEnvironment(dnsReport);
	const dnsGroups = () => v.envNode.children.slice(1).map(row => row.children[0].children[0]);
	let groups = dnsGroups();
	assert.strictEqual(groups.length, 4);
	assert.strictEqual(groups[0].open, false);
	assert(groups[0].children[0].children[0].includes('独立解析实例（127.0.0.1:5335）：2 项；正常 2'));
	assert.strictEqual(groups[1].open, true);
	assert.strictEqual(groups[2].open, true);
	assert.strictEqual(groups[3].open, false);
	assert.strictEqual(groups[1].children[1].children[0].children[2].children[0], '<b>异常</b>');
	groups[0].attrs.toggle({target:{open:true}});
	groups[1].attrs.toggle({target:{open:false}});
	v.updateEnvironment(dnsReport);
	groups = dnsGroups();
	assert.strictEqual(groups[0].open, true, '已手动展开的成功组应保留');
	assert.strictEqual(groups[1].open, false, '已手动折叠的异常组应保留');
	groups[0].attrs.toggle({target:{open:false}});
	v.updateEnvironment({environment:dnsReport.environment.map((row,i) => i === 1 ? {...row,level:'fail'} : row)});
	assert.strictEqual(dnsGroups()[0].open, true, '新出现异常的入口应自动展开');
	v.updateEnvironment(environment);

	// 稳定状态降低 RPC 频率，异常和后台任务恢复快速刷新。
	calls.length = 0;
	v.updateStatus(status);
	assert(v.nextStatusPoll > Date.now() + 29000);
	await pollStatus();
	assert(!calls.some(c => c[0] === 'status'));
	v.nextStatusPoll = 0;
	await pollStatus();
	assert.strictEqual(calls.filter(c => c[0] === 'status').length, 1);
	v.updateStatus({...status,testing:true});
	assert(v.nextStatusPoll < Date.now() + 6000);
	v.updateStatus({...status,running:false});
	assert(v.nextStatusPoll < Date.now() + 6000);
	v.updateStatus({status_error:'RPC unavailable'});
	assert(v.nextStatusPoll < Date.now() + 6000);
	v.updateStatus(status);

	// 未知与部分成功不能被显示成关闭或全部通过。
	v.updateStatus({...status,last_test:{id:'t1',kind:'test',status:'partial',code:'PARTIAL_RESULT',stage:'finished',message:'国内失败',groups:{cn:{status:'failed',code:'FULL_TEST_FAILED',message:'复测失败'},com:{status:'success',code:'SELECTED',message:'采样通过'}},application:{status:'failed',code:'DNS_MISMATCH'}}});
	const summary=v.statusNode.children.find(row=>row.children[0].children[0]==='最近完整测速').children[1].children[0];
	assert(summary.includes('部分完成'));
	const detailed=v.taskDetails.children[1].children[0];
	assert(detailed.includes('FULL_TEST_FAILED') && detailed.includes('DNS_MISMATCH'));
	v.updateStatus({status_error:'RPC unavailable'});
	assert(v.statusNode.children[0].children[1].children[0].includes('无法取得状态'));
	assert(v.statusNode.children[1].children[1].children[0].includes('未知'));
	assert.strictEqual(v.logNode.value,status.log,'状态失败不得抹去已有日志');
	v.updateStatus(status);
	v.pendingExport='export-queued';
	calls.length=0;
	v.updateStatus({...status,report_ready:true,last_task:{id:'export-queued',kind:'report',status:'queued',code:'ACCEPTED'}});
	assert.strictEqual(v.pendingExport,'export-queued','受理中的导出应等待最终结果');
	assert(v.buttons.export.disabled && v.buttons.test.disabled && v.buttons.restart.disabled && !v.buttons.cancel.disabled,'任务排队时按钮应保持运行状态');
	assert(!calls.some(c=>c[0]==='notify'),'排队阶段应保留任务，等待最终结果');
	v.updateStatus({...status,report_ready:true,last_task:{id:'export-queued',kind:'report',status:'success',code:'REPORT_READY'}});
	await new Promise(resolve=>setImmediate(resolve));
	assert.strictEqual(v.pendingExport,null);
	assert.deepStrictEqual(downloads,['/tmp/xbox-accel/diagnostic-report.json']);
	assert(calls.some(c=>c[0]==='download'));
	v.updateStatus({...status,log_source:'persisted'});
	assert(v.logNote.textContent.includes('历史日志快照'));
	const oldAvailable={...status,last_application:{available:true,checked_at:1,status:'verified'}};
	v.updateStatus(oldAvailable);
	downloads.length=0;
	await v.refreshEnvironment();
	assert.strictEqual(downloads.length,0,'快速初查应展示当前结果，历史验收由单独按钮读取');

	// 每个配置项都有对应的界面选项
	const names = options.map(o => o.name);
	for (const n of [ 'enabled', 'autostart', 'optimize_cn', 'optimize_com', 'redirect_cn', 'isp', 'schedule_type', 'schedule_time', 'schedule_weekday', 'schedule_monthday', 'health_interval',
		'switch_threshold', 'max_candidates', 'quick_size_mb', 'quick_time', 'top_n', 'full_size_mb', 'full_time',
		'override_cdn_hosts', 'passwall_direct', 'passwall_restart', 'dnsmasq_instance', 'resolver_port', 'redirect_ip', 'redirect_port', 'ttl', 'fallback_dns', 'log_mode' ])
		assert(names.includes(n), 'missing option ' + n);
	for (const group of ['store','ps','xbox_old','switch']) {
		assert.strictEqual(options.find(o=>o.name==='optimize_'+group).default, '0');
		assert(names.includes('test_url_'+group) && names.includes('extra_ips_'+group));
		assert(v.buttons['test_'+group]);
	}
	assert(names.includes('store_test_app') && names.includes('verification_urls_switch') && names.includes('verification_urls_ps'));
	const switchLink = options.find(o=>o.name==='verification_urls_switch');
	assert(switchLink.optional && switchLink.title.includes('可选') && switchLink.description.includes('默认节点映射'));
	assert.strictEqual(switchLink.validate('main',''), true);
	assert.strictEqual(switchLink.validate('main','https://atum.hac.lp1.d4c.nintendo.net/game?token=value'), true);
	for (const url of ['https://evil.example/game','https://ctest-dl-lp1.cdn.nintendo.net/30m','https://atum.hac.lp1.d4c.nintendo.net:8443/game','https://user@atum.hac.lp1.d4c.nintendo.net/game'])
		assert.notStrictEqual(switchLink.validate('main',url), true);
	v.updateStatus({...status,platforms:{switch:{enabled:true,ip:'9.9.9.9',game_status:'default',game_configured:0,game_verified:0,game_mapped:3,game_default_mapped:3,game_total:3}},last_test:{kind:'test',status:'partial',groups:{store:{status:'failed',code:'STORE_LINK_FETCH_FAILED',message:'链接获取失败'},switch:{status:'success',code:'SELECTED',message:'公开测速通过'}}}});
	const switchText = () => v.statusNode.children.find(row=>row.children[0].children[0]==='Switch').children[1].children[0];
	assert(switchText().includes('已准备 3/3（默认映射 3）') && switchText().includes('增强校验：可选'));
	assert(v.taskDetails.children[1].children[0].includes('STORE_LINK_FETCH_FAILED'));
	v.updateStatus({...status,platforms:{switch:{enabled:true,ip:'9.9.9.9',game_status:'pending',game_configured:1,game_verified:0,game_mapped:2,game_default_mapped:2,game_total:3}}});
	assert(switchText().includes('已准备 2/3（默认映射 2）') && switchText().includes('增强校验：0/1'));
	v.updateStatus({...status,platforms:{switch:{enabled:true,ip:'9.9.9.9',game_status:'verified',game_configured:1,game_verified:1,game_mapped:3,game_default_mapped:2,game_total:3}}});
	assert(switchText().includes('已准备 3/3（默认映射 2）') && switchText().includes('增强校验：1/1'));
	v.updateStatus(status);

	// 日/周/月 + 时间，不枚举时间预设；旧简单周计划自动映射。
	assert(!options.some(o => o.name === 'cron'));
	const frequency = options.find(o => o.name === 'schedule_type');
	assert.strictEqual(frequency.type, form.ListValue);
	assert.deepStrictEqual(frequency.choices.map(c => c[0]), ['daily', 'weekly', 'monthly']);
	assert.strictEqual(frequency.cfgvalue('main'), 'weekly');
	const scheduleTime = options.find(o => o.name === 'schedule_time');
	assert.strictEqual(scheduleTime.cfgvalue('main'), '01:00');
	assert.strictEqual(options.find(o => o.name === 'schedule_weekday').cfgvalue('main'), '1');
	for (const value of ['00:00', '05:15', '23:59']) assert.strictEqual(scheduleTime.validate('main', value), true);
	for (const value of ['24:00', '12:60', '5:15', '12:00 ; reboot', '']) assert.notStrictEqual(scheduleTime.validate('main', value), true);
	const monthday = options.find(o => o.name === 'schedule_monthday');
	for (const value of ['1', '29', '31']) assert.strictEqual(monthday.validate('main', value), true);
	for (const value of ['0', '32', '1.5', '1; reboot']) assert.notStrictEqual(monthday.validate('main', value), true);
	const health = options.find(o=>o.name==='health_interval');
	assert.strictEqual(health.type, form.ListValue);
	assert(health.choices.some(c=>c[0]==='40' && c[1].includes('00、40')));
	const logMode = options.find(o => o.name === 'log_mode');
	assert.strictEqual(logMode.tab, 'logs');
	assert.strictEqual(logMode.default, 'concise');
	assert.deepStrictEqual(logMode.choices.map(c => c[0]), ['concise', 'detail']);

	// 测速期间禁用测速按钮，只允许停止
	v.updateStatus(Object.assign({}, status, { testing: true }));
	assert(v.buttons.test.disabled && v.buttons.test_cn.disabled && v.buttons.test_com.disabled && v.buttons.restart.disabled);
	for (const group of ['store','ps','xbox_old','switch']) assert(v.buttons['test_'+group].disabled);
	assert(!v.buttons.cancel.disabled);
	calls.length = 0;
	v.updateStatus(status);
	assert(!v.buttons.test.disabled && !v.buttons.restart.disabled && v.buttons.cancel.disabled);
	assert(calls.some(c => c[0] == 'environment'), '测速结束后应重新检测环境');

	// “保存并应用”：配置提交完成之前不得让服务重新载入
	calls.length = 0;
	await v.handleSaveApply({}, '0');
	assert(calls.some(c => c[0] == 'super' && c[1] == 'handleSaveApply'));
	assert(!calls.some(c => c[0] == 'action'), '提交完成前不应调用 apply');
	listeners.get('uci-applied')();
	await new Promise(resolve => setImmediate(resolve));
	assert(calls.some(c => c[0] == 'action' && c[1] == 'apply'));
	assert(!listeners.has('uci-applied'), '监听器应只触发一次');

	console.log('LuCI view (mocked): PASS');
})().catch(e => { console.error(e); process.exit(1); });
