# 补丁索引

所有补丁均为 `git diff --binary HEAD` 导出的最终状态，按 `patches.json` 的顺序使用。历史 patch9～patch18 的中间诊断版本不再进入构建。

| 源码项目 | 内容 |
| --- | --- |
| bootable/recovery | metadata block-device 回退、MTP 刷新与去重、亮度和屏幕唤醒、主题调整与只保留中英文 |
| build/make | OrangeFox 镜像构建接入与 recovery 打包调整 |
| system/core | liblp 根据实际 super 大小扩展记录的 block device 容量，含回归测试 |
| system/libbase | recovery 无 logd 时将日志同时输出到 stderr |
| system/update_engine | recovery 更新时修正 super 元数据，以及已有 OrangeFox 的可选校验兼容开关 |
| system/vold | metadata mount 接管、fscrypt session keyring、无密码 CE 回退、keystore2 数据库重新打开、同步 Gatekeeper verify 与 HAT 验证 |
| vendor/twrp | OrangeFox Soong 配置接入与 haptics 配置兼容 |

`device/fiio/jm21` 是用于编译组件的设备配置。最终运行时 fstab 是 `port/twrp.fstab.new`。最终服务、属性和 HAL 装配逻辑在 `work/_cmp/cpio_add_hal.py`；它明确添加 persist 挂载和延迟启动脚本。boot HAL 使用的 `/etc/recovery.fstab` 仅含 misc，不能用完整 TWRP fstab 覆盖。

没有引入临时 Gatekeeper 探针、凭据派生值转储或 enrollment 操作。上游 Recovery CLI 本身会把命令参数记入日志，因此不要公开包含解锁命令的原始 recovery.log。
