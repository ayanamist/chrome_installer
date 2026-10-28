# Chrome installer archive

每天从 Google 官方下载 Windows、macOS、Linux 的 Chrome，解包后重新封装为免安装的浏览器归档，按版本发布到 [GitHub Releases](https://github.com/ayanamist/chrome_installer/releases)。覆盖 Windows（x86、x64、ARM64）、macOS（x64、ARM64）、Linux（x64、ARM64）的 Stable 和 Extended Stable（Linux 官方不提供 Extended Stable）。

## 下载

下表由 GitHub Actions 自动更新，列出各平台、架构、通道最近一次查询到的版本，点击版本号进入对应 Release 下载。各平台版本可能因 Google 发布进度或灰度而不同。

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

## 更新与校验

每天 11:31 UTC 自动检查并发布新版本，也可在 Actions 页手动触发。所有下载均按 Google 官方清单核对大小和 SHA-256，并校验版本、可执行文件架构及重新封装后的归档完整性。整个流程不运行 Chrome、updater 或安装器，也不需要任何个人令牌。

## Release 与附件

- tag 为纯四段版本号，如 `156.0.8078.48`；Release 名称形如 `156.0.8078.48 · Stable`。同一版本同时出现在两个通道时共用一个 Release（`版本号 · Stable / Extended Stable`）。
- Release 描述列出各通道、平台、架构对应的 Google 官方原包链接（Windows EXE、macOS ZIP、Linux DEB 及同版本 RPM）。
- 附件名不含通道，内容相同的通道共享同一份附件；已发布的附件不会被覆盖、删除或重命名。

| 平台 | 附件示例 | 解压后目录 |
|---|---|---|
| Windows | `chrome-windows-x86-156.0.8078.48-browser.7z` | `Chrome-bin` |
| macOS | `chrome-macos-arm64-156.0.8078.47-browser.tar.xz` | `Google Chrome.app/` |
| Linux | `chrome-linux-x64-156.0.8078.47-browser.tar.xz` | `chrome` |

归档保留文件内容、可执行权限和符号链接，不重签名。Linux 的依赖库和系统集成需由系统自行提供。

需要官方原始安装包（Windows `*_chrome_installer_uncompressed.exe`、macOS updater ZIP、Linux DEB／RPM）时，请使用 Release 描述中的 Google 官方链接，原包不作为附件上传。

## 解压使用时恢复 DM token

适用于已通过 Chrome 企业云管理注册、但持久 DM token 丢失（表现为策略安装的扩展每次启动被卸载再安装）的场景；普通使用无需运行。脚本从最近的本机机器级云策略缓存恢复 token，作为独立工具放在 [scripts/chrome-dm-token/](scripts/chrome-dm-token/README.md)，不打入浏览器归档。

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

恢复后正常启动 Chrome，检查策略和扩展是否跨重启保留。脚本保留已有 token，只接受匹配本机、active 且 24 小时内的缓存，不会注册新设备。macOS／Linux 通常会自行保存 token，仅缺失时需要此脚本。详细说明见 [脚本使用说明](scripts/chrome-dm-token/README.md)。

## 维护者文档

自动化流程、发布规则、本地运行和日志说明见 [开发与运维手册](docs/development.md)；协议、验证边界与证据来源见 [docs/](docs/README.md)。
