# Chrome installer archive

每天从 Google 官方下载 Windows、macOS 和 Linux 的 Chrome 文件，按版本保存到 GitHub Releases，仅将解包后重新封装的浏览器归档上传到 GitHub Releases。整个流程不运行 Chrome、updater 或安装器。

## 覆盖范围

| 平台 | 架构 | Stable | Extended Stable | 临时下载输入 |
|---|---|---|---|---|
| Windows | x86（32 位）、x64、ARM64 | ✓ | ✓ | 官方原始完整 uncompressed EXE |
| macOS | x64、ARM64 | ✓ | ✓ | 官方 updater 标准 ZIP |
| Linux | x64、ARM64 | ✓ | 官方不提供 | 官方 APT 仓库的 `.deb` |

共 12 个平台／架构／通道组合。版本取自各平台、各架构实际的官方响应，可能因发布进度或灰度不同而不一致。Linux 浏览器归档从 DEB 提取；Release 说明另附 YUM 清单中同版本、同架构的官方 RPM 链接，不上传 RPM 附件。

## 当前 Release 页面

下表由 GitHub Actions 在每次发布后自动更新，列出每个平台、架构、通道最近一次查询到的版本及对应 Release 页面。Linux 官方不提供 Extended Stable。

<!-- releases:start -->
| 平台 | 架构 | Stable | Extended Stable |
|---|---|---|---|
| Windows | X86 | [156.0.8078.74](https://github.com/ayanamist/chrome_installer/releases/tag/156.0.8078.74) | [156.0.8078.48](https://github.com/ayanamist/chrome_installer/releases/tag/156.0.8078.48) |
| Windows | X64 | [156.0.8078.74](https://github.com/ayanamist/chrome_installer/releases/tag/156.0.8078.74) | [156.0.8078.74](https://github.com/ayanamist/chrome_installer/releases/tag/156.0.8078.74) |
| Windows | ARM64 | [156.0.8078.74](https://github.com/ayanamist/chrome_installer/releases/tag/156.0.8078.74) | [156.0.8078.48](https://github.com/ayanamist/chrome_installer/releases/tag/156.0.8078.48) |
| macOS | X64 | [156.0.8078.48](https://github.com/ayanamist/chrome_installer/releases/tag/156.0.8078.48) | [156.0.8078.74](https://github.com/ayanamist/chrome_installer/releases/tag/156.0.8078.74) |
| macOS | ARM64 | [156.0.8078.49](https://github.com/ayanamist/chrome_installer/releases/tag/156.0.8078.49) | [156.0.8078.48](https://github.com/ayanamist/chrome_installer/releases/tag/156.0.8078.48) |
| Linux | X64 | [156.0.8078.73](https://github.com/ayanamist/chrome_installer/releases/tag/156.0.8078.73) | 官方不提供 |
| Linux | ARM64 | [156.0.8078.73](https://github.com/ayanamist/chrome_installer/releases/tag/156.0.8078.73) | 官方不提供 |
<!-- releases:end -->

## 自动执行

`.github/workflows/archive.yml` 在每天 **04:31（Asia/Singapore，UTC+8）**执行，也支持 Actions 页手动执行。`main` 上脚本、测试或 workflow 的变更会触发执行；Pull Request 只跑测试。

七个平台／架构归档组合统一使用 Ubuntu 24.04 runner，测试作为各归档任务内的一个步骤执行。所有文件均按 Google 官方清单核对大小和 SHA-256；Windows 从 PE 元数据检查版本，macOS 检查 Info.plist 版本。所有平台都会检查实际可执行文件的架构、验证重新封装的归档：Windows 执行 `7z t`，macOS／Linux 逐文件比对 tar.xz 解压内容的 SHA-256 和符号链接。

不需要 Chrome Enterprise token 或个人 PAT。发布任务使用仓库自带的 `GITHUB_TOKEN`，只有发布任务申请 `contents: write`。原始 payload 仅作临时下载输入；Release 只保存重新压缩的浏览器文件，二进制不提交到 Git。

GitHub 的定时任务可能排队或延迟，只对默认分支生效；公共仓库长期无活动时 GitHub 可能停用定时执行。Actions 的中间 artifact 保留 1 天，Release 中的附件长期保留。

## Tag、Release 与附件命名

- tag 使用**纯四段版本号**，例如 `156.0.8078.48`。
- Release 名称为 `156.0.8078.48 · Stable` 或 `152.0.7977.169 · Extended Stable`。
- 同一版本出现在两个通道时，共用一个 tag 和 Release，名称为 `版本号 · Stable / Extended Stable`。
- Release 描述列出通道、平台、架构及 Google 官方原包下载链接（EXE／ZIP／DEB，以及匹配版本的 RPM）。同平台、同架构、相同链接合并为一行，通道标为 `Stable / Extended Stable`；不同来源链接分别保留。
- 保留两个通道的独立版本查询。同平台、同架构、同版本的原包 SHA-256 和大小一致时复用浏览器归档，不重复下载或重新压缩；不同原包若生成的归档大小和 SHA-256 一致，发布时也只上传一份。
- 已存在的旧通道附件继续跳过；缺少原包哈希时不凭版本号跨通道复用，必要时构建后比较归档哈希。既有附件不删除或重命名。
- 每次执行先由归档任务查询各平台／架构／通道的当前可获取版本，发布器只按这批版本的并集刷新对应 Release，使用当前代码的模板。已归档、无需下载的组合也会传递查询记录，因此没有新增浏览器附件时仍可刷新描述；不扫描或改写其他历史版本。保留对应版本的来源链接及已有覆盖组合，旧版排版或手工追加说明由当前模板覆盖；相同内容跳过重复写入。
- 不覆盖已有版本的附件。重试遇到同名不同哈希的附件会报错，避免静默替换已归档的内容。
- 新 Release 先作为 draft 创建，全部附件上传成功后才发布。失败的 draft 可由下次执行继续补齐。

新附件名不包含通道，相同内容由两个通道共享：

| 平台 | Release 附件示例 |
|---|---|
| Windows | `chrome-windows-x86-156.0.8078.48-browser.7z` |
| macOS | `chrome-macos-arm64-156.0.8078.47-browser.tar.xz`，解压得到完整的 `Google Chrome.app/` 应用包目录 |
| Linux | `chrome-linux-x64-156.0.8078.47-browser.tar.xz` |

若同平台、同架构、同版本的两个原包内容不同，后一个归档名在版本号后增加原包完整 SHA-256，避免覆盖。下载缓存也按原包哈希分目录，允许相同文件名对应不同内容。

官方原包不作为 Release 附件上传。需要原始文件时，从 Release 描述中对应通道、平台、架构的 Google 官方链接下载：Windows 直接使用 `*_chrome_installer_uncompressed.exe`，macOS 直接使用包含 `.app` 的 updater ZIP，Linux 使用 DEB 并补充匹配版本和架构的 RPM。Windows／macOS 请求不声明 `acceptformat`，也不回退下载 CRX3；原始 EXE／ZIP 直接按同一份下载清单的版本、大小和 SHA-256 校验。

旧 Release 的 CRX3 来源表仍可读取；优先使用已记录或可恢复的同版本 EXE／ZIP 链接，不覆盖已有浏览器附件。只有旧来源无法恢复匹配原生链接时，保留原有 CRX3 地址作为历史来源，不请求或解包 CRX3。若 YUM 仓库尚未提供对应版本，省略 RPM 链接并记录日志，不使用会变化的 `current` 地址。

每个归档任务还会生成 `*-discovery.json`，记录本次查询到的版本及复用附件的通道映射；即使浏览器附件已齐，也上传这份很小的内部记录。发布器复用这些结果，不重新查询一遍归档版本，避免发布进度变化造成版本偏差。仅有本地归档 manifest 时，以该批输入的版本为范围。

manifest 和 discovery 文件只作为工作流内部中间数据，不作为 Release 附件；不上传原始 EXE、EXE 的 XZ、CRX3、DEB、JSON 或 SHA256SUMS。新描述的隐藏 HTML 注释保留精简的公开来源记录：平台、架构、通道、版本、来源 URL、已知原包大小／SHA-256 及附件名，用于后续恢复共享关系；旧描述继续从来源表恢复。来源表并非 Google 所有安装分发格式的目录：没有列举 Windows MSI／Enterprise Bundle、macOS DMG／PKG 或 Chrome for Testing。

归档内的浏览器目录分别是 Windows 的 `Chrome-bin`、macOS 的 `Google Chrome.app`、Linux 的 `chrome`。保留文件内容、可执行权限和符号链接，不重签名、不移除本地化文件。Linux 依赖库和系统集成仍需使用者的系统提供。

## 解压使用时恢复 DM token

提供 Windows 的 PowerShell 脚本和 macOS／Linux 的 Bash 脚本。针对已通过 Chrome 企业云管理注册、但持久 DM token 丢失而导致策略扩展每次启动被卸载再安装的场景，脚本从最近的本机机器级云策略缓存恢复 token。没有此类云策略的普通使用场景无需运行。

恢复工具作为仓库中的独立脚本提供，不打入浏览器归档。需要时从 [scripts/chrome-dm-token/](scripts/chrome-dm-token/README.md) 获取对应平台脚本及使用说明。

先在 `chrome://policy` 重新加载策略，然后正常完全退出 Chrome。从仓库根目录执行：

```powershell
# Windows：管理员 PowerShell；默认使用当前用户的 Chrome User Data。
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\chrome-dm-token\fix-dm-token.ps1
# 自定义目录：在上面命令末尾加 -UserDataDir 'D:\Chrome User Data'
```

```bash
# macOS / Linux：使用 Chrome 的同一用户，不要 sudo。
bash ./scripts/chrome-dm-token/fix-dm-token.sh
# 自定义目录：加 --user-data-dir '/path/to/Chrome User Data'
```

恢复后正常启动 Chrome 并检查策略和扩展跨重启保留情况。脚本保留任何已有 token；只接受匹配本机、active 且 24 小时内的缓存，不生成新注册、不安装 updater。macOS／Linux 通常会自行保存 token，仅缺失时需要此脚本。实现证据和验证边界见 [DM token 恢复约定](docs/dm-token.md)。

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
