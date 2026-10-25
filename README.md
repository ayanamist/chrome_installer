# Chrome installer archive

每天从 Google 官方下载 Windows、macOS 和 Linux 的 Chrome 文件，按版本保存到 GitHub Releases，仅将解包后重新封装的浏览器归档上传到 GitHub Releases。整个流程不运行 Chrome、updater 或安装器。

## 覆盖范围

| 平台 | 架构 | Stable | Extended Stable | 临时下载输入 |
|---|---|---|---|---|
| Windows | x86（32 位）、x64、ARM64 | ✓ | ✓ | updater CRX3 内的完整安装 EXE |
| macOS | x64、ARM64 | ✓ | ✓ | updater 的原始 CRX3 |
| Linux | x64、ARM64 | ✓ | 官方不提供 | 官方 APT 仓库的 `.deb` |

共 12 个平台／架构／通道组合。版本取自各平台、各架构实际的官方响应，可能因发布进度或灰度不同而不一致。Linux 浏览器归档从 DEB 提取；Release 说明另附 YUM 清单中同版本、同架构的官方 RPM 链接，不上传 RPM 附件。

## 自动执行

`.github/workflows/archive.yml` 在每天 **04:31（Asia/Singapore，UTC+8）**执行，也支持 Actions 页手动执行。`main` 上脚本、测试或 workflow 的变更会触发执行；Pull Request 只跑测试。

七个平台／架构归档组合统一使用 Ubuntu 24.04 runner，测试作为各归档任务内的一个步骤执行。所有文件均按 Google 官方清单核对大小和 SHA-256；Windows 从 PE 元数据检查版本，macOS 检查 Info.plist 版本。所有平台都会检查实际可执行文件的架构、验证重新封装的归档：Windows 执行 `7z t`，macOS／Linux 逐文件比对 tar.xz 解压内容的 SHA-256 和符号链接。

不需要 Chrome Enterprise token 或个人 PAT。发布任务使用仓库自带的 `GITHUB_TOKEN`，只有发布任务申请 `contents: write`。原始 payload 仅作临时下载输入；Release 只保存重新压缩的浏览器文件，二进制不提交到 Git。

GitHub 的定时任务可能排队或延迟，只对默认分支生效；公共仓库长期无活动时 GitHub 可能停用定时执行。Actions 的中间 artifact 保留 1 天，Release 中的附件长期保留。

## Tag、Release 与附件命名

- tag 使用**纯四段版本号**，例如 `156.0.8078.48`。
- Release 名称为 `156.0.8078.48 · Stable` 或 `152.0.7977.169 · Extended Stable`。
- 同一版本出现在两个通道时，共用一个 tag 和 Release，名称为 `版本号 · Stable / Extended Stable`。
- Release 描述依据已上传的浏览器归档及内部来源元数据，列出通道、平台、架构及对应的 Google 官方原包下载链接（CRX3／DEB，以及匹配版本的 EXE／ZIP／RPM）。
- 已存在且附件齐全的组合直接跳过，不重复下载或重新压缩；新增平台、架构或通道会补充到现有版本。
- 不覆盖已有版本的附件。重试遇到同名不同哈希的附件会报错，避免静默替换已归档的内容。
- 新 Release 先作为 draft 创建，全部附件上传成功后才发布。失败的 draft 可由下次执行继续补齐。

每个组合只上传一个浏览器归档：

| 平台 | Release 附件示例 |
|---|---|
| Windows | `chrome-windows-x86-stable-156.0.8078.48-browser.7z` |
| macOS | `chrome-macos-arm64-stable-156.0.8078.47-browser.tar.xz` |
| Linux | `chrome-linux-x64-stable-156.0.8078.47-browser.tar.xz` |

官方原包不作为 Release 附件上传。需要原始文件时，从 Release 描述中对应通道、平台、架构的 Google 官方链接下载：Windows/macOS 保留构建输入 CRX3，并补充同版本、同架构和通道的 Windows `*_chrome_installer_uncompressed.exe` 或 macOS updater ZIP（可获取时）；Linux 为原始 DEB 及匹配版本、架构的 RPM。若 YUM 仓库尚未提供对应版本，则省略 RPM 链接并记录日志，不使用会变化的 `current` 地址。EXE／ZIP 的另一次官方响应也必须版本一致；最新响应不匹配时尝试按归档版本查询，仍无匹配则省略并记录实际返回版本，避免将新旧版本混在一起。

manifest 只作为工作流内部中间数据，用于校验归档及生成 Release 说明，不作为 Release 附件；不上传原始 EXE、EXE 的 XZ、CRX3、DEB、JSON 或 SHA256SUMS。后续增量发布从现有 Release 的来源表恢复已覆盖组合及已记录的 EXE／ZIP／RPM 链接，无需保留公开的元数据文件。来源表并非 Google 所有安装分发格式的目录：没有列举 Windows MSI／Enterprise Bundle、macOS DMG／PKG 或 Chrome for Testing。

浏览器归档的根目录分别是 Windows 的 `Chrome-bin`、macOS 的 `Google Chrome.app`、Linux 的 `chrome`。保留文件内容、可执行权限和符号链接，不重签名、不移除本地化文件。Linux 依赖库和系统集成仍需使用者的系统提供。

## 阶段日志

本地和 runner 使用相同的 JSON 行日志，包含 UTC 时间、通道、平台、架构和版本。每个阶段分别记录开始、结束、实际耗时、本 Python 进程 CPU 时间及已结束子进程 CPU 时间。

- 下载每 15 秒记录已下载字节、总大小、百分比和平均 MiB/s，结束时记录大小／SHA-256 校验结果。
- 解包、版本／架构检查、7z／tar.xz 压缩、归档回读校验、哈希、Release 上传均有独立计时。
- 长阶段每 30 秒输出心跳。7-Zip 等子进程的 CPU 时间在进程结束后才会完整计入 `finished_children_cpu_seconds`，不能从中间心跳的零值判断子进程空闲。
- 启动日志记录 Python／系统版本、CPU 数、CPU affinity 和 cgroup CPU quota，方便对比本地与 runner；不打印完整环境变量或认证信息。
- 当前压缩参数仍为 7z level 5／2 线程、tar.xz preset 6；用新一轮 runner 的分阶段数据判断后续是否调整。

## 本地使用

需要 Python 3.11+ 和 7-Zip；三个目标平台都可以在 Linux 上归档，不依赖 PowerShell、codesign 或目标平台运行环境。归档流程验证官方 HTTPS 清单的哈希，不执行原生 Authenticode／codesign 签名验证。不会自动安装本地依赖。

只查看 Google 返回的版本和下载地址：

```bash
python scripts/chrome_archive.py --platform windows --arch x86 --query-only
python scripts/chrome_archive.py --platform macos --arch arm64 --query-only
python scripts/chrome_archive.py --platform linux --arch arm64 --query-only
```

下载并封装，不发布到 GitHub：

```bash
python scripts/chrome_archive.py --platform linux --arch x64 --seven-zip 7zz
python scripts/chrome_archive.py --platform macos --arch arm64 --seven-zip 7zz
```

Linux / WSL 上归档 Windows：

```bash
python scripts/chrome_archive.py --platform windows --arch x64 \
  --seven-zip 7zz
```

Windows 支持 `--arch x86`（32 位）、`x64`、`arm64`；macOS 和 Linux 支持 `x64`、`arm64`。

`--channel stable`／`--channel extended` 可单独选择通道。Linux 不接受 `extended`。`--repository OWNER/REPO` 可查询现有 Release，跳过已归档的组合；此参数只进行读取，不发布文件。

在本地预览待发布的 Release 标题与描述：

```bash
python scripts/publish_releases.py --input dist --repository OWNER/REPO --preview
```

实际发布仅通过显式调用：

```bash
python scripts/publish_releases.py --input dist --repository OWNER/REPO --target COMMIT_SHA
```

验证脚本和测试：

```bash
python -m unittest discover -s tests -v
python -m py_compile scripts/*.py
```

协议、验证边界与证据来源见 [协议及归档约定](docs/protocol.md)。
