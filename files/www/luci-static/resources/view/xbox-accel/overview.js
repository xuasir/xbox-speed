'use strict';
'require view';
'require form';
'require uci';
'require rpc';
'require poll';
'require ui';
'require dom';

var callStatus = rpc.declare({ object: 'xbox-accel', method: 'status', expect: { '': {} } });
var callEnvironment = rpc.declare({ object: 'xbox-accel', method: 'environment', expect: { '': {} } });
var callAction = rpc.declare({ object: 'xbox-accel', method: 'action', params: [ 'command' ], expect: { '': {} } });

var LEVELS = {
	ok: [ '正常', '#2e7d32' ],
	info: [ '信息', '#607d8b' ],
	warn: [ '注意', '#ef6c00' ],
	fail: [ '异常', '#c62828' ]
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

return view.extend({
	load: function() {
		return Promise.all([
			uci.load('xbox-accel'),
			L.resolveDefault(callStatus(), {}),
			L.resolveDefault(callEnvironment(), {})
		]);
	},

	render: function(data) {
		var self = this;

		this.statusNode = E('table', { 'class': 'table', 'id': 'xbox-accel-status' });
		this.envNode = E('table', { 'class': 'table', 'id': 'xbox-accel-environment' });
		this.envNote = E('span', { 'style': 'margin-left:1em' });
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

		o = s.taboption('test', form.ListValue, 'cron', '定时完整测速',
			'直接选择时间安排，无需填写表达式。使用路由器系统时区；小时计划按整点对齐，不是从启用后倒计时。测速会占用带宽和流量，建议每天一次。');
		var schedules = [
			[ '15 5 * * *', '每天 05:15（推荐）' ],
			[ '*/30 * * * *', '每 30 分钟（每小时 00、30 分）' ],
			[ '0 * * * *', '每 1 小时（每小时整点）' ],
			[ '0 */3 * * *', '每 3 小时（00、03、06、09、12、15、18、21 点）' ],
			[ '0 */6 * * *', '每 6 小时（00、06、12、18 点）' ],
			[ '0 */12 * * *', '每 12 小时（00、12 点）' ]
		];
		for (var hour = 0; hour < 24; hour++) {
			schedules.push([ '0 ' + hour + ' * * *', '每天 ' + (hour < 10 ? '0' : '') + hour + ':00' ]);
		}
		schedules.push([ '0 3 * * 0', '每周日 03:00' ]);
		var currentSchedule = uci.get('xbox-accel', 'main', 'cron');
		if (currentSchedule && !schedules.some(function(item) { return item[0] === currentSchedule; }))
			schedules.push([ currentSchedule, '保留旧版自定义计划（可改选上面的预设）' ]);
		schedules.forEach(function(item) { o.value(item[0], item[1]); });
		o.default = '15 5 * * *';
		o.rmempty = false;
		o.validate = function(section_id, value) {
			return schedules.some(function(item) { return item[0] === value; }) ? true : '请选择一个测速时间安排';
		};

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
			'把候选节点和选中的节点加入 Passwall 的直连集合 psw_white，使测速和下载都不经过代理。没有安装 Passwall 时无影响。');
		flag('advanced', 'passwall_restart', '需要时自动重启 Passwall', '1',
			'Passwall 用自己的 dnsmasq 副本接管局域网 DNS 时，副本只在 Passwall 启动时读取本插件的规则。关闭此项后需要在启用、停用本插件后手动重启 Passwall。');

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
					'以下检测都在路由器本机进行，能确认配置和解析链路是否正确，但不能代替在 Xbox 上观察实际下载速度。'
				]),
				self.envNode,
				E('div', { 'style': 'margin-top:.5em' }, [
					E('button', {
						'class': 'btn cbi-button cbi-button-action',
						'click': ui.createHandlerFn(self, 'refreshEnvironment')
					}, [ '重新检测' ]),
					self.envNote
				])
			]);
		};

		o = s.taboption('logs', form.DummyValue, '_logs');
		o.write = function() {};
		o.remove = function() {};
		o.render = function() {
			return E('div', { 'class': 'cbi-section' }, [
				E('div', { 'class': 'cbi-section-descr' }, [
					'测速、切换节点和健康检查的记录。保存在内存中，最多 500 行，路由器重启后清空；节点切换等关键事件同时写入系统日志。'
				]),
				self.logNode,
				E('div', { 'style': 'margin-top:.5em' }, [ button('清空日志', 'clear_log') ])
			]);
		};

		this.updateStatus(data[1] || {});
		this.updateEnvironment(data[2] || {});

		poll.add(function() {
			return callStatus().then(function(status) {
				self.updateStatus(status);
			});
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
		var service, com;

		if (!status.enabled)
			service = '未启用';
		else if (status.running)
			service = '已启用，解析实例运行中';
		else
			service = '已启用，但解析实例没有运行';

		if (status.testing)
			service += '；正在测速';

		if (status.mode == 'redirect')
			com = '%s，入口 http://%s'.format(COM_MODES.redirect, status.lan_ip || '?');
		else if (status.mode == 'optimize')
			com = nodeText(status.com_ip, status.com_speed, status.com_time);
		else
			com = COM_MODES.off;

		var rows = [
			[ '服务', service ],
			[ '国内下载域名', status.optimize_cn ? nodeText(status.cn_ip, status.cn_speed, status.cn_time) : '不处理' ],
			[ '国际下载域名', com ],
			[ '版本', status.version || '' ]
		];

		dom.content(this.statusNode, rows.map(function(row) {
			return E('tr', { 'class': 'tr' }, [
				E('td', { 'class': 'td left', 'style': 'width:25%' }, [ row[0] ]),
				E('td', { 'class': 'td left' }, [ row[1] ])
			]);
		}));

		var log = status.log || '';

		if (this.logNode.value != log) {
			var atBottom = this.logNode.scrollTop + this.logNode.clientHeight >= this.logNode.scrollHeight - 20;

			this.logNode.value = log;

			if (atBottom)
				this.logNode.scrollTop = this.logNode.scrollHeight;
		}

		var testing = !!status.testing;

		[ 'test', 'test_cn', 'test_com', 'reset' ].forEach(L.bind(function(name) {
			if (this.buttons[name])
				this.buttons[name].disabled = testing;
		}, this));

		if (this.buttons.cancel)
			this.buttons.cancel.disabled = !testing;

		/* 测速结束后解析结果可能已经变化，重新检测一次 */
		if (this.wasTesting && !testing)
			this.refreshEnvironment();

		this.wasTesting = testing;
	},

	updateEnvironment: function(result) {
		var rows = (result.environment || []).map(function(item) {
			var level = LEVELS[item.level] || LEVELS.info;

			return E('tr', { 'class': 'tr' }, [
				E('td', { 'class': 'td left', 'style': 'width:25%' }, [ item.name || '' ]),
				E('td', { 'class': 'td left', 'style': 'width:4em; white-space:nowrap; font-weight:bold; color:' + level[1] }, [ level[0] ]),
				E('td', { 'class': 'td left' }, [ item.detail || '' ])
			]);
		});

		if (!rows.length)
			rows.push(E('tr', { 'class': 'tr' }, [ E('td', { 'class': 'td left' }, [ '没有取得检测结果' ]) ]));

		dom.content(this.envNode, rows);
	},

	refreshEnvironment: function() {
		var self = this;

		this.envNote.textContent = '检测中…';

		return callEnvironment().then(function(result) {
			self.updateEnvironment(result);
			self.envNote.textContent = '';
		}).catch(function(err) {
			self.envNote.textContent = '检测失败：' + err.message;
		});
	},

	runAction: function(command) {
		var self = this;

		return callAction(command).then(function(result) {
			if (!result.ok)
				ui.addNotification(null, E('p', {}, [ result.message || '操作失败' ]), 'error');

			return callStatus();
		}).then(function(status) {
			self.updateStatus(status);

			if (command == 'apply' || command == 'reset')
				return self.refreshEnvironment();
		}).catch(function(err) {
			ui.addNotification(null, E('p', {}, [ '操作失败：' + err.message ]), 'error');
		});
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
