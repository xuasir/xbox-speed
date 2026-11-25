'use strict';
// LuCI 替身覆盖用户操作、任务状态、保存时序和下载证据的显示边界。
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const source = fs.readFileSync(path.join(__dirname,'../files/www/luci-static/resources/view/xbox-accel/overview.js'),'utf8');
const calls=[],options=[],tabs=[],downloads=[],listeners=new Map(),changes=[],rpcSpecs=[];
const config={cron:'0 1 * * 1',health_interval:'40',optimize_cn:'1',optimize_com:'1',redirect_cn:'0',enabled:'1'};
const status={version:'0.3.0',enabled:true,running:true,testing:false,mode:'optimize',optimize_cn:true,
	cn_ip:'8.8.4.4',com_ip:'1.1.1.1',log:'<script>alert(1)</script>',platforms:{ps:{enabled:false},switch:{enabled:false}}};
const environment={environment:[{name:'解析',level:'fail',detail:'<b>x</b>'}]};
let actionReply={ok:true};
let records={events:[],errors:[],tasks:[]};
String.prototype.format=function(...args){let i=0;return this.replace(/%[sd]/g,()=>String(args[i++]));};
function E(tag,attrs,children){
	return {tag,attrs:attrs||{},children:children||[],value:attrs?.value||'',textContent:'',disabled:false,checked:false,
		scrollTop:0,clientHeight:0,scrollHeight:0,events:{},
		addEventListener(name,handler){this.events[name]=handler;},hasAttribute(name){return this.attrs[name]!=null;}};
}
function text(node){
	if(node==null)return '';
	if(typeof node==='string'||typeof node==='number')return String(node);
	return (node.textContent||'')+(node.children||[]).map(text).join(' ');
}
function find(node,id){
	if(!node||typeof node!=='object')return null;
	if(node.attrs&&node.attrs.id===id)return node;
	for(const child of [].concat(node.children||[])){const hit=find(child,id);if(hit)return hit;}
	return null;
}
function section(tab){
	const s={children:[],
		tab(name,label){tabs.push({name,label});},
		taboption(tab,type,name,...args){return addOption(this,tab,type,name,args);},
		option(type,name,...args){return addOption(this,tab,type,name,args);}
	};
	return s;
}
function addOption(parent,tab,type,name,args){
	const o={tab,type,name,option:name,title:args[0],description:args[1],choices:[],dependencies:[],
		depends(...args){this.dependencies.push(args);},value(key,label){this.choices.push([key,label]);},
		cbid(id){return 'cbid.xbox-accel.'+id+'.'+name;},getValidator(){return()=>true;},
		cfgvalue(id,value){if(arguments.length===2)this.cachedValue=value;return Object.prototype.hasOwnProperty.call(this,'cachedValue')?this.cachedValue:config[name]??this.default;},getUIElement(){return{setValue(value){changes.push([name,value]);}}}
	};
	if(type===form.SectionValue){
		o.subsection=section(tab);
		o.renderWidget=async function(){return E('div',{},await Promise.all(o.subsection.children.map(renderOption)));};
	}
	parent.children.push(o);options.push(o);return o;
}
async function renderOption(o){
	if(o.render)return o.render();
	if(o.renderWidget)return o.renderWidget('main');
	return E('div',{'data-name':o.name},[o.title||'']);
}
class MapMock{
	section(){this.s=section();return this.s;}
	render(){
		if(faults.render>0){faults.render--;return Promise.reject(interrupted());}
		return Promise.all(this.s.children.map(renderOption)).then(nodes=>E('form',{},nodes));
	}
}
const view={extend(def){def.super=function(name){calls.push(['super',name]);return Promise.resolve();};return def;}};
// 故障注入：模拟浏览器在取得响应前中断请求（LuCI 报 XHR request aborted by browser）。
const faults={uci:0,status:0,render:0,error:null};
let deferredStatus=null;
const interrupted=()=>faults.error||new Error('XHR request aborted by browser');
const rpc={declare(spec){rpcSpecs.push(spec);return(...args)=>{
	calls.push([spec.method,...args]);
	if(spec.method==='status'&&faults.status>0){faults.status--;return Promise.reject(interrupted());}
	if(spec.method==='status'&&deferredStatus)return deferredStatus;
	return Promise.resolve(spec.method==='status'?status:spec.method==='environment'?environment:spec.method==='records'?records:actionReply);
};}};
class DropdownMock{
	constructor(values,choices,options){this.values=values.slice();this.choices=choices;this.options=options;}
	render(){return this.node=E('div',{id:this.options.id,'class':'cbi-dropdown',multiple:''},Object.values(this.choices));}
	getValue(){return this.values.slice();}
	setValue(values){this.values=values.slice();}
	openDropdown(node){node.attrs.open='';}
}
const ui={Dropdown:DropdownMock,createHandlerFn(ctx,fn){return()=>typeof fn==='string'?ctx[fn]():fn.call(ctx);},addNotification(a,b,type){calls.push(['notify',text(b),type]);}};
const viewNode=E('div',{id:'view'});
const dom={content(node,children){node.children=children;},append(node,child){node.children=[].concat(node.children||[],[child]);}};
const document={createElement(){return{click(){calls.push(['download']);}};},getElementById(id){return id==='view'?viewNode:null;},
	addEventListener(name,fn){listeners.set(name,fn);},removeEventListener(name,fn){if(listeners.get(name)===fn)listeners.delete(name);}};
// 与 LuCI 一致：失败的读取结果留在缓存中，unload 之后才会重新请求。
const uci={cached:null,load(){calls.push(['uci-load']);if(this.cached)return this.cached;if(faults.uci>0){faults.uci--;return this.cached=Promise.reject(interrupted());}return Promise.resolve();},unload(){calls.push(['uci-unload']);this.cached=null;},get(c,s,key){return config[key];},set(c,s,key,value){config[key]=value;changes.push([key,value]);}};
const fileApi={read_direct(file){downloads.push(file);return Promise.resolve({text:()=>Promise.resolve('{}')});}};
const urlApi={createObjectURL:()=> 'blob:diagnostics',revokeObjectURL(){}};
const L={bind:(fn,ctx)=>fn.bind(ctx)};
const form={Map:MapMock,NamedSection:1,Flag:2,Value:3,ListValue:4,DynamicList:5,DummyValue:6,SectionValue:7,MultiValue:8};
let pollStatus;
const pollFunctions=new Set();
const poll={add(fn,interval){pollStatus=fn;pollFunctions.add(fn);assert.strictEqual(interval,5);assert.strictEqual(pollFunctions.size,1,'重载后只有一个状态轮询');},remove(fn){pollFunctions.delete(fn);}};
const body=source.replace(/^'require [^']+';$/gm,'');
const v=new Function('view','form','uci','rpc','poll','ui','dom','E','L','document','window','fs','URL',body)(
	view,form,uci,rpc,poll,ui,dom,E,L,document,{confirm:()=>true},fileApi,urlApi);
const tick=()=>new Promise(resolve=>setImmediate(resolve));

(async()=>{
	const page=await v.render(await v.load());
	assert(rpcSpecs.every(spec=>spec.nobatch&&spec.reject),'插件 RPC 独立发送并报告后端错误');
	assert.deepStrictEqual(tabs.map(t=>t.label),['总览','加速功能','测速','运行记录']);
	assert.strictEqual(page.attrs.class,'xa-product');
	for(const id of ['_automatic','_compatibility','_sampling','_resource_switch','xbox-accel-diagnostics'])
		assert(!v.disclosures[id].open,'高级内容默认折叠：'+id);
	assert.strictEqual(find(page,'xbox-accel-raw-log').tag,'div','运行日志直接可见');
	assert.strictEqual(find(page,'xbox-accel-record-actions').tag,'div','操作按钮直接可见');
	assert.strictEqual(options.find(o=>o.name==='download_devices').type,form.MultiValue,'设备筛选是可保存的多选配置项');
	assert.strictEqual(options.find(o=>o.name==='_records').tab,'records');
	assert.strictEqual(v.logNode.value,status.log);
	assert(text(v.statusNode).includes('当前插件版本')&&text(v.statusNode).includes(status.version));
	assert(!text(v.statusNode).includes('TASK_'),'首页摘要只展示用户可读结论');
	assert(!v.buttons.test.disabled && v.buttons.cancel.disabled);
	assert(v.buttons.adopt_ps.disabled,'取得结果后才可采用');

	// 关闭的平台和关闭的总服务仍可单独测，手动动作携带明确的选择。
	v.updateStatus({...status,enabled:false,platforms:{ps:{enabled:false},switch:{enabled:false}}});
	calls.length=0;changes.length=0;
	await v.buttons.test_ps.attrs.click();
	assert(calls.some(c=>c[0]==='action'&&c[1]==='test_ps'));
	assert.strictEqual(changes.length,0);
	v.selectGroups(['ps','switch']);
	await v.buttons.test.attrs.click();
	assert(calls.some(c=>c[0]==='action'&&c[1]==='test'&&c[2]==='ps,switch'));
	v.selectGroups([]);calls.length=0;await v.buttons.test.attrs.click();
	assert(!calls.some(c=>c[0]==='action'));
	assert(calls.some(c=>c[0]==='notify'&&c[1].includes('请选择')));

	const result={id:'20261124T080000-12',ip:'9.9.9.9',speed_bps:20000000,tested_at:1700000000,compatible:true};
	v.updateStatus({...status,enabled:false,measurements:{ps:result}});
	assert(text(v.resultRows.ps.result).includes('9.9.9.9'));
	assert(text(v.resultRows.ps.adopt).includes('启用并应用'));
	assert(!v.buttons.adopt_ps.disabled);
	calls.length=0;await v.buttons.adopt_ps.attrs.click();
	assert(calls.some(c=>c[0]==='action'&&c[1]==='adopt_ps'&&c[3]===result.id));
	config.enabled='1';config.optimize_ps='1';
	v.configOptions.enabled.cfgvalue('main','0');v.configOptions.optimize_ps.cfgvalue('main','0');
	await v.refreshAdoptedConfiguration();
	assert.strictEqual(v.configOptions.enabled.cfgvalue('main'),'1');
	assert.strictEqual(v.configOptions.optimize_ps.cfgvalue('main'),'1');
	assert(changes.some(c=>c[0]==='enabled'&&c[1]==='1'));
	v.updateStatus({...status,measurements:{ps:{...result,compatible:false}}});
	assert(v.buttons.adopt_ps.disabled);
	assert(text(v.resultRows.ps.result).includes('重新测速'));

	// 排队与运行阶段互斥，取消保留可用；保存配置等待任务完成。
	v.updateStatus({...status,last_task:{id:'q1',kind:'measure',status:'queued',stage:'queued'}});
	assert(v.buttons.test.disabled && v.buttons.restart.disabled && v.buttons.adopt_ps.disabled);
	assert(!v.buttons.cancel.disabled);
	calls.length=0;await v.handleSaveApply({},'0');
	assert(!calls.some(c=>c[0]==='super'));
	v.updateStatus({...status,last_task:{id:'q1',kind:'measure',status:'running',stage:'quick_ps',progress:{completed:2,total:5,valid:1}}});
	assert(text(v.progressNode).includes('PlayStation · 初筛'));
	assert(text(v.progressNode).includes('2/5'));

	// 状态失败保留已有日志并暂停修改，恢复后重新启用动作。
	v.updateStatus({status_error:'RPC unavailable'});
	assert(text(v.statusNode).includes('状态读取失败'));
	assert.strictEqual(v.logNode.value,status.log);
	assert(v.buttons.test.disabled);
	v.updateStatus(status);
	assert(!v.buttons.test.disabled);

	// 设备节点命中与分流证据分别显示；共享目标和旧连接保持准确措辞。
	const devices=[{ip:'192.168.1.20',name:'Xbox'},{ip:'192.168.1.21',name:'<script>device</script>'},{ip:'192.168.1.22',name:'Switch'}];
	const flow={device_ip:'192.168.1.20',target_ip:'8.8.4.4',target_port:443,groups:'cn',status:'matched',path:'unknown',
		speed_bps:12300000,observed_at:1700000000,activity:'downloading'};
	const observation={fresh:true,available:true,observed_at:1700000000,devices,
		flows:[flow,{...flow,device_ip:'192.168.1.21',target_ip:'1.1.1.1',groups:'com',path:'direct'},
			{...flow,device_ip:'192.168.1.22',target_ip:'9.9.9.9',status:'different'}],
		redirects:devices.map((device,index)=>({device_ip:device.ip,host:'device'+index+'.xboxlive.com',target_host:'assets1.xboxlive.cn',observed_at:1700000000}))};
	v.updateStatus({...status,observation});
	assert(text(v.downloadNode).includes('已命中当前节点')&&text(v.downloadNode).includes('分流路径待确认'));
	assert(text(v.downloadNode).includes('12.30 MB/s（估算）'));
	const picker=v.deviceDropdown;
	assert(picker.options.multiple&&picker.options.optional&&picker.options.select_placeholder==='全部设备');
	picker.setValue(['192.168.1.20']);picker.node.events['cbi-dropdown-change']();
	assert(!text(v.downloadNode).includes('1.1.1.1:443'));
	assert(!text(v.downloadNode).includes('直连已确认'));
	picker.setValue(['192.168.1.20','192.168.1.21']);picker.node.events['cbi-dropdown-change']();
	assert.deepStrictEqual(v.selectedDevices,['192.168.1.20','192.168.1.21']);
	assert(text(v.downloadNode).includes('8.8.4.4:443')&&text(v.downloadNode).includes('1.1.1.1:443'));
	assert(!text(v.downloadNode).includes('9.9.9.9:443'));
	assert(text(v.downloadNode).includes('device0.xboxlive.com')&&text(v.downloadNode).includes('device1.xboxlive.com'));
	assert(!text(v.downloadNode).includes('device2.xboxlive.com'));
	assert(!text(v.featureNode).includes('下载目标与配置存在差异'));
	v.updateStatus({...status,observation});
	assert.strictEqual(v.deviceDropdown,picker,'观测更新保留下拉框与选择');
	assert.deepStrictEqual(picker.getValue(),['192.168.1.20','192.168.1.21']);
	assert(text(v.deviceSelection).includes('2 台设备'));
	v.updateStatus({...status,observation:{...observation,devices:devices.slice(1)}});
	assert.deepStrictEqual(v.deviceDropdown.getValue(),['192.168.1.20','192.168.1.21']);
	assert(text(v.deviceDropdown.choices['192.168.1.20']).includes('等待观测'),'暂时离线的已选设备保留');
	v.updateStatus({status_error:'RPC unavailable'});
	v.deviceDropdown.setValue([]);v.deviceDropdown.node.events['cbi-dropdown-change']();
	assert(text(v.downloadNode).includes('暂停更新'));
	v.updateStatus({...status,observation});
	assert(text(v.downloadNode).includes('9.9.9.9:443'));
	v.updateStatus({...status,observation:{...observation,flows:[{...flow,status:'previous',shared_node:true}]}});
	assert(text(v.featureNode).includes('连接沿用旧节点'));
	assert(!text(v.featureNode).includes('共享节点命中'));
	assert(text(v.downloadNode).includes('暂停并继续'));
	v.updateStatus({...status,observation:{...observation,fresh:false}});
	assert(!text(v.downloadNode).includes('已命中当前节点'));
	v.selectDevices([]);
	v.updateStatus({...status,last_application:{status:'verified',router:'verified',same_configuration:false,checked_at:1700000000}});
	assert(text(v.statusNode).includes('配置已变化'));
	assert(text(v.featureNode).includes('等待检查'));

	// 日常、异常、完整任务各自可查看，错误内容以文本展示。
	records={source:'persisted',events:[{time:1700000000,level:'INFO',code:'DOWNLOAD_MATCHED',message:'192.168.1.20 → 8.8.4.4'}],
		errors:[{time:1700000000,level:'ERROR',kind:'measure',code:'FULL_TEST_FAILED',message:'<b>失败</b>'}],
		tasks:[{id:'old1',kind:'measure',status:'partial',code:'PARTIAL_RESULT',message:'部分平台失败',groups:{ps:{status:'failed',code:'FULL_TEST_FAILED',message:'复测失败'}}}]};
	await v.loadRecords();
	assert(text(v.eventsNode).includes('历史事件')&&text(v.eventsNode).includes('设备连接命中'));
	assert(text(v.taskDetails).includes('FULL_TEST_FAILED'));
	v.recordFilter.value='errors';v.updateRecords(records);
	assert(text(v.eventsNode).includes('<b>失败</b>'));
	assert(!text(v.eventsNode).includes('DOWNLOAD_MATCHED'));

	// DNS 报告按入口折叠，刷新保留手动展开状态。
	const report={environment:[
		{name:'依赖',level:'ok',detail:'就绪'},
		{name:'A assets1.xboxlive.cn @127.0.0.1:5335',level:'ok',expected:'9.9.9.9',observed:'9.9.9.9'},
		{name:'SRV assets-srvs.xboxlive.com @127.0.0.1:53',level:'fail',detail:'<b>异常</b>'}
	]};
	v.updateEnvironment(report);
	let dns=v.envNode.children.slice(1).map(row=>row.children[0].children[0]);
	assert(!dns[0].open&&dns[1].open);
	dns[0].attrs.toggle({target:{open:true}});v.updateEnvironment(report);
	assert(v.envNode.children[1].children[0].children[0].open);
	assert(text(v.envNode).includes('<b>异常</b>'));

	// 国际策略互斥并联动国内优选；测试资源对关闭平台保持可编辑。
	const mode=options.find(o=>o.name==='_international_mode');
	assert.deepStrictEqual(mode.choices.map(c=>c[0]),['off','optimize','redirect']);
	mode.write('main','redirect');
	assert.strictEqual(config.redirect_cn,'1');assert.strictEqual(config.optimize_com,'0');assert.strictEqual(config.optimize_cn,'1');
	mode.write('main','optimize');assert.strictEqual(config.redirect_cn,'0');assert.strictEqual(config.optimize_com,'1');
	for(const group of ['cn','com','store','ps','xbox_old','switch']){
		const resource=options.find(o=>o.name==='test_url_'+group);
		assert(resource&&resource.tab==='test');
		assert(!resource.dependencies.some(d=>d[0]==='optimize_'+group));
		assert(v.buttons['test_'+group]);
	}
	const switchLink=options.find(o=>o.name==='verification_urls_switch');
	assert.strictEqual(switchLink.validate('main',''),true);
	assert.strictEqual(switchLink.validate('main','https://atum.hac.lp1.d4c.nintendo.net/game?token=value'),true);
	for(const url of ['https://evil.example/game','https://ctest-dl-lp1.cdn.nintendo.net/30m','https://user@atum.hac.lp1.d4c.nintendo.net/game'])
		assert.notStrictEqual(switchLink.validate('main',url),true);
	const frequency=options.find(o=>o.name==='schedule_type');
	assert.deepStrictEqual(frequency.choices.map(c=>c[0]),['off','daily','weekly','monthly']);
	assert.strictEqual(frequency.cfgvalue('main'),'weekly');
	const scheduleTime=options.find(o=>o.name==='schedule_time');
	assert.strictEqual(scheduleTime.cfgvalue('main'),'01:00');
	for(const value of ['00:00','23:59'])assert.strictEqual(scheduleTime.validate('main',value),true);
	for(const value of ['24:00','5:15','12:60'])assert.notStrictEqual(scheduleTime.validate('main',value),true);
	assert(options.find(o=>o.name==='health_interval').choices.some(c=>c[0]==='40'&&c[1].includes('00、40')));
	assert.strictEqual(options.find(o=>o.name==='log_mode').tab,'records');

	// 首页在运行时快速发现新下载，其他稳定页减少轮询。
	v.updateStatus(status);assert(v.nextStatusPoll<Date.now()+6000);
	v.activeTab='functions';v.updateStatus(status);assert(v.nextStatusPoll>Date.now()+29000);
	calls.length=0;await pollStatus();assert(!calls.some(c=>c[0]==='status'));
	v.nextStatusPoll=0;await pollStatus();assert(calls.some(c=>c[0]==='status'));

	// 导出等待对应任务终态；UCI 提交完成后才应用。
	v.pendingExport='export1';calls.length=0;
	v.updateStatus({...status,last_task:{id:'export1',kind:'report',status:'queued'}});
	assert.strictEqual(v.pendingExport,'export1');
	v.updateStatus({...status,report_ready:true,last_task:{id:'export1',kind:'report',status:'success',code:'REPORT_READY'}});
	await tick();assert.strictEqual(v.pendingExport,null);
	assert(downloads.includes('/tmp/xbox-accel/diagnostic-report.json'));
	v.updateStatus(status);calls.length=0;
	await v.handleSaveApply({},'0');
	assert(calls.some(c=>c[0]==='super'));assert(!calls.some(c=>c[0]==='action'));
	listeners.get('uci-applied')();await tick();
	assert(calls.some(c=>c[0]==='action'&&c[1]==='apply'));
	assert(!listeners.has('uci-applied'));

	// 载入请求被浏览器中断：自动重试后完整渲染，配置与状态都取得。
	v.retryDelay=1;faults.uci=2;faults.status=1;
	await v.render(await v.load());
	await tick();await v.loadStatus();
	assert.strictEqual(faults.uci,0);assert.strictEqual(faults.status,0);
	assert(!v.loadFailed&&text(v.statusNode).includes('加速运行中')&&!v.buttons.test.disabled,'短暂中断后页面完整渲染');
	// 状态请求挂起时，配置和四个标签先渲染；轮询复用请求，取得状态后开放操作。
	let resolveStatus;
	deferredStatus=new Promise(resolve=>{resolveStatus=resolve;});calls.length=0;
	const loading=await v.load();
	assert(!calls.some(call=>call[0]==='status'),'载入配置阶段只读取 UCI');
	const loadingPage=await v.render(loading),request=v.statusRequest;
	assert(find(loadingPage,'xbox-accel-status')&&!v.loadFailed&&text(v.statusNode).includes('正在读取运行状态'));
	assert(v.buttons.test.disabled);
	assert.strictEqual(v.loadStatus(),request);
	assert.strictEqual(calls.filter(call=>call[0]==='status').length,1,'挂起的状态请求只发送一次');
	deferredStatus=null;resolveStatus(status);await request;
	assert(v.statusValid&&!v.buttons.test.disabled);
	// 新页面取得状态后，旧页面的迟到响应继续归属旧请求。
	deferredStatus=new Promise(resolve=>{resolveStatus=resolve;});
	await v.render(await v.load());const oldRequest=v.statusRequest;
	deferredStatus=null;await v.render(await v.load());await v.loadStatus();
	resolveStatus({...status,enabled:false});await oldRequest;
	assert.strictEqual(v.latestStatus.enabled,true,'保留新页面取得的状态');
	// 首页把已完成任务作为历史显示；完整报告在用户展开验收详情时读取。
	status.last_task={id:'existing-task',kind:'verify',status:'success',started_at:1};
	status.last_application={status:'verified',available:true,checked_at:2};
	calls.length=0;const downloaded=downloads.length;
	await v.render(await v.load());await v.loadStatus();
	assert(!calls.some(call=>call[0]==='records'),'首页首次状态继续展示历史摘要');
	assert.strictEqual(downloads.length,downloaded,'首页只展示验收摘要');
	v.activeTab='records';await v.loadApplicationReportIfOpen();
	assert.strictEqual(downloads.length,downloaded,'收起的验收区保留摘要');
	v.disclosures['xbox-accel-diagnostics'].open=true;
	await v.disclosures['xbox-accel-diagnostics'].attrs.toggle();
	assert.strictEqual(downloads.length,downloaded+1,'展开验收区读取一次完整报告');
	v.activeTab='overview';v.updateStatus({...status,last_task:{...status.last_task,id:'new-task'}});await tick();
	assert.strictEqual(downloads.length,downloaded+1,'总览的新任务完成时继续使用验收摘要');
	delete status.last_task;delete status.last_application;
	// 持续中断：显示可恢复页面并隐藏保存按钮，替代停留在“正在载入视图”。
	faults.uci=Infinity;
	const failed=await v.render(await v.load());
	assert(v.loadFailed&&text(failed).includes('XHR request aborted by browser')&&text(failed).includes('重新加载'));
	assert.strictEqual(text(v.addFooter()),'','失败页面不提供保存');
	await find(failed,'xbox-accel-reload').attrs.click();
	assert(v.loadFailed&&find(viewNode,'xbox-accel-load-error'),'仍然失败时保留可重试页面');
	// 连接恢复后原地重新载入，补回保存按钮。
	faults.uci=0;calls.length=0;
	await find(viewNode,'xbox-accel-reload').attrs.click();
	assert(!v.loadFailed&&!find(viewNode,'xbox-accel-load-error')&&find(viewNode,'xbox-accel-status'));
	assert(calls.some(c=>c[0]==='super'&&c[1]==='addFooter'));
	// 配置超时清理失败缓存后再读一次；连续超时给出可恢复页面。
	faults.error=new Error('XHR request timed out');faults.uci=1;
	await v.render(await v.load());assert(!v.loadFailed&&faults.uci===0);
	faults.uci=Infinity;calls.length=0;
	assert(find(await v.render(await v.load()),'xbox-accel-load-error')&&v.loadFailed);
	assert.strictEqual(calls.filter(call=>call[0]==='uci-load').length,2,'配置超时最多读两次');
	assert.strictEqual(calls.filter(call=>call[0]==='uci-unload').length,2,'每次失败清理 UCI 缓存');
	faults.uci=0;faults.error=null;
	// 表单载入中断和超时同样恢复；持续超时有上限；程序错误照常抛出。
	faults.render=1;await v.render(await v.load());assert(!v.loadFailed&&faults.render===0);
	faults.render=1;faults.error=new Error('XHR request timed out');
	await v.render(await v.load());assert(!v.loadFailed&&faults.render===0);
	faults.render=2;
	assert(find(await v.render(await v.load()),'xbox-accel-load-error')&&v.loadFailed);
	assert.strictEqual(faults.render,0,'表单超时最多读两次');
	faults.render=1;faults.error=new TypeError('boom');
	await assert.rejects(v.load().then(data=>v.render(data)),/boom/);
	faults.error=null;await v.render(await v.load());assert(!v.loadFailed);
	// 轮询：偶发中断保留上次状态和可用操作，连续三次才提示；非中断类错误立即提示。
	const pollNow=()=>{v.nextStatusPoll=0;return pollStatus();};
	faults.status=2;await pollNow();await pollNow();
	assert(text(v.statusNode).includes('加速运行中')&&!v.buttons.test.disabled);
	await pollNow();faults.status=3;await pollNow();await pollNow();
	assert(text(v.statusNode).includes('加速运行中'),'成功读取后重新计数');
	await pollNow();
	assert(text(v.statusNode).includes('状态读取失败')&&v.buttons.test.disabled);
	await pollNow();assert(text(v.statusNode).includes('加速运行中')&&!v.buttons.test.disabled);
	faults.status=2;faults.error=new Error('XHR request timed out');await pollNow();await pollNow();
	assert(v.statusValid&&!v.buttons.test.disabled,'短暂超时保留有效状态');
	await pollNow();faults.error=null;
	faults.status=1;faults.error=new Error('Object not found');await pollNow();
	assert(text(v.statusNode).includes('Object not found'));faults.error=null;
	console.log('LuCI user workflow: PASS');
})().catch(error=>{console.error(error);process.exit(1);});
