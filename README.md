# Chrome installer archive

每天从 Google 官方下载 Windows、macOS 和 Linux 的 Chrome 文件，按版本保存到 GitHub Releases，并提供解包后重新封装的浏览器归档。整个流程不运行 Chrome、updater 或安装器。

## 覆盖范围

| 平台 | 架构 | Stable | Extended Stable | 原始文件 |
|---|---|---|---|---|
| Windows | x86（32 位）、x64、ARM64 | ✓ | ✓ | updater CRX3 内的完整安装 EXE |
| macOS | x64、ARM64 | ✓ | ✓ | updater 的原始 CRX3 |
| Linux | x64、ARM64 | ✓ | 官方不提供 | 官方 APT 仓库的 `.deb` |

共 12 个平台／架构／通道组合。版本取自各平台、各架构实际的官方响应，可能因发布进度或灰度不同而不一致。Linux 仅归档 DEB；暂不额外归档 RPM。

## 自动执行

`.github/workflows/archive.yml` 在每天 **04:31（Asia/Singapore，UTC+8）**执行，也支持 Actions 页手动执行。`main` 上脚本、测试或 workflow 的变更会触发执行；Pull Request 只跑测试。

七个平台／架构归档组合统一使用 Ubuntu 24.04 runner，测试作为各归档任务内的一个步骤执行。所有文件均按 Google 官方清单核对大小和 SHA-256；Windows 从 PE 元数据检查版本，macOS 检查 Info.plist 版本。所有平台都会检查实际可执行文件的架构、验证重新封装的归档：Windows 执行 `7z t`，macOS／Linux 逐文件比对 tar.xz 解压内容的 SHA-256 和符号链接。

不需要 Chrome Enterprise token 或个人 PAT。发布任务使用仓库自带的 `GITHUB_TOKEN`，只有发布任务申请 `contents: write`。原始 payload 只保存在 Release 附件中，不提交到 Git。

GitHub 的定时任务可能排队或延迟，只对默认分支生效；公共仓库长期无活动时 GitHub 可能停用定时执行。Actions 的中间 artifact 保留 1 天，Release 中的附件长期保留。

## Tag、Release 与附件命名

- tag 使用**纯四段版本号**，例如 `156.0.8078.48`。
- Release 名称为 `156.0.8078.48 · Stable` 或 `152.0.7977.169 · Extended Stable`。
- 同一版本出现在两个通道时，共用一个 tag 和 Release，名称为 `版本号 · Stable / Extended Stable`。
- Release 描述按实际上传成功的 manifest 列出通道、平台和架构。
- 已存在且附件齐全的组合直接跳过，不重复下载或重新压缩；新增平台、架构或通道会补充到现有版本。
- 不覆盖已有版本的附件。重试遇到同名不同哈希的附件会报错，避免静默替换已归档的内容。
- 新 Release 先作为 draft 创建，全部附件上传成功后才发布。失败的 draft 可由下次执行继续补齐。

每个组合提供四个附件：

| 内容 | Windows 示例 |
|---|---|
| 原始文件；名称含 `uncompressed` 时，直接把完整原文件压成 XZ，并在原文件名后加 `.xz` | `windows-x64-stable-156.0.8078.48_chrome_installer_uncompressed.exe.xz` |
| 解包后的浏览器文件：Windows 为 7z，macOS／Linux 为 tar.xz | `chrome-windows-x64-stable-156.0.8078.48-browser.7z` |
| 官方来源、校验结果、附件哈希等 | `chrome-windows-x64-stable-156.0.8078.48-manifest.json` |
| 两个大文件及 manifest 的 SHA-256 | `chrome-windows-x64-stable-156.0.8078.48-SHA256SUMS` |

原始文件附件添加平台、架构、通道前缀以避免重名；XZ 解压恢复原始 EXE 字节，原始文件名记录在附件名和 manifest 中。不含 `uncompressed` 的原始 CRX3／DEB 直接上传，不再额外压缩。

浏览器归档的根目录分别是 Windows 的 `Chrome-bin`、macOS 的 `Google Chrome.app`、Linux 的 `chrome`。保留文件内容、可执行权限和符号链接，不重签名、不移除本地化文件。Linux 依赖库和系统集成仍需使用者的系统提供，解包文件不能替代完整的软件包安装过程。

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
