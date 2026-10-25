# 协议及归档约定

范围：每日下载 Chrome 官方 Windows／macOS／Linux payload，仅发布重新压缩的浏览器归档，Windows 覆盖 x86（32 位）／x64／ARM64，macOS 和 Linux 覆盖 x64／ARM64，共 12 个有效通道组合。日期：2026-10-25。请求协议：Omaha JSON 3.1；实现依据：Chromium `157.0.8089.0` 源码。该文档记录当前约定，版本号不作为固定下载目标。

## Windows 和 macOS

向 `https://update.googleapis.com/service/update2/json` 发送下载清单请求，`version=0.0.0.0` 用于获取完整安装 payload；显式传入 `release_channel=stable` 或 `extended`。这是独立的下载请求，不读取本机注册 token、设备标识或企业策略，也不注册 updater。

- Windows app ID：`{8A69D345-D564-463C-AFF1-A69D9E530F96}`，AP 指定 `-arch_x86`／`-arch_x64`／`-arch_arm64`。
- macOS app ID：`com.google.Chrome`，请求的 OS／arch 对应目标架构。
- 不声明 `acceptformat`。Windows 必须返回 `.exe`，macOS 必须返回标准 `.zip`；意外返回 CRX3 等其他格式时明确报错，不回退下载。
- 只使用 Google HTTPS 地址及 Google HTTPS 重定向。
- 按同一份服务器清单核对原始 EXE／ZIP 的大小和 SHA-256；macOS 还检查标准 ZIP 头、成员路径及符号链接目标。
- Windows 直接下载清单指定的完整 EXE，使用 Linux 7-Zip 读取 PE 产品版本；使用 7-Zip 解出嵌入的 `Chrome-bin`，检查对应版本的 chrome.dll 及 chrome.exe 的 PE 架构。x86 的 PE Machine 必须为 `0x014c`，x64 为 `0x8664`，ARM64 为 `0xaa64`。
- macOS 下载并校验标准 ZIP，按 ZIP 文件模式恢复 `.app`，先写普通文件，再恢复检查过的符号链接；使用 tar.xz 保留权限和符号链接，验证 Info.plist 版本和 Mach-O 支持的架构。全流程在 Linux runner 执行，不调用原生 macOS 命令。
- 服务器返回的安装命令参数仅作为证据记录，永不执行。
- Release 原包来源直接使用此次下载的 EXE／ZIP URL，不再为了新归档额外查询另一种容器格式。
- 兼容旧 `[Google original]`／`[CRX3]` 及 CRX3+EXE／ZIP 双链接来源表；旧记录若已有同版本原生链接则优先显示原生链接，尚无链接时才尝试同目标查询及固定版本查询。`rollback_allowed` 只用于恢复历史来源的只读查询，不执行安装或回退；无法恢复时保留旧来源，避免丢失已归档的覆盖组合。新记录直接使用 `[EXE]`／`[ZIP]`，读取器同时支持两种格式。

来源：

- [安装请求先获取策略，再进入 InstallImpl](https://chromium.googlesource.com/chromium/src/+/refs/tags/157.0.8089.0/chrome/updater/update_service_impl_impl.cc)
- [目标频道赋值给 component.channel](https://chromium.googlesource.com/chromium/src/+/refs/tags/157.0.8089.0/chrome/updater/installer.cc)
- [component.channel 进入更新协议](https://chromium.googlesource.com/chromium/src/+/refs/tags/157.0.8089.0/components/update_client/update_checker.cc)
- [序列化为 release_channel](https://chromium.googlesource.com/chromium/src/+/refs/tags/157.0.8089.0/components/update_client/protocol_serializer_json.cc)
- [Google 对 Extended Stable 平台范围的说明](https://support.google.com/chrome/a/answer/16942104?hl=en)

限制：没有执行 Windows Authenticode 或 macOS codesign 原生签名验证。这是协议兼容的归档客户端，没有实现完整 updater 的 CUP 签名校验；SHA-256 来源通过 HTTPS 获取。服务器发布进度及灰度会影响实际提供的版本。原始 token 注册过程、安装、自动更新和浏览器启动均不在范围内。

## Linux

从 Google 官方 APT 仓库对应架构的 `Packages.gz` 读取 `google-chrome-stable` 的版本、Filename、Size 和 SHA256，按该清单下载 DEB，避免把版本查询与 `current` 浮动链接混用。

- x64 对应 `binary-amd64`，ARM64 对应 `binary-arm64`。
- DEB 仅作为临时下载输入；只解出 data archive 内 `opt/google/chrome` 的浏览器文件，保留源文件权限和符号链接，不执行任何包管理脚本。
- 读取 ELF 头核对目标架构，不运行浏览器以读取版本。
- 不制造 Linux Extended Stable 产物，因为官方没有该通道。
- 发布阶段读取官方 YUM `repomd.xml` 和 `primary.xml.gz`，核对压缩索引大小及 SHA-256，只为 `google-chrome-stable` 的同版本、同架构补充固定版本 RPM URL。无匹配时省略，不回退到 `current` 浮动链接，不下载／执行／上传 RPM。
- 增量发布保留已有 RPM 来源链接；历史版本即使已退出当前 YUM 索引，也不丢失已记录的地址。

来源：[官方安装平台与架构](https://support.google.com/chrome/answer/95346?hl=en)、[x64 仓库索引](https://dl.google.com/linux/chrome/deb/dists/stable/main/binary-amd64/Packages.gz)、[ARM64 仓库索引](https://dl.google.com/linux/chrome/deb/dists/stable/main/binary-arm64/Packages.gz)。

限制：校验 HTTPS 清单的包大小和 SHA-256，没有重建 apt 的 InRelease/GPG 信任链。浏览器文件只从 DEB 提取；RPM 只记录链接。YUM 索引通过 HTTPS 获取并校验 primary 的清单哈希，没有验证 RPM GPG 签名，也不保证 Google 永久保留历史下载 URL。

## 发布约定

Tag 始终为官方响应的四段 Chrome 版本号，不包含通道、平台或架构。GitHub 同一 tag 只有一个 Release，因此同版本的所有成功归档组合共用 Release。标题列出实际通道，描述列出实际覆盖的通道、平台、架构，以及 manifest 中记录的 Google 官方原始下载 URL，Windows／macOS 为原始 EXE／ZIP，Linux 另补同版本 RPM 链接。纯文件产物由构建 job 生成，统一发布 job 串行处理，避免矩阵任务争抢同一个 tag。

每个组合只上传解包后重新生成的 Windows browser.7z 或 macOS／Linux browser.tar.xz。macOS tar.xz 的根目录为完整的 `Google Chrome.app/` 应用包目录；Release 的 Assets 说明明确标注该名称和解压后的内容，并保留权限及符号链接。原始 EXE、EXE.xz、CRX3、DEB、RPM、manifest 和 SHA256SUMS 都不是 Release 附件。官方原始 URL 放在 Release 描述的来源表中。manifest 仅在临时工作流 artifact 内流转，集中发布后不作为独立公开文件保留；后续发布从已有说明表恢复来源及覆盖范围，并核对对应浏览器附件确实存在。GitHub Actions 临时 artifact 不作为长期存储；最终存储位置是 Release assets。仅在全部附件上传成功后发布新 draft；重试补充缺失附件，不覆盖已有文件。

可用以下命令验证实现和生成的描述：

```bash
python -m unittest discover -s tests -v
python scripts/chrome_archive.py --platform windows --arch x86 --query-only
python scripts/chrome_archive.py --platform macos --arch x64 --query-only
python scripts/chrome_archive.py --platform linux --arch arm64 --query-only
python scripts/publish_releases.py --input dist --repository OWNER/REPO --preview
```

Actions 的时间和默认分支语义依据 [GitHub schedule 文档](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)，版本 tag 与发布过程依据 [GitHub Releases API](https://docs.github.com/en/rest/releases/releases)。7-Zip 固定在 26.05，下载官方 portable 版本并核对 GitHub Release asset SHA-256；七个平台／架构归档组合及发布任务统一使用 Ubuntu 24.04 runner。


## GitHub Actions 依赖版本

Workflow 使用官方维护的大版本 tag，自动跟随该大版本内的兼容更新，不固定小版本号或 commit hash。大版本升级时通过对应仓库的 `releases/latest` 核对最新稳定大版本，并检查 action.yml 输入兼容性。

2026-10-25 已确认以下大版本 tag 与对应的最新稳定发布指向同一提交：

| Action | Workflow 使用的大版本 tag | 本次核对的最新稳定发布 |
|---|---|---|
| actions/checkout | v7 | [v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1) |
| actions/setup-python | v7 | [v7.1.0](https://github.com/actions/setup-python/releases/tag/v7.1.0) |
| actions/upload-artifact | v7 | [v7.0.2](https://github.com/actions/upload-artifact/releases/tag/v7.0.2) |
| actions/download-artifact | v8 | [v8.0.2](https://github.com/actions/download-artifact/releases/tag/v8.0.2) |

以上四个 Action 都使用 Node 24，Ubuntu 24.04 的 GitHub hosted runner 支持该运行环境。upload-artifact 保持 archive=true 的默认行为，download-artifact 使用 merge-multiple=true；不改变现有目录布局和中间 artifact 语义。


## 可复现的诊断日志

阶段日志包含 UTC 时间、版本／平台／架构／通道、事件名、`wall_seconds`、`self_cpu_seconds`、`finished_children_cpu_seconds`。最后一项只累计已结束的子进程，适合在 stage_end 判断 7-Zip CPU 成本。长阶段每 30 秒输出心跳，下载每 15 秒记录进度和平均速率；失败事件明确标记阶段及异常类型。日志字段中的 token／password／authorization／cookie 等敏感键会被脱敏，不转储环境变量或请求头。

2026-10-25 的[首轮任务 40177246723](https://github.com/ayanamist/chrome_installer/actions/runs/40177246723)（提交 5fa3743、旧发布范围）已经成功完成：七个归档 job 为 2:29～12:54，其中 Windows x86 9:59、macOS ARM64 11:33。旧日志将两个通道串行的下载／校验／解包／压缩合并在同一步，不能由该时长推断纯下载耗时。因此新版本先补阶段计时，不根据本地基准推断 runner 瓶颈，也不擅自调整压缩级别。原始 EXE 的额外压缩因发布范围调整已移除。

2026-10-25 的[带阶段日志任务 40180189315](https://github.com/ayanamist/chrome_installer/actions/runs/40180189315)已完成，发布 3 个 Release、12 个浏览器归档，未上传原包或元数据附件。runner 实测：Linux x64 下载 0.762 秒、tar.xz 压缩 171.689 秒（self CPU 171.360 秒）；macOS x64 Stable 下载 4.978 秒、tar.xz 压缩 309.056 秒（self CPU 309.026 秒）；Windows x64 Stable 下载 2.232 秒、7z 压缩 115.706 秒（已结束子进程 CPU 174.67 秒）。这些组合的主要耗时为 CPU 压缩，两个通道在同一 job 内串行；结论仅对应这次 runner 实测，压缩参数尚未调整。

## 原包来源覆盖与历史格式迁移（2026-10-25）

当前下载链路为 Windows EXE → Chrome-bin → browser.7z；macOS ZIP → Google Chrome.app → browser.tar.xz；Linux DEB → opt/google/chrome → browser.tar.xz。不声明 acceptformat，不再提取 CRX3 内嵌 EXE。源码删除了 CRX3 校验及解包分支；标准 ZIP 路径／符号链接安全检查、PE／Mach-O／ELF 版本或架构校验、归档回读校验均保留。

切换前的实测：Windows x64 Stable `156.0.8078.48` 原始 uncompressed EXE 大小 527126440 字节，SHA-256 为 `84e13e72c71a3b7d767c115e8e94ec81cb563f9bf9a782407a54afceaa37f55a`，与旧 CRX3 内同名成员完全一致。CRX3 与 EXE 的 codebase 不同，因此迁移来源使用实际查询结果，不替换后缀拼接 URL。无 acceptformat 的 macOS updater ZIP 是普通 Chrome 分发文件，不是 Chrome for Testing。

旧 Release 附件不因输入格式变化而重建或覆盖。描述刷新仍仅针对本轮官方响应的版本集合，旧来源读取兼容用于保留通道／平台／架构与已记录的原始链接。原生链接已经存在的新记录不会再次查询另一份下载清单。

来源表不宣称覆盖 Google 所有分发格式。Windows MSI／Enterprise Bundle、macOS DMG／PKG 未做历史版本定位，不放浮动 latest/current URL。依据：[Windows 企业包内容](https://support.google.com/chrome/a/answer/7650032)、[macOS 下载说明](https://support.google.com/chrome/a/answer/9020580?hl=en)、[macOS PKG 与 DMG](https://cloud.google.com/blog/products/chrome-enterprise/simplifying-chrome-deployments-on-macos)。Chrome for Testing 属于另一种构建，不混入普通 Chrome 原包来源。

## 当前可获取版本的 Release 描述更新

各归档任务查询官方清单后，将该平台／架构本次实际获得的通道与版本写入根目录的 `chrome-平台-架构-discovery.json`。即使浏览器归档已存在、跳过下载和压缩，也必须保留这份记录；`has_discovery=true` 控制上传临时 Actions artifact。查询记录与浏览器 manifest 均不作为 Release 附件。

发布器先读取本次所有 discovery 记录，取各平台／架构／通道版本的并集，再合并本次新建归档的版本，只通过版本 tag 查询对应 Release。不会分页列举所有历史 Release，也不会把最大版本号当成唯一当前版本；Stable、Extended Stable 及各平台发布进度不同的版本都保留在这批集合内。复用归档阶段的结果，不在发布阶段重新选择一批当前版本。仅有本地归档 manifest 的输入仍按该批归档版本发布及刷新。

只为集合中的版本使用当前 checkout 的 `release_text()` 模板重新生成标题和描述，没有新增浏览器附件时仍执行；已不在本次查询结果中的历史版本不改写。对应版本内已经归档的其他平台和通道、已记录的 EXE／ZIP／RPM 链接继续保留。旧版排版或手工追加说明由当前模板覆盖，相同结果省略重复 PATCH。说明刷新只修改 name／body，不修改 tag、附件、draft 或 prerelease 状态。无法恢复已有浏览器附件的来源时明确失败，避免静默丢失覆盖组合。
