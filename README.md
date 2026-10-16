# OrangeFox for FiiO JM21

JM21 专用设备树、固定源码清单、合并补丁和镜像装配工具。目标为 SM6225 / bengal_515、Android 13、独立 A/B recovery 分区。

## 构建边界

这是 **源码编译 + 固定底包装配** 的工程。目前重新编译的组件是 OrangeFox `recovery` 和 `libbase.so`；其余 ramdisk 用户空间、主题、OTA 钩子及厂商 HAL 由带 SHA-256 清单的固定输入包提供。它不是从源码重新生成 ramdisk 每一个文件的纯源码移植。设备树内的 kernel 为原厂预编译 GKI；最终 recovery 镜像不携带 kernel。

`manifest.xml` 固定所有源码提交，额外包含 OrangeFox `vendor/recovery`，并把 `bootable/recovery` 指向实际使用的 OrangeFox 上游。`patches.json` 记录每组补丁的基准提交；补丁来自实际通过设备验收的源码差异，包含 OrangeFox 构建接入和 JM21 修复。

## 设备能力与关键修复

- FBEv2、metadata encryption、`emmc_optimized+wrappedkey_v0`，无密码及 PIN 下的 CE 解锁已在设备实测。
- Gatekeeper 使用同步 HIDL verify，校验 HAT 后交给 keystore2；不修改 enrollment 或凭据。
- 可写 `/persist` 绑定至 `/mnt/vendor/persist`，满足 QTI Gatekeeper TA 的 GP FS 需求。未挂载时 TA 返回 -1000，只读时返回 -49。
- QSEE/Gatekeeper 等 `/data` 挂载后启动，避免固定住临时原厂 vendor 挂载；补齐 passthrough 实现及 HIDL memory 依赖。
- MTP、亮度、屏幕唤醒、中文字体裁剪，以及官方完整 OTA、动态 super 元数据和 recovery 保留钩子。

Recovery 使用 permissive SELinux；这些配置用于已解锁设备的维护环境。不要把 persist 镜像作为通用刷机材料；每台设备使用自己的 persist。

## 从固定源码构建

在 Ubuntu 22.04 上安装 `scripts/install-deps.sh` 中的依赖。建议全新 chroot、至少 16 GB RAM 和 100 GB 可用空间；8 核可使用 `JOBS=8`，关闭 ccache。

Release 提供可直接解包的 `jm21-build-kit-v64.tar.gz`，包含本仓库源码、装配输入和 kernel；也可分别下载源码、`jm21-assembly-inputs.tar.gz` 和 `jm21-kernel-input.tar.gz`。先用同一 release 的 `SHA256SUMS` 校验下载文件。完整源码工程仍需按固定 manifest 获取。

```bash
# 在空目录初始化源码；将本仓库放到另一个目录，记为 $JM21。
repo init -u https://github.com/Lannamokia/jm21-orangefox.git -b main -m manifest.xml
repo sync -c -j4

# 解开 release 的固定输入包和 kernel 输入包到本仓库根目录。
tar -xf jm21-assembly-inputs.tar.gz -C "$JM21"
tar -xf jm21-kernel-input.tar.gz -C "$JM21"
python3 -m pip install -r "$JM21/requirements.txt"
python3 "$JM21/scripts/apply.py" "$PWD"
bash "$JM21/scripts/build.sh" "$PWD"
python3 "$JM21/scripts/assemble.py" \
  "$PWD/out/target/product/jm21/recovery/root" ./OrangeFox-JM21.img
python3 "$JM21/scripts/compare.py" ./OrangeFox-JM21.img
```

不要直接刷源码树生成的 recovery.img：最终发布镜像必须经过装配步骤。字节级 cpio 编辑保留原始权限、顺序和固定时间戳，避免在 Windows 解包再打包丢失执行位。`work/_cmp` 是装配工具保留的工作路径，名字沿用最初的 v28/v31/v32 工序，发布版本以最终产物校验为准。

从本地 Git 对象缓存建立独立干净源码，可使用 `scripts/checkout-cache.py CACHE_SOURCE EMPTY_DEST`。随后在有 debootstrap、rsync 的 Linux 宿主上执行 `scripts/new-chroot.sh NEW_ROOT CLEAN_SOURCE EXTRACTED_BUNDLE`，会创建全新根目录，使用独立挂载/PID namespace，并执行编译、装配和比较。不要让缓存源码与新源码目录互相嵌套。

## 源码和素材

源码补丁保留对应上游许可证；设备 fstab / init 文件保留原始版权声明。固定输入包含 Android / OrangeFox 编译文件、字体和 FiiO / Qualcomm 厂商文件，权利归各自所有者；本仓库不对这些文件重新授权。`assembly-inputs.json` 给出每一项装配输入的大小与 SHA-256。

不包含设备 persist、misc、用户数据、锁屏材料、私有 SSH 配置或调试日志。构建验证细节和哈希见 `docs/REPRODUCIBILITY.md`。
