'use strict';
'require view';
'require form';
'require uci';
'require rpc';
'require poll';
'require ui';
'require dom';
'require fs';

var callStatus = rpc.declare({ object: 'xbox-accel', method: 'status', expect: { '': {} } });
var callEnvironment = rpc.declare({ object: 'xbox-accel', method: 'environment', expect: { '': {} } });
var callAction = rpc.declare({ object: 'xbox-accel', method: 'action', params: [ 'command' ], expect: { '': {} } });

var LEVELS = {
	ok: [ '正常', '#2e7d32' ],
	info: [ '信息', '#607d8b' ],
	warn: [ '注意', '#ef6c00' ],
	fail: [ '失败', '#c62828' ],
	unknown: [ '未确认', '#607d8b' ],
	skipped: [ '未执行', '#9e9e9e' ]
};

var COM_MODES = {
	redirect: '重定向到国内域名（com→cn）',
	optimize: 'CDN 优选',
	off: '不处理'
};

function formatTime(epoch) {
	if (!epoch)
		return '';

	var d = new Date(epoch * 1000);
	var pad = function(n) { return (n < 10 ? '0' : '') + n; };

	return '%s-%s %s:%s'.format(pad(d.getMonth() + 1), pad(d.getDate()), pad(d.getHours()), pad(d.getMinutes()));
}

function nodeText(ip, speed, time) {
	if (!ip)
		return '还没有测速结果，按默认解析';

	if (!speed)
		return ip + '（路由器重启后尚未复测）';

	return '%s，%s，测于 %s'.format(ip, speed, formatTime(time));
}


var TASK_TYPES = {test:'完整测速',health:'健康检查',apply:'应用并验收',restart:'重启服务并验收',verify:'只做验收',report:'诊断导出'};
var TASK_STATES = {none:'尚无记录',queued:'已受理，未完成',running:'进行中',success:'动作完成',partial:'部分完成',failed:'失败',cancelled:'已取消',interrupted:'异常中断',skipped:'未执行',unknown:'无法确认'};
var APPLY_STATES = {verified:'验收通过',partial:'尚未全部确认',failed:'验收失败',unknown:'未确认',skipped:'未应用/未执行',disabled:'插件未启用',none:'尚无记录'};

function taskText(task, detailed) {
	if (!task || task.status === 'none') return '尚无记录';
	var text = (TASK_TYPES[task.kind] || '任务') + '：' + (TASK_STATES[task.status] || '未知') + ' [' + (task.code || '?') + ']；ID=' + (task.id || '?') + '；阶段=' + (task.stage || '?');
	if(detailed===false) return text;
	text += '；' + (task.message || '');
	if (task.groups && (task.kind==='test' || task.kind==='health')) ['cn','com'].forEach(function(group){
		var result=task.groups[group];
		if(result) text += '；' + (group==='cn'?'国内':'国际') + '=' + (TASK_STATES[result.status] || result.status) + ' [' + result.code + '] ' + result.message;
	});
	if(task.application) text += '；配置=' + (APPLY_STATES[task.application.status] || '未知') + ' [' + task.application.code + ']';
	if(task.started_text) text += '；开始=' + task.started_text;
	if(task.finished_at) text += '；结束=' + task.finished_text + '；耗时=' + task.duration_s + 's';
	if(task.persistence_warning) text += '；警告：摘要未能完整持久保存';
	return text;
}

return view.extend({
	load: function() {
		return Promise.all([
			uci.load('xbox-accel'),
			callStatus().catch(function(err){return {status_error:err.message};}),
			callEnvironment().catch(function(err){return {environment_error:err.message};})
		]);
	},

	render: function(data) {
		var self = this;

		this.statusNode = E('table', { 'class': 'table', 'id': 'xbox-accel-status' });
		this.envNode = E('table', { 'class': 'table', 'id': 'xbox-accel-environment' });
		this.taskDetails = E('div', {id:'xbox-accel-task-details'});
		this.envNote = E('span', { 'style': 'margin-left:1em' });
		this.logNote = E('div', { 'class': 'cbi-section-descr' });
		this.logNode = E('textarea', {
			'id': 'xbox-accel-log',
			'readonly': 'readonly',
			'wrap': 'off',
			'rows': 16,
			'style': 'width:100%; font-family:monospace; font-size:12px'
		});
		this.buttons = {};
		this.wasTesting = false;

		var button = function(label, command, style, confirmText) {
			self.buttons[command] = E('button', {
				'class': 'btn cbi-button ' + (style || 'cbi-button-action'),
				'click': ui.createHandlerFn(self, function() {
					if (confirmText && !window.confirm(confirmText))
						return;

					return self.runAction(command);
				})
			}, [ label ]);

			return self.buttons[command];
		};

		var m = new form.Map('xbox-accel', 'Xbox 下载加速',
			'让 Xbox 的游戏下载连接到对当前线路最快的 CDN 节点，并可把国际下载地址重定向到国内下载地址。' +
			'本插件本身不代理下载流量；是否直连还取决于 Passwall 等软件的分流规则。');

		var s = m.section(form.NamedSection, 'main', 'main');
		s.addremove = false;
		s.tab('basic', '基本设置');
		s.tab('test', '自动测速');
		s.tab('advanced', '高级设置');
		s.tab('environment', '环境检测');
		s.tab('logs', '运行日志');

		var flag = function(tab, name, title, def, description) {
			var o = s.taboption(tab, form.Flag, name, title, description);
			o.default = def;
			o.rmempty = false;
			return o;
		};

		var number = function(tab, name, title, def, min, max, description) {
			var o = s.taboption(tab, form.Value, name, title, description);
			o.default = def;
			o.placeholder = def;
			o.datatype = 'and(uinteger,range(%d,%d))'.format(min, max);
			o.rmempty = false;
			return o;
		};

		var o;

		flag('basic', 'enabled', '启用', '0',
			'关闭后会撤销本插件对主 dnsmasq、uhttpd 和计划任务所做的全部改动。');
		flag('basic', 'autostart', '开机自启动', '1',
			'路由器启动后自动恢复已启用的加速功能。关闭此项后，重启时只清理旧规则；可在页面重新应用以手动启动。总开关关闭时不会自启动。');
		flag('basic', 'optimize_cn', '国内 CDN 优选', '1',
			'把 assets1.xboxlive.cn 等国内下载域名指向测速最快的节点。');
		flag('basic', 'optimize_com', '国际 CDN 优选', '1',
			'把 assets1.xboxlive.com 等国际下载域名指向测速最快的节点。');
		flag('basic', 'redirect_cn', '国际下载重定向到国内（com→cn）', '0',
			'把发往国际下载域名的请求用 HTTP 302 转到国内域名，开启后国际域名不再做 CDN 优选。' +
			'需要路由器的 uhttpd 在 80 端口提供 HTTP。个别游戏在国内 CDN 上可能很慢或无法下载，遇到时请关闭此项。');

		o = s.taboption('basic', form.ListValue, 'isp', '运营商');
		o.value('auto', '自动（只按当前线路发现节点）');
		o.value('telecom', '中国电信');
		o.value('unicom', '中国联通');
		o.value('mobile', '中国移动');
		o.default = 'auto';
		o.rmempty = false;
		o.description = '选择运营商后，会额外查询该运营商在其他地区的节点来扩大候选范围。所有候选都要实际测速，选错不会带来错误结果，只是多测一些节点。';

		// 旧版简单 cron 映射为可编辑字段；复杂表达式只允许原样保留，不要求用户编写 cron。
		var oldPlan = uci.get('xbox-accel', 'main', 'cron') || '15 5 * * *';
		var parts = oldPlan.trim().split(/\s+/), initial = { type: 'legacy', time: '05:15', weekday: '0', monthday: '1' };
		if (parts.length === 5 && /^\d+$/.test(parts[0]) && Number(parts[0]) < 60 &&
			/^\d+$/.test(parts[1]) && Number(parts[1]) < 24 && parts[3] === '*') {
			initial.time = ('0' + Number(parts[1])).slice(-2) + ':' + ('0' + Number(parts[0])).slice(-2);
			if (parts[2] === '*' && parts[4] === '*') initial.type = 'daily';
			else if (parts[2] === '*' && /^[0-7]$/.test(parts[4])) { initial.type = 'weekly'; initial.weekday = String(Number(parts[4]) % 7); }
			else if (/^\d+$/.test(parts[2]) && Number(parts[2]) >= 1 && Number(parts[2]) <= 31 && parts[4] === '*') {
				initial.type = 'monthly'; initial.monthday = String(Number(parts[2]));
			}
		}
		function scheduleDefault(option, value) {
			option.default = value;
			option.rmempty = false;
			option.cfgvalue = function(section_id) { return uci.get('xbox-accel', section_id, this.option || this.name) || value; };
		}
		o = s.taboption('test', form.ListValue, 'schedule_type', '定时完整测速',
			'选择每天、每周或每月，再填写 HH:MM；使用路由器系统时区。测速会占用带宽和流量，建议每天一次。');
		o.value('daily', '每天'); o.value('weekly', '每周'); o.value('monthly', '每月');
		if (initial.type === 'legacy' || uci.get('xbox-accel', 'main', 'schedule_type') === 'legacy')
			o.value('legacy', '保留旧版自定义计划');
		scheduleDefault(o, initial.type);
		o = s.taboption('test', form.Value, 'schedule_time', '测速时间（HH:MM）');
		scheduleDefault(o, initial.time);
		o.placeholder = '05:15';
		o.depends('schedule_type', 'daily'); o.depends('schedule_type', 'weekly'); o.depends('schedule_type', 'monthly');
		o.validate = function(section_id, value) { return /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value) ? true : '请填写 00:00 到 23:59 的时间（HH:MM）'; };
		o = s.taboption('test', form.ListValue, 'schedule_weekday', '星期几');
		[ '星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六' ].forEach(function(day, i) { o.value(String(i), day); });
		scheduleDefault(o, initial.weekday); o.depends('schedule_type', 'weekly');
		o = s.taboption('test', form.Value, 'schedule_monthday', '每月几号', '选择 29、30、31 号时，没有该日期的月份不会执行。');
		scheduleDefault(o, initial.monthday); o.datatype = 'range(1,31)'; o.depends('schedule_type', 'monthly');
		o.validate = function(section_id, value) { return /^(?:[1-9]|[12]\d|3[01])$/.test(value) ? true : '请填写 1 到 31 的日期'; };

		o = s.taboption('test', form.ListValue, 'health_interval', '节点健康检查',
			'和定时完整测速不同：这里只检查当前节点，连续失败才重新测速。按路由器时钟的分钟刻度执行；关闭不会关闭上面的定时测速。');
		var healthPlans = [ [ '0', '关闭健康检查' ], [ '5', '每 5 分钟' ], [ '10', '每 10 分钟（推荐）' ], [ '15', '每 15 分钟' ], [ '20', '每 20 分钟' ], [ '30', '每 30 分钟' ] ];
		var currentHealth = uci.get('xbox-accel', 'main', 'health_interval');
		if (currentHealth && !healthPlans.some(function(item) { return item[0] === currentHealth; })) {
			var step = Number(currentHealth), marks = [];
			if (Number.isInteger(step) && step > 0 && step < 60)
				for (var minute = 0; minute < 60; minute += step) marks.push((minute < 10 ? '0' : '') + minute);
			healthPlans.push([ currentHealth, marks.length ? '保留旧版：每小时 ' + marks.join('、') + ' 分' : '旧版设置（建议重选）' ]);
		}
		healthPlans.forEach(function(item) { o.value(item[0], item[1]); });
		o.default = '10';
		o.rmempty = false;
		number('test', 'switch_threshold', '切换门槛（%）', '15', 0, 100,
			'新节点比当前节点快出这个比例才切换，避免在速度接近的节点之间来回变动。');
		number('test', 'max_candidates', '每组最多测试的节点数', '60', 1, 100);
		number('test', 'quick_size_mb', '初筛：每个节点最多下载（MB）', '4', 1, 128);
		number('test', 'quick_time', '初筛：每个节点最长时间（秒）', '4', 1, 30);
		number('test', 'top_n', '复测：取初筛的前几名', '3', 1, 10);
		number('test', 'full_size_mb', '复测：每个节点最多下载（MB）', '64', 1, 256);
		number('test', 'full_time', '复测：每个节点最长时间（秒）', '10', 1, 60,
			'按默认值，一次完整测速最多消耗约 1 GB 流量，通常远低于此。');

		flag('advanced', 'override_cdn_hosts', '接管备用 CDN', '1',
			'Xbox 会通过 SRV 记录获取另外 4 个备用 CDN 主机名并改用它们下载。开启后这些主机名也按上面的设置处理，否则下载中途可能切回未优化的节点。');
		flag('advanced', 'passwall_direct', '节点加入 Passwall 直连集合', '1',
			'把候选节点和选中的节点加入 Passwall 的直连集合 psw_white。实际直连路径还需结合规则命中与 Xbox 下载连接确认。');
		flag('advanced', 'passwall_restart', '启用时自动重载 Passwall', '1',
			'启用功能时自动重载 Passwall 的 DNS 副本。关闭总开关或某项功能时，会自动重载副本并清理已撤销的规则；重载可能短暂影响代理连接。');

		o = s.taboption('advanced', form.Value, 'dnsmasq_instance', '主 dnsmasq 实例',
			'写入下载域名规则的 dnsmasq 实例，通常保持默认。更换实例前请先停用本插件并保存。');
		o.default = '@dnsmasq[0]';
		o.placeholder = '@dnsmasq[0]';
		o.rmempty = false;
		o.validate = function(section_id, value) {
			return /^(@dnsmasq\[[0-9]+\]|[A-Za-z0-9_]+)$/.test(value) ? true : '格式：@dnsmasq[0] 或配置节的名称';
		};

		number('advanced', 'resolver_port', '解析实例端口', '5335', 1024, 65535,
			'本插件自带的解析实例在 127.0.0.1 上监听的端口，主 dnsmasq 把下载域名的查询转到这里。');
		number('advanced', 'ttl', '解析结果有效期（秒）', '30', 5, 600,
			'越短，切换节点后生效越快。');

		o = s.taboption('advanced', form.DynamicList, 'fallback_dns', '上游 DNS',
			'解析实例用来查询未被固定的域名，只能填公网 IPv4 地址。不要填路由器自己的地址，否则会形成解析回环。');
		o.datatype = 'ip4addr';
		o.default = [ '223.5.5.5', '119.29.29.29' ];
		o.rmempty = false;

		o = s.taboption('advanced', form.Value, 'redirect_ip', '重定向使用的路由器地址',
			'com→cn 重定向时，国际下载域名会解析到这个地址。留空则使用 LAN 口地址。');
		o.datatype = 'ip4addr';
		o.optional = true;

		o = s.taboption('advanced', form.Value, 'test_url_cn', '国内测速文件',
			'环境检测提示测速文件失效时再修改。必须是 http://assets1.xboxlive.cn/ 下的文件。');
		o.placeholder = 'http://assets1.xboxlive.cn/Z/routing/extraextralarge.txt';
		o.optional = true;
		o.validate = function(section_id, value) {
			return (!value || /^http:\/\/assets1\.xboxlive\.cn\/\S+$/.test(value)) ? true : '必须以 http://assets1.xboxlive.cn/ 开头';
		};

		o = s.taboption('advanced', form.Value, 'test_url_com', '国际测速文件',
			'必须是 http://assets1.xboxlive.com/ 下的文件。');
		o.placeholder = 'http://assets1.xboxlive.com/Z/routing/extraextralarge.txt';
		o.optional = true;
		o.validate = function(section_id, value) {
			return (!value || /^http:\/\/assets1\.xboxlive\.com\/\S+$/.test(value)) ? true : '必须以 http://assets1.xboxlive.com/ 开头';
		};

		// 展示内容使用 DummyValue，不写入 UCI；由 LuCI 原生标签管理显隐。
		o = s.taboption('environment', form.DummyValue, '_environment');
		o.write = function() {};
		o.remove = function() {};
		o.render = function() {
			return E('div', { 'class': 'cbi-section' }, [
				E('div', { 'class': 'cbi-section-descr' }, [
					'默认显示快速环境初查；一键完整验收会在后台逐项验证 DNS/SRV/AAAA/302、测试资源和直连集合。Xbox 实际路径与速度需在主机上观察。'
				]),
				self.envNode,
				E('div', { 'style': 'margin-top:.5em' }, [
					E('button', {
						'class': 'btn cbi-button cbi-button-action',
						'click': ui.createHandlerFn(self, 'refreshEnvironment')
					}, [ '刷新初查' ]),
					self.envNote, ' ',
					button('一键完整验收', 'verify'), ' ',
					E('button', {'class':'btn cbi-button cbi-button-action', 'click':ui.createHandlerFn(self,'loadApplicationReport')}, ['查看上次验收']), ' ',
					button('生成脱敏报告', 'export'), ' ',
					E('button', {'class':'btn cbi-button cbi-button-action', 'click':ui.createHandlerFn(self,'downloadReport')}, ['下载上次报告'])
				])
			]);
		};

		o = s.taboption('logs', form.DummyValue, '_logs');
		o.write = function() {};
		o.remove = function() {};
		o.render = function() {
			return E('div', { 'class': 'cbi-section' }, [
				E('div', { 'class': 'cbi-section-descr' }, [
					'日志包含任务 ID、阶段和错误代码，页面显示最近 300 行。任务结束时保存最多 500 行、128 KiB 的日志快照和任务摘要；异常退出会显示中断状态。'
				]),
				self.logNote,
				self.taskDetails,
				self.logNode,
				E('div', { 'style': 'margin-top:.5em' }, [ button('清空日志', 'clear_log') ])
			]);
		};

		this.updateStatus(data[1] || {});
		this.updateEnvironment(data[2] || {});

		poll.add(function() {
			return callStatus().then(function(status) {
				self.updateStatus(status);
			}).catch(function(err){self.updateStatus({status_error:err.message});});
		}, 5);

		return m.render().then(function(formNode) {
			var page = E('div', {}, [
				E('div', { 'class': 'cbi-section' }, [
					E('h3', {}, [ '运行状态' ]),
					self.statusNode,
					E('div', { 'style': 'margin-top:.5em' }, [
						button('立即测速', 'test'), ' ',
						button('只测国内', 'test_cn'), ' ',
						button('只测国际', 'test_com'), ' ',
						button('停止测速', 'cancel', 'cbi-button-negative'), ' ',
						button('重新应用', 'apply'), ' ',
						button('重启服务', 'restart'), ' ',
						button('清除测速结果', 'reset', 'cbi-button-negative',
							'清除后下载域名恢复默认解析，直到下一次测速完成。确定要清除吗？')
					])
				]),
				formNode
			]);
			self.updateStatus(data[1] || {});
			return page;
		});
	},

	updateStatus: function(status) {
		var service, com, invalid = !!status.status_error || typeof status.enabled!=='boolean' || typeof status.running!=='boolean';

		if (invalid)
			service = '无法取得状态：' + (status.status_error || '响应缺失/格式无效') + '；不能确认服务已启动或停用';
		else if (!status.enabled)
			service = '未启用';
		else if (status.running)
			service = '已启用，解析实例运行中';
		else
			service = '已启用，但解析实例没有运行';

		if (status.testing)
			service += '；后台任务正在执行';

		if (invalid) com='未知（未取得有效状态）';
		else if (status.mode == 'redirect')
			com = '%s，入口 http://%s'.format(COM_MODES.redirect, status.lan_ip || '?');
		else if (status.mode == 'optimize')
			com = nodeText(status.com_ip, status.com_speed, status.com_time);
		else
			com = COM_MODES.off;

		var rows = [
			[ '服务', service ],
			[ '国内下载域名', invalid ? '未知（未取得有效状态）' : status.optimize_cn ? nodeText(status.cn_ip, status.cn_speed, status.cn_time) : '不处理' ],
			[ '国际下载域名', com ],
			[ '版本', status.version || '未知' ],
			[ '最近完整测速', taskText(status.last_test, false) ],
			[ '最近活动', taskText(status.last_task, false) ],
			[ '最近完整验收', status.last_application && status.last_application.status!=='none' ? (APPLY_STATES[status.last_application.status] || '未知') + '；资源=' + (APPLY_STATES[status.last_application.measurement] || '未确认') + '；直连集合=' + (APPLY_STATES[status.last_application.direct_set] || '未确认') + '；检查时间=' + formatTime(status.last_application.checked_at) + (status.last_application.stale ? '；历史记录，当前状态需重验' : '；对应检查时刻') : '尚无验收记录' ],
			[ 'Xbox 实际效果', '未观测；需要在主机上对比真实目标节点和稳定下载速度' ]
		];

		dom.content(this.statusNode, rows.map(function(row) {
			return E('tr', { 'class': 'tr' }, [
				E('td', { 'class': 'td left', 'style': 'width:25%' }, [ row[0] ]),
				E('td', { 'class': 'td left' }, [ row[1] ])
			]);
		}));

		dom.content(this.taskDetails, [E('h4',{},['持久任务摘要（与下方临时日志分开）']),
			E('pre',{style:'white-space:pre-wrap'},['最近完整测速：'+taskText(status.last_test)+'\n最近活动：'+taskText(status.last_task)])]);

		var log = typeof status.log==='string' ? status.log : this.logNode.value;
		this.logNote.textContent = status.log_source==='persisted' ? '正在显示上次任务结束时保存的历史日志快照。' : status.log_source==='live' ? '正在显示本次开机后的运行日志。' : invalid ? '状态读取失败，保留已显示的日志。' : '尚无运行日志。';

		if (this.logNode.value != log) {
			var atBottom = this.logNode.scrollTop + this.logNode.clientHeight >= this.logNode.scrollHeight - 20;

			this.logNode.value = log;

			if (atBottom)
				this.logNode.scrollTop = this.logNode.scrollHeight;
		}

		var task = status.last_task;
		var testing = !!status.testing || !!(task && (task.status==='queued' || task.status==='running'));

		[ 'test', 'test_cn', 'test_com', 'reset', 'apply', 'restart', 'verify', 'export' ].forEach(L.bind(function(name) {
			if (this.buttons[name])
				this.buttons[name].disabled = testing;
		}, this));

		if (this.buttons.cancel)
			this.buttons.cancel.disabled = !testing;

		/* 测速结束后解析结果可能已经变化，重新检测一次 */
		if (!invalid && this.wasTesting && !testing) {
			if (status.last_application && status.last_application.available && task && status.last_application.checked_at>=task.started_at)
				this.loadApplicationReport();
			else this.refreshEnvironment();
		}

		if (!invalid) this.wasTesting = testing;
		this.latestStatus = status;
		if (!invalid && this.pendingExport && task && task.id===this.pendingExport && ['success','partial','failed','cancelled','interrupted','skipped','unknown'].indexOf(task.status)!==-1) {
			this.pendingExport=null;
			if(status.last_task.code==='REPORT_READY' && status.report_ready) this.downloadReport();
			else ui.addNotification(null,E('p',{},['报告没有生成成功，请看任务摘要。']),'error');
		}
	},

	updateEnvironment: function(result) {
		var rows = (result.environment || []).map(function(item) {
			var level = LEVELS[item.level] || LEVELS.info;

			return E('tr', { 'class': 'tr' }, [
				E('td', { 'class': 'td left', 'style': 'width:25%' }, [ item.name || '' ]),
				E('td', { 'class': 'td left', 'style': 'width:4em; white-space:nowrap; font-weight:bold; color:' + level[1] }, [ level[0] ]),
				E('td', { 'class': 'td left' }, [ (item.code ? '['+item.code+'] ' : '') + (item.detail || ''), item.expected ? E('div',{},['预期：'+item.expected+'；实际：'+(item.observed||'无结果')]) : '', item.remediation ? E('div',{},['处理建议：'+item.remediation]) : '' ])
			]);
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

	runAction: function(command) {
		var self = this;

		return callAction(command).then(function(result) {
			if (!result.ok)
				ui.addNotification(null, E('p', {}, [ result.message || '操作失败' ]), 'error');
			else if(result.accepted) {
				ui.addNotification(null,E('p',{},[result.message || '任务已受理，尚未完成，请等待最终摘要。']),'info');
				if(command==='export') self.pendingExport=result.task_id;
			}
			return callStatus();
		}).then(function(status) {
			self.updateStatus(status);

			if (command == 'apply' || command == 'restart' || command == 'reset')
				return self.refreshEnvironment();
		}).catch(function(err) {
			ui.addNotification(null, E('p', {}, [ '操作失败：' + err.message ]), 'error');
		});
	},

	loadApplicationReport: function() {
		var self=this;
		return fs.read_direct('/tmp/xbox-accel/last-application.json','blob').then(function(blob){return blob.text();}).then(function(text){
			var report=JSON.parse(text);
			self.updateEnvironment(report);
			self.envNote.textContent='上次完整验收记录于 '+formatTime(report.checked_at)+'；当前状态需重新验收，Xbox 效果待实机确认。';
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

	/* LuCI 的“保存并应用”不会等配置真正提交。等 uci-applied 事件之后再让服务
	   重新载入，否则服务读到的还是旧配置。 */
	handleSaveApply: function(ev, mode) {
		var self = this;

		if (this.appliedHandler)
			document.removeEventListener('uci-applied', this.appliedHandler);

		this.appliedHandler = function() {
			document.removeEventListener('uci-applied', self.appliedHandler);
			self.appliedHandler = null;
			self.runAction('apply');
		};

		document.addEventListener('uci-applied', this.appliedHandler);

		return this.super('handleSaveApply', [ ev, mode ]);
	}
});
