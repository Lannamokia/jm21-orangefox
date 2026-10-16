# v64 构建复现记录

本文件保留 v64 的历史验收证据。v65 的源码变化、构建比较及设备验证另见 `V65-STARTUP-PIN.md`。

2026-10-16，在同一 Linux 宿主上使用 debootstrap 新建 Ubuntu 22.04 minbase chroot。原构建根目录与新根目录完全分开；新源码从 manifest 固定的 Git 对象建立独立 checkout，没有复制原 out、ccache、编译对象或可执行产物。源码共 250 个项目。仅复用只读 Git 内容对象作为传输缓存；工作目录和编译输出独立。

新 chroot 安装的包清单见 `packages-repro.tsv`；原环境清单见 `packages-original.tsv`。编译运行于独立 mount/PID namespace，关闭 ccache，使用 8 个编译任务。chroot 与宿主共享 Linux kernel，这不是虚拟机。首轮全量 `recoveryimage` 编译成功，用时 11 分 15 秒；随后对齐构建参数并显式编译所需共享库。

## 固定项

- 上游提交见 `manifest.xml`，最终源码差异见 `patches.json`。
- Android 源码内路径 `/root/fox_12.1`；时区 UTC、locale C。
- `bootable/recovery` 分支名 `fox_12.1`，因为 OrangeFox 将其嵌入程序。
- `out/build_date.txt` 固定为 `1792111963`，不用当前构建时间作为版本输入。
- `TARGET_SCREEN_WIDTH` / `TARGET_SCREEN_HEIGHT` 保持未设置，与已验收版 Soong 配置一致；屏幕使用设备分辨率和运行时 framebuffer。
- `libbase` 必须使用 **android_recovery_arm64** 共享变体。平台共享变体与它的可执行字节一致但 ELF Build ID 不同，不能用于 v64 字节比较。
- Python 装配依赖为 fonttools 4.60.2、lz4 4.4.5；输入逐文件哈希见 `assembly-inputs.json` 与 `kernel-input.json`。

## 验证范围

`recovery` 和 `libbase.so` 是本次全新编译产物，组件比较见 `components.json`。最终镜像采用这些组件和固定 ramdisk / HAL 输入装配；不声称其他固定底包文件也由本次源码编译产生。

最终比较目标为已在 JM21 上冷启动、PIN CE 解锁及此前官方 OTA 验收的 v64。此次复现不重复刷设备；以最终镜像与已验收镜像逐字节一致作为验收条件。

结果：**PASS，逐字节一致**。最终镜像大小 **27,275,264 字节**，SHA-256：

```text
4ca6a5c88c087f73b89ab6cad7d6f6928a14ac553d3ea1c6b6049e9451847464
```

组件与最终装配比较均使用新 chroot 生成的文件，没有回填旧二进制、修改 ELF Build ID 或忽略差异。平台与 Recovery 变体差异通过选择正确源码构建目标解决。最终比较脚本返回 0。
