# 协议及归档约定

范围：每日下载 Chrome 官方 Windows／macOS／Linux payload，仅发布重新压缩的浏览器归档，Windows 覆盖 x86（32 位）／x64／ARM64，macOS 和 Linux 覆盖 x64／ARM64，共 12 个有效通道组合。日期：2026-10-25。请求协议：Omaha JSON 3.1；实现依据：Chromium `157.0.8089.0` 源码。该文档记录当前约定，版本号不作为固定下载目标。

## Windows 和 macOS

向 `https://update.googleapis.com/service/update2/json` 发送下载清单请求，`version=0.0.0.0` 用于获取完整安装 payload；显式传入 `release_channel=stable` 或 `extended`。这是独立的下载请求，不读取本机注册 token、设备标识或企业策略，也不注册 updater。

- Windows app ID：`{8A69D345-D564-463C-AFF1-A69D9E530F96}`，AP 指定 `-arch_x86`／`-arch_x64`／`-arch_arm64`。
- macOS app ID：`com.google.Chrome`，请求的 OS／arch 对应目标架构。
- 请求 `acceptformat=crx3`，禁用对增量格式的依赖，获取可独立检查的完整包。
- 只使用 Google HTTPS 地址及 Google HTTPS 重定向。
- 按服务器清单核对包大小和 SHA-256，检查 CRX3 头部及 ZIP 路径。
- Windows 从 CRX3 提取清单指定的完整 EXE，使用 Linux 7-Zip 读取 PE 产品版本；使用 7-Zip 解出嵌入的 `Chrome-bin`，检查对应版本的 chrome.dll 及 chrome.exe 的 PE 架构。x86 的 PE Machine 必须为 `0x014c`，x64 为 `0x8664`，ARM64 为 `0xaa64`。
- macOS 下载并校验 CRX3，按 ZIP 文件模式恢复 `.app`，先写普通文件，再恢复检查过的符号链接；使用 tar.xz 保留权限和符号链接，验证 Info.plist 版本和 Mach-O 支持的架构。全流程在 Linux runner 执行，不调用原生 macOS 命令。
- 服务器返回的安装命令参数仅作为证据记录，永不执行。

来源：

- [安装请求先获取策略，再进入 InstallImpl](https://chromium.googlesource.com/chromium/src/+/refs/tags/157.0.8089.0/chrome/updater/update_service_impl_impl.cc)
- [目标频道赋值给 component.channel](https://chromium.googlesource.com/chromium/src/+/refs/tags/157.0.8089.0/chrome/updater/installer.cc)
- [component.channel 进入更新协议](https://chromium.googlesource.com/chromium/src/+/refs/tags/157.0.8089.0/components/update_client/update_checker.cc)
- [序列化为 release_channel](https://chromium.googlesource.com/chromium/src/+/refs/tags/157.0.8089.0/components/update_client/protocol_serializer_json.cc)
- [Google 对 Extended Stable 平台范围的说明](https://support.google.com/chrome/a/answer/16942104?hl=en)

限制：没有执行 Windows Authenticode 或 macOS codesign 原生签名验证。这是协议兼容的归档客户端，没有实现完整 updater 的 CUP 签名校验或 CRX3 publisher proof 验证；SHA-256 来源通过 HTTPS 获取。服务器发布进度及灰度会影响实际提供的版本。原始 token 注册过程、安装、自动更新和浏览器启动均不在范围内。

## Linux

从 Google 官方 APT 仓库对应架构的 `Packages.gz` 读取 `google-chrome-stable` 的版本、Filename、Size 和 SHA256，按该清单下载 DEB，避免把版本查询与 `current` 浮动链接混用。

- x64 对应 `binary-amd64`，ARM64 对应 `binary-arm64`。
- DEB 仅作为临时下载输入；只解出 data archive 内 `opt/google/chrome` 的浏览器文件，保留源文件权限和符号链接，不执行任何包管理脚本。
- 读取 ELF 头核对目标架构，不运行浏览器以读取版本。
- 不制造 Linux Extended Stable 产物，因为官方没有该通道。

来源：[官方安装平台与架构](https://support.google.com/chrome/answer/95346?hl=en)、[x64 仓库索引](https://dl.google.com/linux/chrome/deb/dists/stable/main/binary-amd64/Packages.gz)、[ARM64 仓库索引](https://dl.google.com/linux/chrome/deb/dists/stable/main/binary-arm64/Packages.gz)。

限制：校验 HTTPS 清单的包大小和 SHA-256，没有重建 apt 的 InRelease/GPG 信任链。只覆盖 DEB，不额外归档 RPM。

## 发布约定

Tag 始终为官方响应的四段 Chrome 版本号，不包含通道、平台或架构。GitHub 同一 tag 只有一个 Release，因此同版本的所有成功归档组合共用 Release。标题列出实际通道，描述列出实际覆盖的通道、平台、架构，以及 manifest 中记录的 Google 官方原始下载 URL。纯文件产物由构建 job 生成，统一发布 job 串行处理，避免矩阵任务争抢同一个 tag。

每个组合只上传解包后重新生成的 Windows browser.7z 或 macOS／Linux browser.tar.xz。原始 EXE、EXE.xz、CRX3、DEB、manifest 和 SHA256SUMS 都不是 Release 附件。官方原始 URL 放在 Release 描述的来源表中。manifest 仅在临时工作流 artifact 内流转，集中发布后不作为独立公开文件保留；后续发布从已有说明表恢复来源及覆盖范围，并核对对应浏览器附件确实存在。GitHub Actions 临时 artifact 不作为长期存储；最终存储位置是 Release assets。仅在全部附件上传成功后发布新 draft；重试补充缺失附件，不覆盖已有文件。

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
