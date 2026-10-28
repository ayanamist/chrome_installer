# 开发与运维手册

面向维护者：自动化流程、发布规则和本地运行方式。最终用户说明见根目录 [README](../README.md)；协议、验证边界与证据来源见 [协议及归档约定](protocol.md)。

## 自动执行

`.github/workflows/archive.yml` 在每天 **11:31 UTC**执行，也支持 Actions 页手动执行。`main` 上脚本、测试或 workflow 的变更会触发执行；Pull Request 只跑测试。

七个平台／架构归档组合统一使用 Ubuntu 24.04 runner，测试作为各归档任务内的一个步骤执行。所有文件均按 Google 官方清单核对大小和 SHA-256；Windows 从 PE 元数据检查版本，macOS 检查 Info.plist 版本。所有平台都会检查实际可执行文件的架构、验证重新封装的归档：Windows 执行 `7z t`，macOS／Linux 逐文件比对 tar.xz 解压内容的 SHA-256 和符号链接。

不需要 Chrome Enterprise token 或个人 PAT。发布任务使用仓库自带的 `GITHUB_TOKEN`，只有发布任务申请 `contents: write`。原始 payload 仅作临时下载输入；Release 只保存重新压缩的浏览器文件，二进制不提交到 Git。

GitHub 的定时任务可能排队或延迟，只对默认分支生效；公共仓库长期无活动时 GitHub 可能停用定时执行。Actions 的中间 artifact 保留 1 天，Release 中的附件长期保留。

## Tag、Release 与附件规则

- tag 使用纯四段版本号，例如 `156.0.8078.48`；Release 名称为 `版本号 · Stable`、`版本号 · Extended Stable`，同版本出现在两个通道时共用一个 Release，名称为 `版本号 · Stable / Extended Stable`。
- Release 描述列出通道、平台、架构及 Google 官方原包链接（EXE／ZIP／DEB，以及匹配版本的 RPM）。同平台、同架构、相同链接合并为一行，通道标为 `Stable / Extended Stable`；不同来源链接分别保留。
- 两个通道独立查询版本。同平台、同架构、同版本的原包 SHA-256 和大小一致时复用浏览器归档；不同原包若生成的归档大小和 SHA-256 一致，发布时也只上传一份。缺少原包哈希时不凭版本号跨通道复用。
- 附件名不含通道。若同平台、同架构、同版本的两个原包内容不同，后一个归档名在版本号后增加原包完整 SHA-256；下载缓存也按原包哈希分目录。
- 不覆盖、删除或重命名已有附件；重试遇到同名不同哈希的附件会报错。
- 新 Release 先作为 draft 创建，全部附件上传成功后才发布。失败的 draft 可由下次执行继续补齐。
- 每次执行先由归档任务查询各平台／架构／通道的当前版本，写入 `*-discovery.json`（记录版本及复用附件的通道映射，即使浏览器附件已齐也上传）。发布器只按这批版本的并集，用当前代码的模板刷新对应 Release 的标题和描述，不扫描或改写其他历史版本。
- manifest 和 discovery 文件仅为工作流内部数据，不作为 Release 附件；不上传原始 EXE、DEB、CRX3、JSON 或 SHA256SUMS。新描述的隐藏 HTML 注释保留精简来源记录（平台、架构、通道、版本、来源 URL、已知原包大小／SHA-256、附件名），用于后续恢复共享关系；旧描述继续从来源表恢复，旧 CRX3 来源仅作历史记录读取，不请求或解包 CRX3。
- 若 YUM 仓库尚未提供对应版本，省略 RPM 链接并记录日志，不使用会变化的 `current` 地址。
- 来源表不是 Google 所有分发格式的目录：不含 Windows MSI／Enterprise Bundle、macOS DMG／PKG 或 Chrome for Testing。

归档内的浏览器目录：Windows 为 `Chrome-bin`，macOS 为 `Google Chrome.app`，Linux 为 `chrome`。保留文件内容、可执行权限和符号链接，不重签名、不移除本地化文件。

## 阶段日志

本地和 runner 使用相同的 JSON 行日志，包含 UTC 时间、通道、平台、架构和版本。每个阶段分别记录开始、结束、实际耗时、本 Python 进程 CPU 时间及已结束子进程 CPU 时间。

- 下载每 15 秒记录已下载字节、总大小、百分比和平均 MiB/s，结束时记录大小／SHA-256 校验结果。
- 解包、版本／架构检查、7z／tar.xz 压缩、归档回读校验、哈希、Release 上传均有独立计时。
- 长阶段每 30 秒输出心跳。7-Zip 等子进程的 CPU 时间在进程结束后才会完整计入 `finished_children_cpu_seconds`，不能从中间心跳的零值判断子进程空闲。
- 启动日志记录 Python／系统版本、CPU 数、CPU affinity 和 cgroup CPU quota；不打印完整环境变量或认证信息。
- 当前压缩参数为 7z level 5／2 线程、tar.xz preset 6；用新一轮 runner 的分阶段数据判断后续是否调整。

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
python scripts/chrome_archive.py --platform windows --arch x64 --seven-zip 7zz
```

Windows 支持 `--arch x86`、`x64`、`arm64`；macOS 和 Linux 支持 `x64`、`arm64`。`--channel stable`／`--channel extended` 可单独选择通道，Linux 不接受 `extended`。`--repository OWNER/REPO` 可查询现有 Release，跳过已归档的组合；此参数只读取，不发布文件。

预览待发布的 Release 标题与描述：

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
