# 文档索引

- [开发与运维手册](development.md)：维护者用的自动执行、Tag／Release／附件规则、阶段日志和本地运行命令；面向最终用户的说明在根目录 README。关键词：GitHub Actions、发布、discovery、本地归档、日志。

- [Stable 与 Extended Stable 的内容重合](protocol.md#stable-与-extended-stable-的内容重合2026-10-25)：2026-10-25 核对 Windows 156.0.8078.48 重复来源与附件 digest，记录通道发现、原包／归档哈希去重、无通道附件名、隐藏来源映射及历史兼容合同。关键词：Stable、Extended Stable、去重、SHA-256、release_channel、browser_asset、metadata-v1。

- [协议及归档约定](protocol.md)：三平台、Windows x86／x64／ARM64 及 macOS／Linux x64／ARM64 官方 payload 的下载、校验、7z／tar.xz 封装和版本 Release 合并及当前可获取版本的描述刷新规则。关键词：Omaha、release_channel、原生下载、uncompressed EXE、updater ZIP、旧 CRX3 来源兼容、DEB、RPM、Stable、Extended Stable、SHA256、7-Zip、GitHub Actions。范围及日期见正文；实现基于 Chromium 157.0.8089.0 协议，使用实时官方响应确定版本。

- [普通 Chrome 与 CfT 二进制比较](cft-comparison.md)：2026-10-25 同版本三平台 x64 样本的文件 SHA-256 与 PE／ELF／Mach-O 代码段比较；记录 Universal、Widevine、Updater 与资源文件差异及验证边界。关键词：Chrome for Testing、CfT、ZIP、二进制、代码段、Universal、Widevine。

- [DM token 恢复约定](dm-token.md)：2026-10-25 Windows PowerShell 与 macOS／Linux Bash 独立恢复脚本，依据 Chromium 157.0.8089.0 的存储规则；记录设备标识、缓存校验、写入范围、独立分发及测试边界，不加入浏览器归档。关键词：CBCM、DM token、机器级云策略、扩展重装、解压版、PowerShell、Bash、Enrollment、MachineGuid、machine-id、IOPlatformSerialNumber。
