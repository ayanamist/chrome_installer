# 解压版 Chrome 的 DM token 恢复约定

范围及日期：2026-10-25，普通 Google Chrome 的 Windows x86／x64／ARM64、macOS x64／ARM64、Linux x64／ARM64 解压使用场景。脚本接口与缓存字段依据 Chromium `157.0.8089.0`。实现包括独立的单文件恢复脚本及缓存解析测试，不接入浏览器归档。

关键词：CBCM、DM token、机器级云策略、扩展重装、解压版、PowerShell、Bash、Enrollment、MachineGuid、machine-id、IOPlatformSerialNumber。

## 原因与适用条件

缺少持久 DM token 时，机器级云策略存储的 `LoadImmediately` 不加载本地缓存；重新在线注册、获取策略前，云策略扩展可能失去安装来源并被作为 orphaned external extension 清理，之后再由恢复的策略安装。应区分注册用的 enrollment token 与注册成功后用于获取策略的 DM token，脚本恢复的是后者。

Windows 的保存任务通过 updater app command 调用 installer。纯解压运行可能缺少这条链路，因此需要补写已有注册状态。macOS／Linux 的实现直接写用户文件，正常情况下能自行保存；脚本只处理文件缺失，不能把 Windows 的失败原因直接外推到它们。

来源：

- [157.0.8089.0 机器级策略缓存加载](https://raw.githubusercontent.com/chromium/chromium/157.0.8089.0/components/policy/core/common/cloud/machine_level_user_cloud_policy_store.cc)。
- [149.0.7827.201 外部扩展清理](https://raw.githubusercontent.com/chromium/chromium/149.0.7827.201/chrome/browser/extensions/external_provider_manager.cc)。
- [157.0.8089.0 Windows token 读取和保存任务](https://raw.githubusercontent.com/chromium/chromium/157.0.8089.0/chrome/browser/policy/browser_dm_token_storage_win.cc)。
- [注册和撤销管理说明](https://support.google.com/chrome/a/answer/9301891?hl=en)。

## 脚本和路径合同

入口为 [`fix-dm-token.ps1`](../scripts/chrome-dm-token/fix-dm-token.ps1) 和 [`fix-dm-token.sh`](../scripts/chrome-dm-token/fix-dm-token.sh)。Windows 提供单一恢复操作，参数 `-UserDataDir`；Bash 参数 `--user-data-dir`。Bash 使用系统 OpenSSL 计算 Chromium 兼容的 SHA-1 和 base64url，不依赖 Go、Python 或 protobuf 包。完整操作步骤及依赖见 [脚本使用说明](../scripts/chrome-dm-token/README.md)。

| 平台 | client ID | 读取及补写位置 |
|---|---|---|
| Windows | HKLM 64 位视图 `SOFTWARE\Microsoft\Cryptography` 的 MachineGuid | 先检查中立位置 `HKLM\SOFTWARE\Google\Enrollment` 的 32 位视图，再检查 `HKLM\SOFTWARE\Google\Chrome\Enrollment` 的 64 位视图；仅缺失时在后者补写 `REG_BINARY dmtoken` |
| Linux | `/etc/machine-id` 去除尾部 ASCII 空白后必须为 32 字节；`base64url(SHA-1(machine-id))`，去掉 padding | `<User Data>/Policy/Enrollment/<client ID>` |
| macOS | `IOPlatformSerialNumber`，原始序列号与缓存 device ID 比较 | `~/Library/Application Support/Google/Chrome Cloud Enrollment/<base64url(SHA-1(client ID))>`，去掉 padding |

Windows 默认缓存根目录为当前用户 `%LOCALAPPDATA%\Google\Chrome\User Data`。macOS 默认 `~/Library/Application Support/Google/Chrome`，token 则属于当前用户 Application Support 的单独目录。Linux 默认优先使用 `CHROME_USER_DATA_DIR`，否则在 `CHROME_CONFIG_HOME`／`XDG_CONFIG_HOME`／`~/.config` 下使用 `google-chrome`。命令行自定义 User Data 时需要显式传参，不能传 `Default` 等 profile 子目录。Bash 应以 Chrome 的同一用户运行，不应 sudo；Windows 需要管理员写 HKLM，如果使用另一个管理员账户，应显式传入原用户的缓存目录。

来源：

- [Linux client ID 和 token 文件](https://raw.githubusercontent.com/chromium/chromium/157.0.8089.0/chrome/browser/policy/browser_dm_token_storage_linux.cc)。
- [macOS client ID 和 token 文件](https://raw.githubusercontent.com/chromium/chromium/157.0.8089.0/chrome/browser/policy/browser_dm_token_storage_mac.mm)。
- [macOS DIR_APP_DATA 的用户 Application Support 定义](https://raw.githubusercontent.com/chromium/chromium/157.0.8089.0/base/base_paths_mac.mm)。
- [macOS 平台序列号读取](https://raw.githubusercontent.com/chromium/chromium/157.0.8089.0/base/mac/mac_util.mm)。
- [User Data 默认目录及环境变量](https://chromium.googlesource.com/chromium/src/+/refs/tags/157.0.8089.0/docs/user_data_dir.md)。

## 缓存验证和写入约束

读取 `<User Data>/Policy/Machine Level User Cloud Policy`，限制为 16 MiB。外层 protobuf `PolicyFetchResponse` field 3 为 `PolicyData`；内层使用 field 1 策略类型、2 毫秒时间戳、3 request_token、8 device_id、9 state。state 未设置时默认为 active（0）。关键字段必须唯一且 wire type 正确；拒绝截断、异常长度、溢出 varint、不支持的 wire type。

必须满足：策略类型为 `google/chrome/machine-level-user`、device ID 精确匹配本机、state 为 active、时间不早于当前 24 小时且不晚于未来 5 分钟、token 长度 1～4096 字节且仅含可打印的非空白 ASCII，不接受 `INVALID_DM_TOKEN`。Bash 解析器与 PowerShell 一样对 varint 使用非负有符号 64 位范围，范围外值明确拒绝。

任一读取位置已有值时直接退出，包含空值、不同类型及 Bash 文件路径上的符号链接，不覆盖、不删除、不替换。缓存检查后再检查目标。Bash 使用 noclobber 独占创建和 `0600` 权限，Windows 写 REG_BINARY，均回读验证；Bash 不向 token 文件添加换行。脚本不输出 token，不把 token 放入外部命令参数，不建立含 token 的原状态记录。Bash 写入失败可能留下仅部分写入的文件，应诊断后处理，不自动覆盖重试。

本机缓存是信任前提：脚本不自行验证缓存的密码学签名，不证明服务器仍认可注册，也不恢复扩展已丢失的设置。Chrome 完全退出后执行一次，再正常启动验证；不能把脚本设为每次启动的修复任务，以免重新引入管理员已撤销的状态。未来 token 的自动持久化服务不在此实现范围内。

字段依据：[device_management_backend.proto](https://raw.githubusercontent.com/chromium/chromium/157.0.8089.0/components/policy/proto/device_management_backend.proto)。

## 独立分发

根据 2026-10-25 确认的范围，恢复工具仅在仓库 `scripts/chrome-dm-token/` 独立提供，Windows 使用 PowerShell，macOS／Linux 使用同一份 Bash。使用者按需单独获取并执行，浏览器归档只包含提取的浏览器目录。

归档流程不复制或执行恢复脚本，不读取构建机的 Chrome 注册状态。Windows 7z 输入仅为 `Chrome-bin/`，macOS tar.xz 输入仅为 `Google Chrome.app/`，Linux tar.xz 输入仅为 `chrome/`；不增加辅助脚本目录。

## 验证和边界

2026-10-25 本地验证：

- Python 归档测试检查 Windows 压缩只传入浏览器目录、macOS tar.xz 实际回读只包含 `.app` 及其原有内容，即使旁边存在恢复工具目录也不打入。
- Linux 使用真实脚本入口、本机 ID 和临时 User Data 写入合成 token；缓存解析及落盘为真实执行，未读写实际 Chrome profile。
- Bash 分别模拟 Linux machine-id 和 macOS 序列号，检查实际哈希／protobuf 解析、默认路径、自定义目录、权限、重复执行保留原文件，以及错误设备、状态、时间、token、截断、重复字段和溢出的拒绝行为。macOS 分支本地是在 Linux 上执行的模拟，不能当作真实 macOS 运行证明。
- 使用已安装的 Windows PowerShell 5.1 执行合成 protobuf 解析测试，通过；只 dot-source 函数，不执行真实注册表写入。
- Bash 通过 `bash -n` 与 ShellCheck。测试使用 `/bin/bash`，可在 macOS 上检查系统 Bash 兼容性；本次没有真实 macOS 运行结果，也没有触发远程 CI。

复核命令：

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/*.py tests/test_dm_token.py
bash -n scripts/chrome-dm-token/fix-dm-token.sh
shellcheck scripts/chrome-dm-token/fix-dm-token.sh
```

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\tests\fix-dm-token.Tests.ps1
```

本次未重启用户真实 Chrome、删除／补写生产 token、重新注册设备或验证真实扩展跨启动行为。未在本地真实 macOS 运行，也未执行 Windows 实际注册表写入测试，因此不宣称三平台已完成真实浏览器端到端验证。
