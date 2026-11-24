'use strict';
'require view';
'require form';
'require uci';
'require rpc';
'require poll';
'require ui';
'require dom';
'require fs';

var callStatus = rpc.declare({object:'xbox-accel',method:'status',expect:{'':{}}});
var callEnvironment = rpc.declare({object:'xbox-accel',method:'environment',expect:{'':{}}});
var callRecords = rpc.declare({object:'xbox-accel',method:'records',expect:{'':{}}});
var callAction = rpc.declare({object:'xbox-accel',method:'action',params:['command','groups','result_id'],expect:{'':{}}});
var PLATFORMS = [
	{id:'cn',label:'Xbox 国内',hosts:['assets1.xboxlive.cn'],description:'优选 Xbox 国内下载节点。'},
	{id:'com',label:'Xbox 国际',hosts:['assets1.xboxlive.com'],description:'选择国际下载使用的方式。'},
	{id:'store',label:'微软商店',hosts:['dl.delivery.mp.microsoft.com','tlu.dl.delivery.mp.microsoft.com','1d.tlu.dl.delivery.mp.microsoft.com'],description:'优选微软商店和 Xbox 应用下载节点。'},
	{id:'ps',label:'PlayStation',hosts:['gst.prod.dl.playstation.net','gs2.ww.prod.dl.playstation.net','zeus.dl.playstation.net','ares.dl.playstation.net'],description:'优选 PlayStation 下载节点。'},
	{id:'xbox_old',label:'Xbox 老游戏下载',hosts:['dlassets.xboxlive.cn','dlassets2.xboxlive.cn'],description:'优选旧版游戏资源的国内下载节点。'},
	{id:'switch',label:'Switch',hosts:['ctest-dl-lp1.cdn.nintendo.net','ctest-dl.p01.lp1.ctest.srv.nintendo.net','atum.hac.lp1.d4c.nintendo.net','nemof.p01.lp1.nemo.srv.nintendo.net','nemof.hac.lp1.nemo.srv.nintendo.net'],description:'通过公开资源选优，应用到游戏下载域名。'}
];
var GROUP_LABELS = {};
PLATFORMS.forEach(function(p){GROUP_LABELS[p.id]=p.label;});
var LEVELS = {ok:['正常','#2e7d32'],info:['信息','#607d8b'],warn:['注意','#b35b00'],fail:['异常','#c62828'],unknown:['待确认','#607d8b'],skipped:['已跳过','#9e9e9e']};
var TASK_TYPES = {test:'自动测速',measure:'独立测速',adopt:'采用测速结果',health:'健康检查',apply:'应用配置',restart:'重启服务',verify:'检查生效',report:'导出诊断报告'};
var TASK_STATES = {none:'尚无记录',queued:'等待执行',running:'进行中',success:'完成',partial:'部分完成',failed:'失败',cancelled:'已取消',interrupted:'异常中断',skipped:'已跳过',unknown:'待确认'};
var APPLY_STATES = {verified:'验收通过',partial:'部分通过',failed:'验收异常',unknown:'等待确认',skipped:'已跳过',disabled:'功能已关闭',none:'等待验收'};
var FLOW_STATES = {matched:'已命中当前节点',previous:'连接沿用旧节点',different:'下载目标与配置存在差异',unidentified:'其他连接，平台待确认'};
var PATH_STATES = {direct:'直连已确认',redirect_entry:'已进入重定向服务',intercepted:'观察到本地转发',unknown:'分流路径待确认'};
var EVENT_TITLES = {TASK_FINAL:'任务结束',NODE_SELECTED:'运行节点已更新',MEASUREMENT_READY:'测速结果已保存',ROUTER_VERIFIED:'配置验收通过',DISABLE_VERIFIED:'停用清理完成',DOWNLOAD_MATCHED:'设备连接命中当前节点',DOWNLOAD_PREVIOUS_NODE:'设备连接沿用旧节点',DOWNLOAD_TARGET_DIFFERENT:'设备下载目标存在差异',DOWNLOAD_CONNECTION_ENDED:'设备连接结束',OBSERVATION_STARTED:'下载观测启动',HTTP_REDIRECT_OBSERVED:'设备已请求国内重定向',HISTORY_PERSIST_FAILED:'历史记录保存异常'};
var CSS = '.xa-product .xa-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}' +
	'.xa-product .xa-card{border:1px solid var(--border-color,#d9dfe5);border-radius:8px;padding:16px;margin:12px 0;min-width:0}' +
	'.xa-product .xa-card h4{margin:0 0 12px;font-size:15px}.xa-product .xa-card p{margin:7px 0;overflow-wrap:anywhere}' +
	'.xa-product .xa-actions{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.xa-product .xa-muted{color:var(--text-color-secondary,#677580)}' +
	'.xa-product .xa-ok{color:#2e7d32}.xa-product .xa-warn{color:#b35b00}.xa-product .xa-fail{color:#c62828}' +
	'.xa-product details.xa-disclosure{border:1px solid var(--border-color,#d9dfe5);border-radius:6px;padding:10px;margin:12px 0}' +
	'.xa-product details.xa-disclosure>summary{cursor:pointer;font-weight:600}.xa-product .xa-table-wrap{overflow-x:auto}' +
	'.xa-product .xa-chips{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}.xa-product .xa-chips label{padding:8px;border:1px solid var(--border-color,#d9dfe5);border-radius:6px;cursor:pointer}' +
	'.xa-product .xa-chips input{margin-right:6px}.xa-product .xa-progress{margin:12px 0;padding:12px;background:var(--background-color-low,#f4f6f8);border-radius:6px}' +
	'.xa-product .xa-card .cbi-section{margin:0;border:0;padding:0}.xa-product [data-widget="CBI.ContainerValue"]{display:block;width:100%;min-width:0;margin:12px 0}' +
	'.xa-product [data-widget="CBI.ContainerValue"]>.cbi-value-field{margin:0;width:100%}.xa-product [data-widget="CBI.ContainerValue"]>.cbi-value-title{display:none}' +
	'.xa-product [data-widget="CBI.ContainerValue"] .xa-card,.xa-product [data-widget="CBI.ContainerValue"] .xa-disclosure{margin:0}.xa-product .xa-disclosure>.cbi-section{margin:12px 0 0;padding:0;border:0}' +
	'.xa-product div[data-tab="functions"][data-tab-active="true"]{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding-top:12px;align-items:start}' +
	'.xa-product div[data-tab="functions"]>[data-widget="CBI.ContainerValue"]{margin:0}.xa-product [data-name="_basic"],.xa-product [data-name="_automatic"],.xa-product [data-name="_compatibility"]{grid-column:1/-1}' +
	'.xa-product [data-name^="_feature_"] .cbi-value{display:block;margin-bottom:10px}.xa-product [data-name^="_feature_"] .cbi-value-title{display:block;text-align:left;padding:0;width:auto}' +
	'.xa-product [data-name^="_feature_"] .cbi-value-field{display:block;margin:6px 0 0;min-width:0}.xa-product [data-name^="_feature_"] .cbi-dropdown,.xa-product [data-name^="_feature_"] .cbi-input-select{width:100%;min-width:0;max-width:100%}.xa-product .xa-small{font-size:12px}' +
	'@media(max-width:720px){.xa-product div[data-tab="functions"][data-tab-active="true"]{grid-template-columns:1fr}}' +
	'@media(max-width:600px){.xa-product .xa-grid{grid-template-columns:1fr}.xa-product .xa-actions .btn{flex:1 1 auto}.xa-product .xa-card{padding:12px}}';

function formatTime(epoch) {
	if(!epoch)return '尚无记录';
	var d=new Date(epoch*1000),pad=function(n){return(n<10?'0':'')+n;};
	return '%s-%s %s:%s'.format(pad(d.getMonth()+1),pad(d.getDate()),pad(d.getHours()),pad(d.getMinutes()));
}
function speedText(bytes){return bytes>0?(bytes/1000000).toFixed(2)+' MB/s':'等待取样';}
function busyTask(status){var task=status.last_task;return !!status.testing||!!(task&&['queued','running'].indexOf(task.status)!==-1);}
function groupEnabled(status,group){
	if(group==='cn')return !!status.optimize_cn;
	if(group==='com')return status.mode==='optimize'||status.mode==='redirect';
	return !!(status.platforms&&status.platforms[group]&&status.platforms[group].enabled);
}
function groupIp(status,group){
	if(group==='com'&&status.mode==='redirect')return status.redirect_ip||'';
	return status[group+'_ip']||(status.platforms&&status.platforms[group]||{}).ip||'';
}
function taskText(task,detailed){
	if(!task||task.status==='none')return '尚无记录';
	var text=(TASK_TYPES[task.kind]||'任务')+' · '+(TASK_STATES[task.status]||'待确认');
	if(task.finished_at)text+=' · '+formatTime(task.finished_at);
	if(detailed===false)return text;
	text+='\n'+(task.message||'')+'\n任务：'+(task.id||'?')+' · 阶段：'+(task.stage||'?')+' · 代码：'+(task.code||'?');
	PLATFORMS.forEach(function(p){var r=task.groups&&task.groups[p.id];if(r&&r.code!=='NOT_REQUESTED')text+='\n'+p.label+'：'+(TASK_STATES[r.status]||r.status)+' · '+r.code+' · '+r.message;});
	if(task.application)text+='\n应用结果：'+(APPLY_STATES[task.application.status]||'待确认')+' · '+task.application.code;
	if(task.persistence_warning)text+='\n历史保存异常：当前结果可查看，请及时导出记录。';
	return text;
}
function validatePlatformUrl(platform,value,games){
	if(!value)return true;
	var match=/^https?:\/\/([^/]+)\/[^\s#\\]+$/.exec(value),hosts=games?platform.hosts.slice(2):platform.hosts;
	if(value.length>4096||/[\s\x00-\x1f\x7f]/.test(value)||!match||hosts.indexOf(match[1])===-1)return '请填写该平台下载域名下的 HTTP(S) 资源链接，保留完整路径和参数';
	if(platform.id==='cn'||platform.id==='com')return value.indexOf('http://assets1.xboxlive.'+platform.id+'/')===0?true:'请使用平台默认 HTTP 下载域名';
	if(platform.id==='switch'&&!games&&platform.hosts.slice(0,2).indexOf(match[1])===-1)return '公开测速使用 ctest 下载域名，游戏资源填写在增强校验链接中';
	return true;
}
// 手机浏览器切换网络、转入后台，或路由器瞬时繁忙时，请求会在取得响应前中断；这类失败可以直接重试。
function interruptedError(err){return /XHR request aborted|No related RPC reply|HTTP error 50[234]/i.test(String(err&&err.message||err||''));}
function networkError(err){return interruptedError(err)||/XHR request timed out/i.test(String(err&&err.message||err||''));}
function retryInterrupted(fn,attempts,delay){
	return Promise.resolve().then(fn).catch(function(err){
		if(attempts<=1||!interruptedError(err))throw err;
		return new Promise(function(resolve){setTimeout(resolve,delay);}).then(function(){return retryInterrupted(fn,attempts-1,delay*2);});
	});
}

return view.extend({
	retryDelay:500,
	// LuCI 在 load/render 被拒绝后停留在“正在载入视图”；请求中断时先重试，仍失败则渲染可恢复的页面。
	load:function(){
		var delay=this.retryDelay;
		return Promise.all([
			// LuCI 的 uci.load 会缓存失败的请求；先卸载，后续重试与“重新加载”才会真正重新读取。
			retryInterrupted(function(){return uci.load('xbox-accel').catch(function(err){if(uci.unload)uci.unload('xbox-accel');throw err;});},4,delay).then(function(){return null;},function(err){return{load_error:err};}),
			retryInterrupted(function(){return callStatus();},3,delay).catch(function(err){return{status_error:err.message};})
		]);
	},
	render:function(data){
		var self=this,failure=data&&data[0]&&data[0].load_error;
		this.loadFailed=false;
		if(failure)return this.renderLoadFailure(failure);
		return retryInterrupted(function(){return self.renderView(data);},3,this.retryDelay).catch(function(err){
			if(!networkError(err))throw err;
			return self.renderLoadFailure(err);
		});
	},
	renderView:function(data){
		var self=this;this.statusFailures=0;
		this.buttons={};this.actionButtons=[];this.configOptions={};this.groupInputs={};this.resultRows={};this.environmentGroups={};
		this.activeTab='overview';this.nextStatusPoll=0;this.latestStatus=data[1]||{};
		this.lastFinishedTask=this.latestStatus.last_task&&this.latestStatus.last_task.id;
		this.statusNode=E('table',{'class':'table',id:'xbox-accel-status'});
		this.featureNode=E('div',{'class':'xa-grid',id:'xbox-accel-features'});
		this.downloadNode=E('div',{id:'xbox-accel-downloads'});
		this.progressNode=E('div',{'class':'xa-progress',id:'xbox-accel-progress'});
		this.recordProgressNode=E('div',{'class':'xa-progress',id:'xbox-accel-record-progress'});
		this.envNode=E('table',{'class':'table',id:'xbox-accel-environment'});
		this.envNote=E('p',{'class':'xa-muted'});this.logNote=E('p',{'class':'xa-muted'});
		this.taskDetails=E('div',{id:'xbox-accel-task-details'});this.eventsNode=E('div',{id:'xbox-accel-events'});
		this.logNode=E('textarea',{id:'xbox-accel-log',readonly:'readonly',wrap:'off',rows:14,style:'width:100%;font-family:monospace;font-size:12px'});
		this.selectedDevice='';this.disclosures={};
		this.deviceSelect=E('select',{id:'xbox-accel-device',change:function(){self.selectedDevice=this.value;self.updateDownloads(self.latestStatus);}});
		this.recordFilter=E('select',{id:'xbox-accel-record-filter',change:function(){self.updateRecords(self.latestRecords||{});}},[E('option',{value:'all'},['全部事件']),E('option',{value:'errors'},['异常事件'])]);
		this.button=function(label,command,args,confirmText,predicate){
			var node=E('button',{'class':'btn cbi-button cbi-button-action',type:'button',click:function(){
				if(node.disabled)return Promise.resolve();
				if(confirmText&&!window.confirm(typeof confirmText==='function'?confirmText():confirmText))return Promise.resolve();
				if(node.classList)node.classList.add('spinning');
				return self.runAction(command,typeof args==='function'?args():args).finally(function(){
					if(node.classList)node.classList.remove('spinning');
					self.updateActionButtons();
				});
			}},[label]);
			self.buttons[command]=node;self.actionButtons.push({node:node,command:command,predicate:predicate});return node;
		};
		var linkButton=function(label,tab,group){return E('button',{type:'button','class':'btn cbi-button',click:ui.createHandlerFn(self,function(){if(group)self.selectGroups([group]);self.navigateTab(tab);})},[label]);};
		var disclosure=function(label,children,id){
			var node=E('details',{'class':'xa-disclosure',id:id},[E('summary',{},[label])].concat(children));
			if(id)self.disclosures[id]=node;return node;
		};
		var m=this.map=new form.Map('xbox-accel','Xbox 下载加速','选择下载功能，按需测速，在设备下载时查看节点是否生效。');
		var s=m.section(form.NamedSection,'main','main');s.addremove=false;
		s.tab('overview','总览');s.tab('functions','加速功能');s.tab('test','测速');s.tab('records','运行记录');
		var panel=function(tab,name,render){var o=s.taboption(tab,form.DummyValue,name);o.write=function(){};o.remove=function(){};o.render=render;return o;};
		var section=function(tab,name,title,collapsed){
			var o=s.taboption(tab,form.SectionValue,name,form.NamedSection,'main','main',collapsed?'':title);o.subsection.addremove=false;
			var original=o.renderWidget;o.renderWidget=function(){var args=arguments;return Promise.resolve(original.apply(this,args)).then(function(node){return collapsed?disclosure(title,[node],name):E('div',{'class':'xa-card'},[node]);});};
			return o.subsection;
		};
		var option=function(section,type,name,title,description){var o=section.option(type,name,title,description);self.configOptions[name]=o;return o;};
		var flag=function(section,name,title,def,description){var o=option(section,form.Flag,name,title,description);o.default=def;o.rmempty=false;return o;};
		var number=function(section,name,title,def,min,max,description){var o=option(section,form.Value,name,title,description);o.default=def;o.placeholder=def;o.datatype='and(uinteger,range(%d,%d))'.format(min,max);o.rmempty=false;return o;};
		var settings,o;
		panel('overview','_overview',function(){return E('div',{},[
			self.statusNode,E('div',{'class':'xa-actions'},[linkButton('选择加速功能','functions'),linkButton('按需测速','test'),self.button('检查生效','verify')]),self.featureNode,
			E('h3',{},['真实下载状态']),E('p',{'class':'xa-muted'},['开始设备下载后，自动观察实际连接与当前节点的匹配情况。']),
			E('label',{},['下载设备 ',self.deviceSelect]),self.downloadNode
		]);});
		settings=section('functions','_basic','加速服务',false);
		flag(settings,'enabled','启动加速','0','按已选择的功能应用下载节点。');
		o=option(settings,form.ListValue,'isp','运营商');
		[['auto','自动'],['telecom','中国电信'],['unicom','中国联通'],['mobile','中国移动']].forEach(function(pair){o.value(pair[0],pair[1]);});o.default='auto';o.rmempty=false;
		PLATFORMS.forEach(function(p){
			settings=section('functions','_feature_'+p.id,p.label,false);
			if(p.id==='com'){
				o=option(settings,form.ListValue,'_international_mode','下载方式',p.description);
				o.value('off','默认解析');o.value('optimize','国际节点优选');o.value('redirect','转到国内下载（HTTP）');o.rmempty=false;
				o.cfgvalue=function(){return uci.get('xbox-accel','main','redirect_cn')==='1'?'redirect':uci.get('xbox-accel','main','optimize_com')==='0'?'off':'optimize';};
				o.write=function(id,value){
					uci.set('xbox-accel',id,'optimize_com',value==='optimize'?'1':'0');uci.set('xbox-accel',id,'redirect_cn',value==='redirect'?'1':'0');
					if(value==='redirect')uci.set('xbox-accel',id,'optimize_cn','1');
				};
				o.remove=function(id){this.write(id,'off');};
			}else flag(settings,'optimize_'+p.id,'启用下载优选',p.id==='cn'?'1':'0',p.description);
			o=settings.option(form.DummyValue,'_test_link_'+p.id);o.write=function(){};o.remove=function(){};
			o.render=function(){return E('div',{'class':'xa-actions'},[linkButton('单独测速','test',p.id)]);};
		});
		settings=section('functions','_automatic','自动维护',true);
		flag(settings,'autostart','开机恢复加速','1','路由器启动后恢复已启用的下载功能。');
		var oldPlan=uci.get('xbox-accel','main','cron')||'15 5 * * *',parts=oldPlan.trim().split(/\s+/),initial={type:'legacy',time:'05:15',weekday:'0',monthday:'1'};
		if(parts.length===5&&/^\d+$/.test(parts[0])&&Number(parts[0])<60&&/^\d+$/.test(parts[1])&&Number(parts[1])<24&&parts[3]==='*'){
			initial.time=('0'+Number(parts[1])).slice(-2)+':'+('0'+Number(parts[0])).slice(-2);
			if(parts[2]==='*'&&parts[4]==='*')initial.type='daily';
			else if(parts[2]==='*'&&/^[0-7]$/.test(parts[4])){initial.type='weekly';initial.weekday=String(Number(parts[4])%7);}
			else if(/^\d+$/.test(parts[2])&&Number(parts[2])>=1&&Number(parts[2])<=31&&parts[4]==='*'){initial.type='monthly';initial.monthday=String(Number(parts[2]));}
		}
		var scheduleDefault=function(o,value){o.default=value;o.rmempty=false;o.cfgvalue=function(id){return uci.get('xbox-accel',id,this.option||this.name)||value;};};
		o=option(settings,form.ListValue,'schedule_type','定时更新节点','定时测速自动采用优选结果，时间使用路由器系统时区。');
		[['off','仅手动测速'],['daily','每天'],['weekly','每周'],['monthly','每月']].forEach(function(pair){o.value(pair[0],pair[1]);});
		if(initial.type==='legacy'||uci.get('xbox-accel','main','schedule_type')==='legacy')o.value('legacy','保留旧版自定义计划');scheduleDefault(o,initial.type);
		o=option(settings,form.Value,'schedule_time','测速时间（HH:MM）');scheduleDefault(o,initial.time);o.placeholder='05:15';
		['daily','weekly','monthly'].forEach(function(type){o.depends('schedule_type',type);});
		o.validate=function(id,value){return /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value)?true:'请填写 00:00 到 23:59 的时间';};
		o=option(settings,form.ListValue,'schedule_weekday','星期几');['星期日','星期一','星期二','星期三','星期四','星期五','星期六'].forEach(function(day,i){o.value(String(i),day);});
		scheduleDefault(o,initial.weekday);o.depends('schedule_type','weekly');
		o=option(settings,form.Value,'schedule_monthday','每月几号','29 至 31 号在缺少对应日期的月份跳过。');scheduleDefault(o,initial.monthday);o.depends('schedule_type','monthly');
		o.validate=function(id,value){return /^(?:[1-9]|[12]\d|3[01])$/.test(value)?true:'请填写 1 到 31 的日期';};
		o=option(settings,form.ListValue,'health_interval','节点异常自动恢复','检查当前节点的资源可用性，连续失败时重新测速。');
		var healthPlans=[['0','关闭'],['5','每 5 分钟'],['10','每 10 分钟'],['15','每 15 分钟'],['20','每 20 分钟'],['30','每 30 分钟']],currentHealth=uci.get('xbox-accel','main','health_interval');
		if(currentHealth&&!healthPlans.some(function(pair){return pair[0]===currentHealth;})){
			var marks=[],step=Number(currentHealth);if(Number.isInteger(step)&&step>0&&step<60)for(var minute=0;minute<60;minute+=step)marks.push(('0'+minute).slice(-2));
			healthPlans.push([currentHealth,marks.length?'保留旧版：每小时 '+marks.join('、')+' 分':'保留旧版设置']);
		}
		healthPlans.forEach(function(pair){o.value(pair[0],pair[1]);});o.default='10';o.rmempty=false;
		settings=section('functions','_compatibility','网络兼容设置',true);
		flag(settings,'override_cdn_hosts','接管 Xbox 备用下载节点','1','覆盖主机通过 SRV 获取的备用下载域名。');
		flag(settings,'passwall_direct','节点加入 Passwall 直连集合','1','实际分流结果显示在总览。');
		flag(settings,'passwall_restart','自动同步 Passwall DNS 规则','1','应用功能变化时同步已识别的 DNS 副本。');
		o=option(settings,form.Value,'dnsmasq_instance','主 DNS 实例','通常保留默认值，更换实例前先关闭加速并保存。');o.default='@dnsmasq[0]';o.rmempty=false;
		o.validate=function(id,value){return /^(@dnsmasq\[[0-9]+\]|[A-Za-z0-9_]+)$/.test(value)?true:'请填写 @dnsmasq[0] 或配置节名称';};
		number(settings,'resolver_port','解析端口','5335',1024,65535);number(settings,'ttl','解析有效期（秒）','30',5,600);
		o=option(settings,form.DynamicList,'fallback_dns','备用上游 DNS','使用公网 IPv4 地址。');o.default=['223.5.5.5','119.29.29.29'];o.datatype='ip4addr';o.rmempty=false;
		o=option(settings,form.Value,'redirect_ip','重定向入口地址','专用地址须经当前路由器到达，旁路由部署需设置相应路由。');o.default='192.0.2.1';o.datatype='ip4addr';o.rmempty=false;
		number(settings,'redirect_port','重定向服务端口','18081',1024,65535);
		panel('test','_measurement',function(){
			var chips=PLATFORMS.map(function(p){self.groupInputs[p.id]=E('input',{type:'checkbox',value:p.id});self.groupInputs[p.id].checked=groupEnabled(self.latestStatus,p.id);return E('label',{},[self.groupInputs[p.id],p.label]);});
			if(!PLATFORMS.some(function(p){return groupEnabled(self.latestStatus,p.id);}))self.groupInputs.cn.checked=true;
			var rows=[E('tr',{'class':'tr'},['平台','测速结果','测试时间','操作'].map(function(label){return E('th',{'class':'th'},[label]);}))];
			PLATFORMS.forEach(function(p){
				var result=E('td',{'class':'td'}),time=E('td',{'class':'td'});
				var adopt=self.button('采用结果','adopt_'+p.id,function(){return{result_id:(self.latestStatus.measurements&&self.latestStatus.measurements[p.id]||{}).id||''};},
					function(){return p.id==='com'?'采用该结果会启用国际节点优选，并更新国际下载方式。继续应用？':'采用该结果会启用 '+p.label+' 并应用选中的节点。继续应用？';},
					function(status){var record=status.measurements&&status.measurements[p.id];return !!record&&record.compatible!==false;});
				self.resultRows[p.id]={result:result,time:time,adopt:adopt};
				rows.push(E('tr',{'class':'tr'},[E('td',{'class':'td'},[p.label]),result,time,E('td',{'class':'td'},[self.button('单独测','test_'+p.id),' ',adopt])]));
			});
			return E('div',{},[
				E('p',{},['全部平台均可独立测速。测试使用已保存的设置，完成后可选择采用结果。']),
				E('div',{'class':'xa-chips',id:'xbox-accel-test-groups'},chips),
				E('div',{'class':'xa-actions'},[
					E('button',{type:'button','class':'btn',click:function(){self.selectGroups(PLATFORMS.filter(function(p){return groupEnabled(self.latestStatus,p.id);}).map(function(p){return p.id;}));}},['选择已开启平台']),
					E('button',{type:'button','class':'btn',click:function(){self.selectGroups(PLATFORMS.map(function(p){return p.id;}));}},['选择全部平台']),
					self.button('开始测速','test',function(){return{groups:self.selectedGroups().join(',')};}),self.button('取消任务','cancel')
				]),self.progressNode,E('div',{'class':'xa-table-wrap'},[E('table',{'class':'table',id:'xbox-accel-measurements'},rows)])
			]);
		});
		settings=section('test','_sampling','测速采样参数',true);
		number(settings,'max_candidates','每平台最多测试节点','60',1,100);number(settings,'quick_size_mb','初筛每节点下载上限（MiB）','4',1,128);
		number(settings,'quick_time','初筛时间上限（秒）','4',1,30);number(settings,'top_n','复测前几名','3',1,10);
		number(settings,'full_size_mb','复测每节点下载上限（MiB）','64',1,256);number(settings,'full_time','复测时间上限（秒）','10',1,60,'采样占用当前线路带宽与流量。');
		number(settings,'switch_threshold','自动更新的提升门槛（%）','15',0,100,'定时更新和异常恢复按此门槛切换节点。');
		PLATFORMS.forEach(function(p){
			settings=section('test','_resource_'+p.id,p.label+' 测速资源',true);
			if(p.id==='store'){o=option(settings,form.ListValue,'store_test_app','商店测试应用');o.value('store','微软商店应用');o.value('xbox','Xbox 应用');o.default='store';o.rmempty=false;}
			o=option(settings,form.Value,'test_url_'+p.id,'测速链接','留空使用内置资源。微软商店自动获取有效签名链接。');o.optional=true;o.validate=function(id,value){return validatePlatformUrl(p,value,false);};
			if(p.id==='ps'||p.id==='switch'){
				o=option(settings,form.DynamicList,'verification_urls_'+p.id,p.id==='switch'?'游戏增强校验链接（可选）':'分域名校验链接',p.id==='switch'?'留空使用默认节点映射；可填写 atum/nemof 实际游戏资源，最多 8 条。':'可补充分域名的实际下载资源，最多 8 条。');
				o.optional=true;o.validate=function(id,value){return validatePlatformUrl(p,value,p.id==='switch');};
			}
			if(p.id!=='cn'&&p.id!=='com'){o=option(settings,form.DynamicList,'extra_ips_'+p.id,'额外候选节点','可选公网 IPv4，与发现的候选一起测试。');o.optional=true;o.datatype='ip4addr';}
		});
		panel('test','_reset_results',function(){return disclosure('运行节点维护',[self.button('清除当前运行节点','reset',null,'清除后下载使用默认解析，测速历史保留。继续清除？')]);});
		panel('records','_records',function(){return E('div',{},[
			E('div',{'class':'xa-actions'},[self.recordFilter,E('button',{type:'button','class':'btn',click:ui.createHandlerFn(self,'loadRecords')},['刷新记录']),self.button('导出诊断报告','export')]),self.recordProgressNode,self.eventsNode,self.taskDetails,
			disclosure('验收与环境详情',[self.envNote,self.envNode,E('div',{'class':'xa-actions'},[self.button('重新检查生效','verify'),E('button',{type:'button','class':'btn',click:ui.createHandlerFn(self,'loadApplicationReport')},['查看最近验收']),E('button',{type:'button','class':'btn',click:ui.createHandlerFn(self,'refreshEnvironment')},['环境初查'])])],'xbox-accel-diagnostics'),
			disclosure('原始日志',[self.logNote,self.logNode],'xbox-accel-raw-log'),
			disclosure('维护操作',[E('div',{'class':'xa-actions'},[self.button('重新应用配置','apply'),self.button('重启服务','restart'),self.button('清空事件与日志','clear_log',null,'清空日常事件、异常事件和原始日志，任务及验收历史保留。继续清空？')])])
		]);});
		settings=section('records','_logging','日志采集设置',true);
		o=option(settings,form.ListValue,'log_mode','采集内容','日常事件自动保留，详细模式增加逐节点和逐域名诊断。');o.value('concise','日常摘要');o.value('detail','详细诊断');o.default='concise';o.rmempty=false;
		return m.render().then(function(formNode){
			self.formNode=formNode;
			var page=E('div',{'class':'xa-product'},[E('style',{},[CSS]),formNode]);
			if(formNode.querySelectorAll)formNode.querySelectorAll('.cbi-tabmenu a').forEach(function(a){a.addEventListener('click',function(){self.activeTab=a.parentElement.getAttribute('data-tab');self.nextStatusPoll=0;if(self.activeTab==='records')self.loadRecords();});});
			self.updateStatus(data[1]||{});self.updateRecords({});self.updateEnvironment({});
			poll.add(function(){if(Date.now()<self.nextStatusPoll)return Promise.resolve();return callStatus().then(function(status){self.statusFailures=0;self.updateStatus(status);if(self.activeTab==='records')return self.loadRecords();}).catch(function(err){
				// 单次请求中断时保留上次状态，下个周期重读；连续中断再提示并锁定操作。
				self.statusFailures=(self.statusFailures||0)+1;
				if(interruptedError(err)&&self.statusFailures<3)return;
				self.updateStatus({status_error:err.message});
			});},5);
			if(self.latestStatus.last_application&&self.latestStatus.last_application.available)self.loadApplicationReport();
			return page;
		});
	},
	// 载入阶段的请求中断后给出可恢复的页面，原地重新读取配置与状态。
	renderLoadFailure:function(err){
		var self=this,message=E('p',{'class':'xa-warn'},[String(err&&err.message||err||'未知错误')]);
		var button=E('button',{'class':'btn cbi-button cbi-button-action',type:'button',id:'xbox-accel-reload',click:function(){
			if(button.disabled)return Promise.resolve();
			button.disabled=true;if(button.classList)button.classList.add('spinning');
			return self.reloadView().catch(function(error){
				button.disabled=false;if(button.classList)button.classList.remove('spinning');
				message.textContent=String(error&&error.message||error);
			});
		}},['重新加载']);
		this.loadFailed=true;
		return E('div',{'class':'xa-product'},[E('style',{},[CSS]),E('div',{'class':'xa-card',id:'xbox-accel-load-error'},[
			E('h4',{},['页面数据读取中断']),message,
			E('p',{'class':'xa-muted'},['已自动重试。手机切换网络、浏览器转入后台或路由器繁忙时会出现这种情况；页面读取失败不影响已应用的加速配置。']),
			E('div',{'class':'xa-actions'},[button])
		])]);
	},
	reloadView:function(){
		var self=this;
		return Promise.resolve(this.load()).then(function(data){return self.render(data);}).then(function(nodes){
			var target=document.getElementById('view');
			if(!target)return;
			dom.content(target,nodes);dom.append(target,self.addFooter());
		});
	},
	addFooter:function(){return this.loadFailed?E('div',{}):this.super('addFooter',[]);},
	navigateTab:function(tab){this.activeTab=tab;this.nextStatusPoll=0;var anchor=this.formNode&&this.formNode.querySelector&&this.formNode.querySelector('.cbi-tabmenu [data-tab="'+tab+'"] a');if(anchor)anchor.click();},
	selectedGroups:function(){var self=this;return PLATFORMS.filter(function(p){return self.groupInputs[p.id]&&self.groupInputs[p.id].checked;}).map(function(p){return p.id;});},
	selectGroups:function(groups){var self=this;PLATFORMS.forEach(function(p){if(self.groupInputs[p.id])self.groupInputs[p.id].checked=groups.indexOf(p.id)!==-1;});},
	updateStatus:function(status){
		var self=this,invalid=!!status.status_error||typeof status.enabled!=='boolean'||typeof status.running!=='boolean',task=status.last_task,busy=busyTask(status),application=status.last_application;
		if(invalid){
			this.statusValid=false;
			dom.content(this.statusNode,[E('tr',{'class':'tr'},[E('td',{'class':'td xa-warn'},['状态读取失败：'+(status.status_error||'响应格式异常')])])]);
			dom.content(this.featureNode,[E('p',{'class':'xa-warn'},['功能状态等待重新读取，最近验收与日志保留在运行记录。'])]);
			dom.content(this.downloadNode,[E('p',{'class':'xa-warn'},['下载观测暂停更新，请等待状态恢复。'])]);
			this.recordProgressNode.hidden=false;dom.content(this.recordProgressNode,[E('p',{},['状态读取等待恢复，最近记录保留。'])]);
			this.actionButtons.forEach(function(button){button.node.disabled=true;});this.nextStatusPoll=Date.now()+5000;return;
		}
		this.statusValid=true;
		this.latestStatus=status;
		var service=status.enabled?(status.running?'加速运行中':'加速启动异常'):'加速已关闭';
		var verification=application&&application.status!=='none'?(APPLY_STATES[application.status]||'等待确认')+' · '+formatTime(application.checked_at):'等待首次验收';
		if(application&&application.same_configuration===false)verification+=' · 配置已变化，等待重新检查';
		var rows=[['加速服务',service],['最近验收',verification],['最近测速',taskText(status.last_test,false)],['最近操作',taskText(task,false)]];
		dom.content(this.statusNode,rows.map(function(row){return E('tr',{'class':'tr'},[E('td',{'class':'td',style:'width:25%'},[row[0]]),E('td',{'class':'td'},[row[1]])]);}));
		var active=PLATFORMS.filter(function(p){return groupEnabled(status,p.id);});
		dom.content(this.featureNode,active.map(function(p){
			var ip=groupIp(status,p.id),group=application&&application.groups&&application.groups[p.id],state=group?group.status:application&&application.router||'unknown';
			var checked=application&&application.same_configuration!==false&&application.status!=='none',observations=self.flowsFor(status,p.id);
			var flow=observations.find(function(f){return f.status==='different'||f.status==='previous';})||observations[0],node=ip||'等待测速节点';
			if(p.id==='com'&&status.mode==='redirect')node='转到国内下载 · '+node;
			var sw=status.platforms&&status.platforms.switch;
			return E('div',{'class':'xa-card','data-group':p.id},[E('h4',{},[p.label]),
				E('p',{},['功能：'+(status.enabled?(status.running?'已启用':'启动异常'):'已选择，服务已关闭')]),
				E('p',{'class':'xa-small xa-muted'},['当前节点：'+node]),
				E('p',{'class':state==='verified'&&checked?'xa-ok':'xa-muted'},['配置验收：'+(checked?(APPLY_STATES[state]||'待确认'):'等待检查')]),
				E('p',{'class':flow&&flow.status==='matched'?'xa-ok':flow?'xa-warn':'xa-muted'},['实际连接：'+(flow?(flow.shared_node&&flow.status==='matched'?'共享节点命中 · 平台待确认':FLOW_STATES[flow.status]||'待确认'):'等待设备下载')]),
				p.id==='switch'&&sw?E('p',{'class':'xa-small xa-muted'},['游戏下载域名：'+(sw.game_mapped||0)+'/'+(sw.game_total||3)]):''
			]);
		}));
		if(!active.length)dom.content(this.featureNode,[E('p',{'class':'xa-muted'},['在加速功能中选择使用的平台。'])]);
		this.updateDownloads(status);this.updateMeasurements(status);
		this.logNote.textContent=status.log_source==='persisted'?'历史日志快照 · 重启前保存的近期记录':'本次运行日志';
		if(typeof status.log==='string'&&this.logNode.value!==status.log){var bottom=this.logNode.scrollTop+this.logNode.clientHeight>=this.logNode.scrollHeight-20;this.logNode.value=status.log;if(bottom)this.logNode.scrollTop=this.logNode.scrollHeight;}
		this.updateActionButtons();
		this.nextStatusPoll=Date.now()+((busy||(status.enabled&&this.activeTab==='overview')||(status.enabled&&!status.running))?5000:30000);
		if(task&&['queued','running','none'].indexOf(task.status)===-1&&task.id!==this.lastFinishedTask){
			this.lastFinishedTask=task.id;this.loadRecords();
			if(application&&application.available&&application.checked_at>=task.started_at)this.loadApplicationReport();
			if(task.kind==='adopt')this.refreshAdoptedConfiguration();
		}
		if(this.pendingExport&&task&&task.id===this.pendingExport&&['queued','running'].indexOf(task.status)===-1){this.pendingExport=null;if(task.code==='REPORT_READY'&&status.report_ready)this.downloadReport();else ui.addNotification(null,E('p',{},['诊断报告生成失败，详情见运行记录。']),'error');}
	},
	updateActionButtons:function(){
		var status=this.latestStatus||{},busy=busyTask(status),pending=!!this.actionInFlight,valid=this.statusValid;
		this.actionButtons.forEach(function(button){button.node.disabled=!valid||(button.command==='cancel'?!busy:busy||pending||!!(button.predicate&&!button.predicate(status)));});
	},
	flowsFor:function(status,group){var observation=status.observation||{},selected=this.selectedDevice;if(!observation.fresh||!observation.available||!status.enabled)return [];return(observation.flows||[]).filter(function(flow){return(!selected||flow.device_ip===selected)&&(flow.groups||'').split(',').indexOf(group)!==-1;});},
	updateDownloads:function(status){
		var observation=status.observation||{},selected=this.selectedDevice,devices=observation.devices||[];
		dom.content(this.deviceSelect,[E('option',{value:''},['全部设备'])].concat(devices.map(function(device){return E('option',{value:device.ip},[device.name===device.ip?device.ip:device.name+'（'+device.ip+'）']);})));this.deviceSelect.value=selected||'';
		if(!status.enabled){dom.content(this.downloadNode,[E('p',{'class':'xa-muted'},['开启加速后，自动观察设备下载连接。'])]);return;}
		if(!observation.fresh||!observation.available){dom.content(this.downloadNode,[E('p',{'class':'xa-muted'},[observation.available?'下载观测正在更新，最近记录于 '+formatTime(observation.observed_at):'连接观测能力待确认，配置验收结果可在上方查看。'])]);return;}
		var flows=(observation.flows||[]).filter(function(flow){return!selected||flow.device_ip===selected;}),redirects=(observation.redirects||[]).filter(function(item){return!selected||item.device_ip===selected;});
		var rows=flows.map(function(flow){
			var name=devices.find(function(device){return device.ip===flow.device_ip;}),groups=(flow.groups||'').split(',').map(function(group){return GROUP_LABELS[group]||'';}).filter(Boolean).join('、')||'平台待确认';
			var text=FLOW_STATES[flow.status]||'待确认';if(flow.shared_node)text+=' · 共享 CDN 节点，平台待确认';
			var received=flow.activity==='counters_unavailable'?'流量统计待确认':flow.speed_bps>0?speedText(flow.speed_bps)+'（估算）':'连接已建立，等待流量';
			return E('tr',{'class':'tr'},[
				E('td',{'class':'td'},[name&&name.name||flow.device_ip,E('div',{'class':'xa-small xa-muted'},[flow.device_ip])]),
				E('td',{'class':'td'},[groups,E('div',{'class':'xa-small'},[flow.target_ip+':'+flow.target_port])]),
				E('td',{'class':'td '+(flow.status==='matched'?'xa-ok':'xa-warn')},[text,E('div',{'class':'xa-small xa-muted'},[PATH_STATES[flow.path]||'分流路径待确认'])]),
				E('td',{'class':'td'},[received,E('div',{'class':'xa-small xa-muted'},[formatTime(flow.observed_at)])])
			]);
		}),contents=[];
		if(rows.length)contents.push(E('div',{'class':'xa-table-wrap'},[E('table',{'class':'table'},[E('tr',{'class':'tr'},['设备','实际目标','生效证据','下行流量'].map(function(label){return E('th',{'class':'th'},[label]);}))].concat(rows))]));
		else contents.push(E('p',{'class':'xa-muted'},['等待设备下载 · 观测更新于 '+formatTime(observation.observed_at)]));
		if(flows.some(function(flow){return flow.status==='previous';}))contents.push(E('p',{'class':'xa-warn'},['当前连接沿用此前节点，暂停并继续下载可建立新连接。']));
		if(flows.some(function(flow){return flow.status==='different';}))contents.push(E('p',{'class':'xa-warn'},['下载目标与当前节点存在差异，请检查设备 DNS、IPv6 和分流设置。']));
		if(redirects.length){var latest=redirects[redirects.length-1],domestic=flows.some(function(flow){return flow.device_ip===latest.device_ip&&flow.status==='matched'&&/(^|,)cn(,|$)/.test(flow.groups);});contents.push(E('p',{'class':'xa-ok'},[(domestic?'已观察到重定向请求与国内节点连接':'已观察到设备重定向请求')+' · '+latest.host+' → '+latest.target_host+' · '+formatTime(latest.observed_at)]));}
		dom.content(this.downloadNode,contents);
	},
	updateMeasurements:function(status){
		var self=this,task=status.last_test||{},busy=busyTask(status),current=status.last_task||{},stages={configuration:'检查测速设置',application:'应用配置',verification:'检查生效',finished:'完成',queued:'等待执行',start:'准备执行',health:'检查当前节点'};
		var stage=stages[current.stage]||current.stage||'',match=/^(resource|discover|quick|full|domains)_(.+)$/.exec(stage);
		if(match)stage=(GROUP_LABELS[match[2]]||match[2])+' · '+({resource:'准备资源',discover:'发现节点',quick:'初筛',full:'复测',domains:'检查资源兼容性'}[match[1]]);
		var progress=current.progress||{},text=busy?(TASK_TYPES[current.kind]||'任务')+' · '+stage:taskText(task,false);
		if(busy&&progress.total)text+=' · '+progress.completed+'/'+progress.total+' · 有效样本 '+progress.valid;
		dom.content(this.progressNode,[E('p',{},[text]),E('p',{'class':'xa-small xa-muted'},[busy?'后台任务持续执行，关闭页面后仍会完成。':task.message||'选择平台后开始独立测速。'])]);
		this.recordProgressNode.hidden=!busy;
		dom.content(this.recordProgressNode,busy?[E('p',{},[text])]:[]);
		PLATFORMS.forEach(function(p){
			var row=self.resultRows[p.id],record=status.measurements&&status.measurements[p.id];if(!row)return;
			var group=task.kind==='measure'&&task.groups&&task.groups[p.id],contents=[];
			if(record){
				contents.push(E('div',{},[record.ip+' · '+(record.speed||speedText(record.speed_bps))]));
				if(record.partial)contents.push(E('div',{'class':'xa-small xa-warn'},['部分下载域名通过资源校验']));
				if(record.compatible===false)contents.push(E('div',{'class':'xa-small xa-warn'},['资源配置已变化，请重新测速']));
			}else contents.push(E('span',{'class':'xa-muted'},['等待测速']));
			if(group&&['failed','cancelled','interrupted','running'].indexOf(group.status)!==-1)contents.push(E('details',{},[E('summary',{'class':'xa-small xa-warn'},['最近测试：'+(TASK_STATES[group.status]||'待确认')]),E('p',{},[group.message||group.code])]));
			dom.content(row.result,contents);dom.content(row.time,[record?formatTime(record.tested_at):'—']);
			dom.content(row.adopt,[status.enabled&&groupEnabled(status,p.id)&&!(p.id==='com'&&status.mode==='redirect')?'采用结果':'启用并应用']);
		});
	},
	updateRecords:function(result){
		this.latestRecords=result;
		var events=this.recordFilter.value==='errors'?result.errors||[]:result.events||[],rows=events.slice().reverse().map(function(event){
			var severity=event.level==='ERROR'?'xa-fail':event.level==='WARN'?'xa-warn':'xa-muted';
			return E('tr',{'class':'tr'},[E('td',{'class':'td xa-small'},[formatTime(event.time)]),E('td',{'class':'td '+severity},[event.level==='ERROR'?'异常':event.level==='WARN'?'注意':'日常']),
				E('td',{'class':'td'},[E('details',{},[E('summary',{},[EVENT_TITLES[event.code]||(TASK_TYPES[event.kind]||'运行事件')]),E('p',{'class':'xa-small'},[event.message||'',' · '+(event.code||'')])])])]);
		});
		dom.content(this.eventsNode,rows.length?[E('p',{'class':'xa-muted'},[result.source==='persisted'?'正在查看重启前保存的历史事件。':'日常事件批量保存，关键异常及时保存。']),E('div',{'class':'xa-table-wrap'},[E('table',{'class':'table'},[E('tr',{'class':'tr'},['时间','类型','事件'].map(function(label){return E('th',{'class':'th'},[label]);}))].concat(rows))])]:[E('p',{'class':'xa-muted'},[result.records_error||'当前筛选暂无事件。'])]);
		var tasks=(result.tasks||[]).slice().sort(function(a,b){return(b.finished_at||b.started_at||0)-(a.finished_at||a.started_at||0);});
		dom.content(this.taskDetails,[E('h3',{},['测速与操作历史'])].concat(tasks.length?tasks.map(function(task){return E('details',{'class':'xa-disclosure'},[E('summary',{},[taskText(task,false)]),E('pre',{style:'white-space:pre-wrap'},[taskText(task,true)])]);}):[E('p',{'class':'xa-muted'},['任务结束后在这里保留结果。'])]));
	},
	loadRecords:function(){
		var self=this;if(this.recordsLoading)return this.recordsLoading;
		this.recordsLoading=callRecords().then(function(result){self.updateRecords(result);}).catch(function(err){self.updateRecords(Object.assign({},self.latestRecords,{records_error:'读取记录失败：'+err.message}));}).finally(function(){self.recordsLoading=null;});return this.recordsLoading;
	},
	updateEnvironment: function(result) {
		var self = this, groups = {}, entries = [];
		var renderRow = function(item) {
			var level = LEVELS[item.level] || LEVELS.info;

			return E('tr', { 'class': 'tr' }, [
				E('td', { 'class': 'td left', 'style': 'width:25%' }, [ item.name || '' ]),
				E('td', { 'class': 'td left', 'style': 'width:4em; white-space:nowrap; font-weight:bold; color:' + level[1] }, [ level[0] ]),
				E('td', { 'class': 'td left' }, [ (item.code ? '['+item.code+'] ' : '') + (item.detail || ''), item.expected ? E('div',{},['预期：'+item.expected+'；实际：'+(item.observed||'无结果')]) : '', item.remediation ? E('div',{},['处理建议：'+item.remediation]) : '' ])
			]);
		};
		(result.environment || []).forEach(function(item) {
			var endpoint = /^(?:(?:A|AAAA|SRV)\s+\S+|DNS 验收)\s+@(\S+)$/.exec(item.name || '');
			var key = endpoint ? endpoint[1] : /^覆盖\s+/.test(item.name || '') ? 'coverage' : null;
			if (key === null) { entries.push(item); return; }
			if (!groups[key]) {
				groups[key] = { key: key, items: [] };
				entries.push(groups[key]);
			}
			groups[key].items.push(item);
		});
		var rows = entries.map(function(entry) {
			if (!entry.items) return renderRow(entry);
			var counts = {}, hasIssues = false;
			entry.items.forEach(function(item) {
				counts[item.level] = (counts[item.level] || 0) + 1;
				if (['ok', 'info', 'skipped'].indexOf(item.level) === -1) hasIssues = true;
			});
			var label = entry.key === 'coverage' ? '域名接管范围' : entry.key === '127.0.0.1:53' ? '主 DNS' :
				entry.key === '127.0.0.1:' + ((self.latestStatus || {}).resolver_port || uci.get('xbox-accel', 'main', 'resolver_port') || '5335') ? '独立解析实例' : 'DNS 转发入口';
			if (entry.key !== 'coverage') label += '（' + entry.key + '）';
			var summary = '域名转发验证 · ' + label + '：' + entry.items.length + ' 项';
			['ok', 'fail', 'warn', 'unknown', 'skipped', 'info'].forEach(function(level) {
				if (counts[level]) summary += '；' + LEVELS[level][0] + ' ' + counts[level];
			});
			var previous = self.environmentGroups[entry.key];
			var state = { open: previous ? previous.open || (hasIssues && !previous.hasIssues) : hasIssues, hasIssues: hasIssues };
			self.environmentGroups[entry.key] = state;
			var details = E('details', {
				'class': 'xbox-accel-dns-details',
				'toggle': function(event) { state.open = event.target.open; }
			}, [
				E('summary', { 'style': 'cursor:pointer; padding:.5em 0; font-weight:bold' }, [summary]),
				E('table', { 'class': 'table' }, entry.items.map(renderRow))
			]);
			details.open = state.open;
			return E('tr', { 'class': 'tr' }, [ E('td', { 'class': 'td left', 'colspan': 3 }, [details]) ]);
		});

		if (!rows.length)
			rows.push(E('tr', { 'class': 'tr' }, [ E('td', { 'class': 'td left' }, [ '未取得诊断结果，不能确认成功：' + (result.environment_error || '记录缺失') ]) ]));

		dom.content(this.envNode, rows);
	},

	refreshEnvironment: function() {
		var self = this;

		this.envNote.textContent = '检测中…';

		return callEnvironment().then(function(result) {
			self.updateEnvironment(result);
			self.envNote.textContent = '这是快速初查；完整结果请运行后台验收。';
		}).catch(function(err) {
			self.envNote.textContent = '检测失败：' + err.message;
		});
	},

	loadApplicationReport: function() {
		var self=this;
		return fs.read_direct('/tmp/xbox-accel/last-application.json','blob').then(function(blob){return blob.text();}).then(function(text){
			var report=JSON.parse(text);
			self.updateEnvironment(report);
			self.envNote.textContent='完整验收记录于 '+formatTime(report.checked_at)+'；实时下载连接见总览，速度效果需同资源对比。';
		}).catch(function(err){self.envNote.textContent='无法读取完整报告：'+err.message+'；请重新验收。';});
	},

	downloadReport: function() {
		if(!this.latestStatus || !this.latestStatus.report_ready) {
			ui.addNotification(null,E('p',{},['没有可下载的报告，请先生成脱敏报告并等待完成。']),'error');
			return Promise.resolve();
		}
		return fs.read_direct('/tmp/xbox-accel/diagnostic-report.json','blob').then(function(blob){
			var url=URL.createObjectURL(blob), a=document.createElement('a');
			a.href=url;a.download='xbox-accel-diagnostics.json';a.click();
			setTimeout(function(){URL.revokeObjectURL(url);},1000);
		}).catch(function(err){ui.addNotification(null,E('p',{},['报告下载失败：'+err.message]),'error');});
	},


	refreshAdoptedConfiguration:function(){
		var self=this;if(uci.unload)uci.unload('xbox-accel');
		return uci.load('xbox-accel').then(function(){
			['enabled','optimize_cn','optimize_store','optimize_ps','optimize_xbox_old','optimize_switch','_international_mode'].forEach(function(name){
				var o=self.configOptions[name],widget=o&&o.getUIElement&&o.getUIElement('main');
				var value=name==='_international_mode'?o.cfgvalue('main'):uci.get('xbox-accel','main',name);
				if(o&&o.cfgvalue)o.cfgvalue('main',value);
				if(widget&&widget.setValue)widget.setValue(value);
			});
		}).catch(function(err){ui.addNotification(null,E('p',{},['读取新配置失败：'+err.message]),'error');});
	},
	runAction:function(command,args){
		var self=this;args=args||{};
		if(command==='test'&&!args.groups){ui.addNotification(null,E('p',{},['请选择需要测速的平台。']),'info');return Promise.resolve();}
		this.actionInFlight=true;this.updateActionButtons();
		return callAction(command,args.groups||'',args.result_id||'').then(function(result){
			if(!result.ok)ui.addNotification(null,E('p',{},[result.message||'操作失败']),'error');
			else if(result.accepted&&command==='export')self.pendingExport=result.task_id;
			return callStatus();
		}).then(function(status){self.updateStatus(status);if(command==='clear_log')return self.loadRecords();}).catch(function(err){ui.addNotification(null,E('p',{},['操作失败：'+err.message]),'error');}).finally(function(){self.actionInFlight=false;self.updateActionButtons();});
	},
	handleSave:function(ev){
		if(this.statusValid===false||busyTask(this.latestStatus||{})||this.actionInFlight){ui.addNotification(null,E('p',{},['状态恢复且当前任务完成后可保存配置。']),'info');return Promise.resolve();}
		return this.super('handleSave',[ev]);
	},
	handleSaveApply:function(ev,mode){
		var self=this;
		if(this.statusValid===false||busyTask(this.latestStatus||{})||this.actionInFlight){ui.addNotification(null,E('p',{},['状态恢复且当前任务完成后可保存并应用配置。']),'info');return Promise.resolve();}
		if(this.appliedHandler)document.removeEventListener('uci-applied',this.appliedHandler);
		this.appliedHandler=function(){document.removeEventListener('uci-applied',self.appliedHandler);self.appliedHandler=null;self.runAction('apply');};
		document.addEventListener('uci-applied',this.appliedHandler);return this.super('handleSaveApply',[ev,mode]);
	}
});
