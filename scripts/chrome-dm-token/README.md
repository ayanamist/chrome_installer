# 解压版 Chrome：恢复已有 DM token

适用范围：已通过 Chrome 企业云管理（CBCM）注册、已有本机机器级云策略缓存，但持久 DM token 缺失的普通 Google Chrome。Windows、macOS、Linux 的 Stable / Extended Stable 共用此逻辑（Linux 没有 Extended Stable）。没有相关云策略的普通浏览器无需运行。

缺少 DM token 时，Chrome 启动会跳过机器级云策略缓存。策略安装的扩展可能先被卸载，再随在线策略恢复而重装，造成扩展设置丢失。这些脚本复用本机缓存中的注册 token，供下一次启动读取；它们不会创建 enrollment token、联网注册或启动 Chrome。

## 使用步骤

1. 在目标 Chrome 的 `chrome://version` 核对 Profile Path。下面参数应指向它的父目录（User Data 根目录），不能指向 `Default` 或 `Profile 1`。
2. 在 `chrome://policy` 重新加载策略，等待成功，保证缓存为最近 24 小时内的本机缓存。首次注册尚未完成或策略未成功获取时，不能运行恢复。
3. 正常完全退出 Chrome，包括后台进程，然后在仓库根目录执行对应脚本；单独下载脚本时改用实际保存路径。
4. 正常启动 Chrome，确认 `chrome://policy` 的机器级云策略立即生效，并连续重启确认扩展及其设置保留。

Windows：使用系统自带的 Windows PowerShell 5.1 或更高版本，以管理员身份打开 PowerShell。默认读取当前用户的 `%LOCALAPPDATA%\Google\Chrome\User Data`。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\chrome-dm-token\fix-dm-token.ps1
```

自定义路径（如果使用另一个管理员账户提升权限，也必须显式指定原 Chrome 用户的目录）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\chrome-dm-token\fix-dm-token.ps1 -UserDataDir 'D:\Chrome User Data'
```

macOS / Linux：以运行 Chrome 的同一用户执行，**不要 sudo**。Bash 兼容 macOS 自带的 3.2；需要系统命令 `openssl`、`od`、`head`、`tr`、`date`、`cmp`、`mkdir`、`cat`、`uname`，macOS 另需 `ioreg`、`sed`。不依赖 Python、Go 或 protobuf 包；缺少命令时会报错退出，不自动安装。

```bash
bash ./scripts/chrome-dm-token/fix-dm-token.sh
# 自定义 --user-data-dir 的 Chrome：
bash ./scripts/chrome-dm-token/fix-dm-token.sh --user-data-dir '/path/to/Chrome User Data'
```

macOS 默认缓存目录为 `~/Library/Application Support/Google/Chrome`；Linux 默认按 `CHROME_USER_DATA_DIR` 或 `CHROME_CONFIG_HOME` / `XDG_CONFIG_HOME` / `~/.config` 下的 `google-chrome` 定位。Chrome 使用命令行 `--user-data-dir` 或其他目录时，显式传入相同路径。macOS 的 token 存储始终属于当前登录用户，与传入的缓存目录无关。

## 写入范围和保护

| 平台 | 本机身份 | token 持久化位置 |
|---|---|---|
| Windows | 64 位注册表 MachineGuid | `HKLM\SOFTWARE\Google\Chrome\Enrollment`，64 位视图的 `REG_BINARY dmtoken` |
| Linux | `/etc/machine-id` 去除尾部 ASCII 空白后 SHA-1，再 base64url，无 `=` | `<User Data>/Policy/Enrollment/<client ID>` |
| macOS | `IOPlatformSerialNumber` | `~/Library/Application Support/Google/Chrome Cloud Enrollment/<base64url(SHA-1(serial))>`，无 `=` |

脚本会检查 protobuf 结构、唯一关键字段、策略类型、本机身份、active 状态、时间（24 小时内、最多允许未来 5 分钟）及 token 格式。缓存读取限制为 16 MiB。只有全部检查通过才写入，不显示 token。Bash 新文件权限为 `0600`，不添加换行，并回读验证。

任一预期位置已有 token 时直接跳过，包括空值、不同类型或符号链接；不会覆盖或删除已有 token。Windows 还优先检查 32 位视图 `HKLM\SOFTWARE\Google\Enrollment` 的 `dmtoken`。Bash 写入使用 noclobber 独占创建，防止检查后出现的文件被覆盖。已有但损坏的 token 需要单独诊断，这些脚本不会自动替换。

## 边界

- Windows 解压运行可能缺少 updater/setup.exe 的 token 保存链路。macOS / Linux 的 Chrome 自己直接保存 token，通常不需要此修复；这里提供的是持久化文件缺失时的恢复工具。
- token 属于设备／用户存储，不是随浏览器程序一起携带的便携配置。不能跨机器复制缓存或 token。
- 只读取本机受信任缓存；脚本不验证缓存密码学签名，也不证明服务器仍认可注册。过期缓存应先在 Chrome 中重新加载策略。管理员撤销或重新纳管后的旧 token 不应恢复。
- 不注册 updater、不提供未来 token 自动保存的服务，不应加入每次启动的自动修复任务。已被卸载而丢失的扩展设置不会被脚本还原。
- 恢复脚本仅在仓库 `scripts/chrome-dm-token/` 独立提供，不加入浏览器归档；需要时单独获取并手动执行。

实现依据、来源、测试命令和实测边界见仓库的 [DM token 恢复约定](https://github.com/ayanamist/chrome_installer/blob/main/docs/dm-token.md)。
