# GitHub Actions 打包与发布

## 触发规则

| 事件 | 结果 |
|---|---|
| 推送到 **`release/test`** 分支 | 构建 Beta，自动建立 GitHub **Pre-release** |
| 推送 **`vX.Y.Z`** 或 **`X.Y.Z`** tag | 构建正式版，自动建立普通 GitHub Release，并标记为 latest |
| 推送到 main / 其他分支 | 不触发这个发布工作流 |
| 非三段语义版本 tag | 不作为正式版发布；匹配触发过滤器但格式无效的 tag 会被校验拒绝 |

`release/test` 是一个包含斜杠的完整分支名，不是 release 和 test 两个分支。

工作流：`.github/workflows/release.yml`。先跑回归测试，再打包和校验，最后由单独的发布 job 上传 Release。构建 job 只有读取仓库权限，发布 job 才请求 `contents: write`。

## 版本规则

仓库中的基线始终维护为普通的 `X.Y.Z`，例如 `0.2.2`：

- `files/usr/bin/xbox-accel` 的 `VERSION`；
- `Makefile` 的 `PKG_VERSION`。

这两个值必须一致。正式 tag 也必须与基线一致，不一致时 CI 直接失败，避免 tag 和实际运行版本不同。

假设基线是 `0.2.2`、工作流编号 17、提交短 SHA 为 `0123456789ab`：

| 渠道 | IPK 中的版本 | GitHub Release tag |
|---|---|---|
| Beta | `0.2.2~beta.17.g0123456789ab-1` | `beta-v0.2.2-17-g0123456789ab` |
| 正式 | `0.2.2-1` | `v0.2.2`（也接受 `0.2.2`） |

Beta 使用 `~beta`，不是简单的 `-beta`，保证 opkg 排序为：

```text
0.2.2~beta.9... < 0.2.2~beta.17... < 0.2.2 < 0.2.3~beta.1...
```

**已经装了正式 `0.2.2` 的设备，不会把同基线 `0.2.2~beta` 当成升级。**开发下一个版本时，先将两个基线改成 `0.2.3`，再推送测试分支；或者明确使用 opkg 的降级选项测试同基线 Beta。

Beta 版本由 CI 自动生成，只修改打包后的运行脚本和源码包中的 Makefile，不会回写、提交或推送到仓库。Beta 自动生成的 tag 以 `beta-v` 开头，不匹配正式版 tag 触发过滤器，避免递归发布。

## Release 产物

每个版本包含：

1. `luci-app-xbox-accel_<版本>-1_all.ipk`；
2. `xbox-accel-<版本>.tar.gz`，包含源码、构建脚本、测试和工作流；
3. `BUILD_INFO.json`：渠道、版本、源码基线、提交 SHA、产物文件名；
4. `INSTALL.txt`：本次版本的准确安装命令和文件名，Beta 安装应以此为准；
5. `SHA256SUMS.txt`：以上四个文件的 SHA256。

全部是 Shell / LuCI 脚本，因此只打 **all 架构 IPK**，不需要 x86 / ARM 二进制矩阵。这里使用已有的 Python 打包器，不声称使用 OpenWrt SDK 编译，也不生成 apk 格式安装包。

打包器固定归档时间，给定同一份源码、版本和提交 SHA，产物可复现。

## 发布行为与权限

- 仅使用自动提供的 `GITHUB_TOKEN`，不需要填写个人 PAT。
- 发布时校验构建信息、提交 SHA 和每个文件的校验和。
- 正式版还确认远端 tag 指向触发构建的提交；tag 被移动时拒绝发布。
- 首次创建为 draft，上传全部文件成功后才公开，避免出现没有安装包的空正式发布。
- 同一工作流重跑复用同一个 tag，并替换对应文件；上传失败不会把首次 draft 公开。若仓库启用了不可变 Release，GitHub 会禁止修改已公开资产，此时应发布新 tag，而不是强行重跑替换。
- Beta 设置 `prerelease=true`、`latest=false`；正式版设置 `prerelease=false`、`latest=true`。
- 正式发布旧版本 tag 同样会被标记为 latest；不要补推历史 tag 来替代发布新版本。

GitHub Actions 必须在仓库中启用。如果发布遇到 403，检查仓库/组织对 `GITHUB_TOKEN` 写入权限的限制。不要把 PAT 或其他令牌写进 YAML 或源码。

## 你的首次提交与触发步骤

先将插件源码、构建工具、测试、说明和工作流一起提交。不要提交系统缓存或 `dist/` 构建产物。

首次提交完成后，建立测试分支：

```sh
git switch -c release/test
git push -u origin release/test
```

以后推送该分支就会自动发布新的 Beta。

正式发布前：

1. 验证功能，并将稳定代码合并到 main；
2. 确认两个基线版本都为要发布的版本；
3. 提交版本变更并推送 main；
4. 打 tag 并推送。

```sh
# 示例：两个基线已经是 0.2.2，且已经提交
git tag -a v0.2.2 -m 'Xbox Accel 0.2.2'
git push origin v0.2.2
```

不要移动已发布的正式 tag。下一个版本应修改基线并使用新 tag。

## 本地验证与构建

```sh
python3 tests/test_ci_release.py
python3 tests/test_build_ipk.py
python3 tests/test_publish_release.py
python3 tests/test_cgi.py
node tests/test_luci.js
```

发布测试使用模拟的 gh CLI，不会访问或发布到 GitHub。Linux CI 会额外用 dpkg 检查 Beta/正式版本排序；本地也可以使用 OpenWrt 的 opkg 验证。

普通本地打包：

```sh
python3 tools/build_ipk.py
```

手动模拟 Beta 打包（示例 SHA 仅用于本地测试，不代表真实提交）：

```sh
python3 tools/build_ipk.py \
  --version '0.2.2~beta.17.g0123456789ab' \
  --commit 0123456789abcdef0123456789abcdef01234567 \
  --output-dir /tmp/xbox-beta
```

CI 使用 Node 24、Python 3.12。CI 运行语法、LuCI 替身、CGI、发布版本、归档及模拟 gh 回归测试；**没有在 GitHub runner 上自动运行真实 Xbox、完整 Passwall 或浏览器测试**。现有 ARM OpenWrt 集成测试和本机 Chrome 测试仍作为独立验证工具，不应误称为每次 CI 均执行。

工作流和脚本已可本地检查；首次推送后仍需确认 GitHub 实际运行和仓库发布权限。

参考：官方 [checkout 定义](https://raw.githubusercontent.com/actions/checkout/v5/action.yml)、[setup-node 定义](https://raw.githubusercontent.com/actions/setup-node/v5/action.yml)、[setup-python 定义](https://raw.githubusercontent.com/actions/setup-python/v6/action.yml)、[Artifact 上传定义](https://raw.githubusercontent.com/actions/upload-artifact/v5/action.yml)、[Artifact 下载定义](https://raw.githubusercontent.com/actions/download-artifact/v5/action.yml)。本地工作流检查可使用 [actionlint](https://github.com/rhysd/actionlint/releases/tag/v1.7.12)。
