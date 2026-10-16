# v65：启动时保留 PIN 解密页面

症状是进入 Recovery 后直接到主界面，手动进入用户解密页仍能正常解锁。日志确认启动时已经打开 `decrypt_pin`，随后外置 SD 自动挂载助手发送 `twrp unmount /external_sd`。CLI 的 `singleaction_page` 完成时设置全局 `tw_page_done=1`，导致启动解密对话框提前退出。

Recovery 在所有启动对话框结束后设置 `of.ui.ready=1`。外置 SD 助手等待这个信号，再发送卸载、挂载和 MTP 刷新命令。等待不设置超时：用户可以停留在密码页，启动提示不会因为等得久而被关闭。原有解密和跳过逻辑保留。装配脚本显式替换固定底包中的助手脚本，确保设备树修改进入最终镜像。

## 构建比较

2026-10-16，分别在原 Ubuntu 22.04 chroot 和 v64 复现所用的独立 chroot 中增量编译本次修改；不是重新创建第三个空 chroot 全量构建。两个环境的 `recovery` 及最终装配镜像均使用 `cmp` 比较通过，`libbase.so` 与 v64 一致。

| 产物 | SHA-256 |
| --- | --- |
| recovery | `27eee698d2ddafc18710c82fa9948a8d2e64ed5b1347a3fdf91b2ce8f3a94012` |
| libbase.so | `62f1c62553bf148496ca1688b5bd6ad36bc27bdbdc5d3b8aae121b7f7250f183` |
| OrangeFox-JM21-v65-startup-pin.img | `56cfe1c935d70899b2aef4ed658de9b3b338c8f154d38057d2df9631c0f6d36f` |

镜像大小为 27,275,264 字节。构建参数及固定输入沿用 v64。比较脚本默认校验 v65，`--reference v64` 保留旧版校验。

## 设备验证

- 刷入当前 recovery_a，通过 bootloader 启动 Recovery。
- 启动自动进入 PIN 页；等待超过 45 秒后截图确认页面仍在，此时 SD 分区节点已存在，`of.ui.ready` 尚未置位，未执行外置 SD CLI 命令。
- 本轮通过 Recovery CLI 输入正确凭据，user 0 CE 解锁成功。启动流程返回后 `of.ui.ready=1`，外置 SD 自动挂载为 exfat，并执行 MTP 刷新。
- 首次启动日志亦记录了 `trydecrypt`、user 0 解锁以及随后执行的 SD 命令。

本轮未重新验收官方 OTA，也未独立完成跳过按钮的设备测试。一次 Recovery 直接热重启曾在 metadata 解密前的 keystore 初始化处停住，经过 bootloader 再进入 Recovery 恢复正常；该启动现象尚未定位，本补丁只处理已确认的 PIN 对话框与 SD CLI 竞争。

原始日志可能包含解锁命令参数，不随发布包提供。
