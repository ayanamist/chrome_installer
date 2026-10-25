# 普通 Chrome 与 Chrome for Testing 二进制实测比较

范围：2026-10-25，Windows、Linux、macOS 的 x64 Stable 样本。只读取压缩包、文件头和文件内容，没有启动 Chrome、installer 或 updater。Windows 双方版本均为 156.0.8078.48；Linux/macOS 双方均为 156.0.8078.47。ARM64、x86、Extended Stable 未做本轮比较。

普通版的 Windows 7z、Linux tar.xz 与已发布 Release 附件 SHA-256 完全一致；macOS 直接下载已发布的 tar.xz，另与相应官方 CRX3 的 .app 成员逐项交叉校验，完全一致。CfT ZIP 从 Google 官方 chrome-for-testing-public bucket 下载。Windows .48 未列入本次 per-version JSON（HTTP 404），但 ZIP 官方 URL 可下载，PE ProductVersion 实测为 156.0.8078.48、ProductName 为 Google Chrome for Testing。

比较方法：跳过目录，仅统计普通文件；符号链接另列。Windows 去掉 Chrome-bin 与版本目录，并按 Windows 路径语义忽略大小写；macOS 去掉外层目录和 .app 名称，将 Google Chrome for Testing 的已知品牌路径映射到 Google Chrome，保留双方实际路径。文件相等以完整 SHA-256 为准；权限差异单独记录，不用于判定内容不同。没有通过任意 basename 或哈希匹配来隐藏路径差异。

| 平台 | 版本 | 普通版文件 | CfT 文件 | 对应路径内容相同 | 对应路径内容不同 | 普通版独有路径文件 | CfT 独有路径文件 |
|---|---|---:|---:|---:|---:|---:|---:|
| windows | 156.0.8078.48 | 264 | 305 | 174 | 70 | 20 | 61 |
| linux | 156.0.8078.47 | 253 | 301 | 175 | 61 | 17 | 65 |
| macos | 156.0.8078.47 | 382 | 340 | 188 | 150 | 44 | 2 |

结论：主程序和核心库并非同一份二进制。Windows 的 chrome.exe、chrome.dll、chrome_elf.dll 的 PE .text，Linux chrome 的 ELF .text，以及 macOS 主程序和 Framework 的 x64 __TEXT,__text 均不同。代码段比较避开 Authenticode、Mach-O 签名和非代码资源，但不同代码字节本身不说明具体功能差异，也不排除链接布局等因素。

macOS 的普通官方 .app 是 Universal（x64 + ARM64），CfT mac-x64 是单架构 x64；下述文件数量包含该打包差异。为排除架构数量的影响，已额外比较普通版 Universal 中的 x64 代码段，仍与 CfT 不同。普通包里的 GoogleUpdater.app 在 CfT 中不存在。macOS 另有 1 个普通版独有符号链接和 1 个因品牌命名而目标字符串不同的符号链接，不计入普通文件统计。

Linux 的 chrome-sandbox 与 CfT chrome_sandbox 完整 SHA-256 相同，仅路径不同，因此保留在两边独有路径计数中；不能将它解释为缺少沙箱。macOS 两个 manifest 目录里的 Localizable.strings 也只是路径不同、内容相同。

本轮 Windows CfT ZIP 没有 WidevineCdm；Linux CfT 的 libwidevinecdm.so 和 macOS CfT 的 x64 libwidevinecdm.dylib 均存在，且与普通版相应文件完整 SHA-256 相同。未验证 DRM 运行行为。

## 核心代码段证据

| 对象 | 普通版代码段字节 | CfT 代码段字节 | 普通版 SHA-256 | CfT SHA-256 |
|---|---:|---:|---|---|
| windows/chrome.exe | 3480950 | 3549434 | 9b2ee0a8f3ccc101c5b427cca343adaf5616267ecb5b95afe0c867a5a76913a6 | c533f392030ddee6382c358c2b882d94622688d83bd8b88aa37ef8dd0ecc06cd |
| windows/chrome.dll | 262439254 | 262166582 | c6f2731ee7af4d7960b7607e1159cf3b9be7790324b1ca30fea683ab2b997e12 | a5e9fad6bae1ce5897e92d639be2939801a11557c7586967b2a49fc856f0879d |
| windows/chrome_elf.dll | 2294723 | 2290483 | 4c6f3152eebe9ae15ec593b4a5dd304a5aa624b64d39cae32937176321567b5e | 71416afb4771196d86fd22b9b32186e8e9bead4594613aeaee7af96f91cbdf41 |
| linux/chrome | 233397632 | 233459744 | 45f85d04f968eba21ee0a4c6ac38336d5daf3182cec5ce8318b31e90f6421f59 | 7e5196a9a81653530d5a07ba260fb55448f2ac57278dbd0d25ab04d8d119957d |
| macos/Google Chrome | 8317 | 8317 | 6036ec782855b83817f981dd2d0c6f869af997f6408902673c765dd6cb7ba58c | b0a5a65998f735bf3872a346e24bd6aa5ed90b0b87ed53d8e9062c15ec6fced9 |
| macos/Google Chrome Framework | 234893404 | 234741580 | 892b5f40028aa32b92ed58f968b96c05948296353534e4c78ab5bcdc2346de0a | ed4d514cf63e0ff262c887effc2b51b12aaed47e492880dbd4d205fa2652c15e |

## 来源与验证边界

- windows：普通版 [Release](https://github.com/ayanamist/chrome_installer/releases/tag/156.0.8078.48)；[CfT ZIP](https://storage.googleapis.com/chrome-for-testing-public/156.0.8078.48/win64/chrome-win64.zip)。
- linux：普通版 [Release](https://github.com/ayanamist/chrome_installer/releases/tag/156.0.8078.47)；[CfT ZIP](https://storage.googleapis.com/chrome-for-testing-public/156.0.8078.47/linux64/chrome-linux64.zip)。
- macos：普通版 [Release](https://github.com/ayanamist/chrome_installer/releases/tag/156.0.8078.47)；[CfT ZIP](https://storage.googleapis.com/chrome-for-testing-public/156.0.8078.47/mac-x64/chrome-mac-x64.zip)。

每份输入先记录完整压缩包 SHA-256，再读取所有成员并按上述路径规则逐文件计算 SHA-256。PE 从节表定位 .text；ELF64 从 section headers/string table 定位 .text；Mach-O 从 fat header 选择 CPU_TYPE_X86_64，再从 LC_SEGMENT_64 定位 __TEXT,__text。没有执行反汇编或功能测试，因此不把哈希差异直接归因于某个具体功能。样本结论不外推到所有版本和架构。

官方背景：[CfT 设计说明](https://developer.chrome.com/docs/automation-and-testing/chrome-for-testing)、[发布 API 与平台列表](https://github.com/GoogleChromeLabs/chrome-for-testing)。

Windows 进一步验证：以下文件的完整 SHA-256 不同，但 PE .text 代码段一致：`chrome_proxy.exe`, `chrome_pwa_launcher.exe`, `chrome_wer.dll`, `dxcompiler.dll`, `vk_swiftshader.dll`, `vulkan-1.dll`。这说明整个文件不同不能直接推断代码段不同；本次只对已明确列出的核心程序确认代码段差异。
