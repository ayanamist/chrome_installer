# 开发与运维手册

面向维护者：自动化流程、发布规则和本地运行方式。最终用户说明见根目录 [README](../README.md)；协议、验证边界与证据来源见 [协议及归档约定](protocol.md)。

## 自动执行

`.github/workflows/archive.yml` 在每天 **11:31 UTC**执行，也支持 Actions 页手动执行。`main` 上脚本、测试、workflow 或 `chrome-state.json` 的变更会触发执行；Pull Request 只跑测试。

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
- 每次执行先由归档任务查询各平台／架构／通道的更新，写入 `*-discovery.json`（记录本轮选用版本及复用附件的通道映射，即使浏览器附件已齐也上传）；有状态查询返回 `noupdate` 时选用 JSON 中的已发布版本。发布器只按这批版本的并集，用当前代码的模板刷新对应 Release 的标题和描述，不扫描或改写其他历史版本。
- manifest 和 discovery 文件仅为工作流内部数据，不作为 Release 附件；不上传原始 EXE、DEB、CRX3、JSON 或 SHA256SUMS。新描述的隐藏 HTML 注释保留精简来源记录（平台、架构、通道、版本、来源 URL、已知原包大小／SHA-256、附件名），用于后续恢复共享关系；旧描述继续从来源表恢复，旧 CRX3 来源仅作历史记录读取，不请求或解包 CRX3。
- 若 YUM 仓库尚未提供对应版本，省略 RPM 链接并记录日志，不使用会变化的 `current` 地址。
- 来源表不是 Google 所有分发格式的目录：不含 Windows MSI／Enterprise Bundle、macOS DMG／PKG 或 Chrome for Testing。

归档内的浏览器目录：Windows 为 `Chrome-bin`，macOS 为 `Google Chrome.app`，Linux 为 `chrome`。保留文件内容、可执行权限和符号链接，不重签名、不移除本地化文件。

## README 版本回退防护（2026-10-28）

范围：`scripts/update_readme.py` 的下载表更新；版本基线为 `5ceabdb`。`8772f11` 的表格中 Windows x64、macOS x64 的 Extended Stable 均为 `156.0.8078.74`，`5ceabdb` 将两者降回 `156.0.8078.48`。旧脚本直接用本轮 discovery 覆盖已有目标的整行，只保留本轮未查询的平台／架构，没有比较同一通道的版本。使用这组版本调用实际 `main()` 可稳定复现回退。

下载表按 **平台、架构、通道** 独立保留已归档的最高版本。本次已从上述 Git 历史恢复两个回退单元格，并确认 `.74` Release 已公开且含对应架构的附件。自动流程以 `chrome-state.json` 为跨执行的版本基线，由状态合并层按四段整数元组阻止回退，README 从成功发布后的 JSON 生成。不传 `--state` 的旧调用继续以 README 为基线，保留较高版本和本轮缺少的通道，拦下回退时记录 `readme_version_regression_ignored`。

Git 历史和本地重放能证明表格覆盖问题，不能单独证明 Google 当时返回旧版的具体原因。正常流程遇到低版本响应或 `noupdate` 时保留 JSON 中的已发布版本；上游主动撤回版本也不会自动降级，需要维护者明确调整状态文件。

证据与验证命令：

```bash
git diff 8772f11 5ceabdb -- README.md
gh api repos/ayanamist/chrome_installer/releases/tags/156.0.8078.74 \
  --jq '{tag: .tag_name, draft: .draft, assets: [.assets[].name]}'
python3 -m unittest discover -s tests -p test_update_readme.py -v
```

回归测试覆盖上述历史回退、连续新旧版本交替、数值比较（`.9 → .10`、`.100 → .99`、跨主版本）、通道和平台隔离，以及部分查询保留已有链接。未执行线上 Actions 或发布操作。

## 仓库中的更新状态（2026-10-28）

`chrome-state.json` 的 `schema_version` 为 `1`，`targets` 以 `平台/架构/通道` 为键；每项保存已发布的 `version` 和响应实际给出的 `cohort`、`cohorthint`、`cohortname`。同一平台的多个架构和通道相互隔离。仅允许这些公开字段；不写入 token、设备标识或凭据。初始 12 项版本从修正后的下载表迁移，并逐项核对了四个公开 Release 的来源记录及附件；cohort 初始缺省，不自行编造。

工作流的状态流转如下：

1. 归档任务检出当前分支最新状态，通过 `--state chrome-state.json` 读取版本和 cohort。Windows／macOS 将已发布版本填入 `app.version`，默认不请求同版本重装、不允许 rollback；首次没有版本时才用 `0.0.0.0`。Linux 仍查询 APT 清单，并采用同样的版本回退防护。
2. 合法响应实际出现的 cohort 字段更新到内存，缺失字段保留、空字符串可清空。`noupdate` 或异常低版本响应保留原版本，不下载、不压缩；新版本走原有完整原包校验和归档流程。归档任务只将候选 `updater_state` 写到 discovery，不修改持久 JSON。
3. 发布器先验证候选状态与 discovery 一致；全部归档发布、说明刷新成功后，按目标合并候选状态，拒绝较旧候选覆盖已有版本或 cohort。随后按已知版本核对公开 Release 的对应通道／平台／架构附件存在，才写回 JSON；失败或 `--preview` 不写回。这里只按 tag 核对状态文件引用的 Release，不扫描历史列表。
4. README 从更新后的 JSON 生成。主分支将 README 和 JSON 放在同一个自动提交中，以 `[skip ci]` 避免循环触发。即使版本未变，cohort 改变也会提交；相同内容不会产生提交。推送失败会让任务失败，下次仍从仓库中最后成功提交的状态重试。

2026-10-28 的只读验证：使用真实 CLI 和 JSON 中 Windows x64 Extended Stable `.74` 基线，得到保留 `.74` 的 discovery 及服务端 cohort 字段，原 JSON 未变；使用非零 `152.0.7977.169` 查询 Stable，Windows x64 返回 `.74` 的完整 EXE 清单，macOS x64 返回 `.49` 的 ZIP 清单。没有下载或重新封装这些大文件，也没有执行线上发布或自动提交；服务端灰度行为仍以每次实际响应为准。

验证命令：

```bash
python3 -m unittest discover -s tests -p test_updater_state.py -v
python3 -m unittest discover -s tests -p test_archive.py -v
python3 -m unittest discover -s tests -p test_channel_dedup.py -v
python3 -m unittest discover -s tests -p test_update_readme.py -v
python3 scripts/chrome_archive.py --platform windows --arch x64 --channel extended \
  --state chrome-state.json --query-only --output /tmp/chrome-state-query
```

本地需要延续同一更新状态时，给归档、发布、README 脚本都传入 `--state chrome-state.json`；不传时保留原来的独立查询方式。JSON 中已发布版本对应的 Release 或附件缺失会明确报错，不静默将状态当作成功。

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
