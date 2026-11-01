# sheng 移植的 macOS 构建流水线

本仓库收纳 UniXNU `sheng`（小米平板 6 Pro / SM8550）移植中**只能在 macOS 上完成**的构建步骤。
Linux 侧无法产出这些输入，原因有两条且都是硬约束：

1. `build-owner-os-policy.py` 依赖 `hdiutil`、`diskutil`、`/dev/rdiskN`，并且必须针对真实的只读 UDIF 镜像运行；
2. verified-root 探针需要用 iOS SDK 交叉编译。

运行环境固定为 `macos-26`（实测 macOS 26.6.2 / Xcode 26.6 / iPhoneOS 26.5 SDK），所有输入均来自公开源：
`github.com/UniXNU/UniXNU`、`github.com/UniXNU/UniXNU-sheng`，以及公开的 iPad7,1 17.7.11 (21H461) IPSW。

---

## 1. 流水线一览

| workflow | 输入 | 产物（artifact） | 现状 |
|---|---|---|---|
| `probe-runner.yml` | 无 | 无（仅打印环境事实） | ✅ 可用 |
| `verified-root.yml` | `config`（默认 `sheng_seed_defconfig`） | `verified-root-inputs`、`kernel-log` | ✅ 可用，已产出可信包 |
| `turnip-ios.yml` | 无 | `turnip-ios`（`build/mesa-tu-ios`、`build/adreno-metal-plugin`） | ✅ 可用（checker 用例不稳定，走直编回退） |
| `data-seed.yml` | `ipsw`（IPSW 直链，必填）、`run_id_note` | `data-seed`（`owner-data-32.cdr` + 报告 JSON） | ✅ 可用 |
| `target-volumes.yml` | `globs`（默认空串） | `target-volumes`（`harvest/`） | ⚠️ 仅取证用，已证明卷上没有 trust cache |
| `hvf-capture.yml` | `asset_url`（可选） | 无 | ❌ 结论性死路：runner 无嵌套虚拟化 |

---

## 2. 固定输入与 pin

改动以下任何一项都会让下游审计判定输入不符，必须同步更新仓库里的钉值。

**镜像（按 sha256 钉住）**

| 成员 | 文件 | sha256 | 大小 |
|---|---|---|---|
| OS | `044-76040-038.dmg` | `3f99b859c9728379110b66657c7b15285bcb1925ad8b5726542e2cdcf8b1028d` | 1483785 块 |
| SystemOS | `044-76034-038.dmg` | `b4908cde41d23e6bc05e6af51a2c9eeb00cf0f863a6a18b1600c96a3fd70414f` | 806400 块 |

IPSW 直链（6835124840 B）：
`https://updates.cdn-apple.com/2025SpringFCS/fullrestores/122-77166/DCA6CFE8-90E9-4B92-880D-3DE171798E40/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw`

**源码与工具链**

| 项 | 钉值 |
|---|---|
| `UniXNU` 检出 | `f0721e9745c45ad2600cd69c74730f1cca55d56a`（必须 40 位全 sha + `fetch-depth: 0`） |
| Mesa | `85fda3b36405bcaea8bffc64aa4f2651dc90770d` + 9 个 `patches/mesa/*.patch` |
| metal2vulkan 翻译层 | `720ef1ce9ae2a2068999a8675717418b9416283e` |
| Rust | `1.98.0` + `aarch64-apple-ios`、`aarch64-apple-darwin` |
| 系统工具 | `brew`：meson、ninja、glslang、shaderc、bison、flex、vulkan-headers、llvm、openssl@3 |

> 用短 sha 会被 `actions/checkout` 当作分支名而失败（`A branch or tag with the name … could not be found`）；
> 用上游 HEAD 则会带来新的 Mesa 补丁序列，触发 `Recorded Mesa patch sequence differs`。

---

## 3. 各 workflow 详解

### 3.1 `probe-runner.yml` — 环境体检

无输入、无产物，只确认 runner 具备后续任务所需的四类事实：`sw_vers`／`uname -m`、`xcodebuild -version`、
`xcrun --sdk iphoneos --show-sdk-version`、IPSW 直链可达性与磁盘余量。新 runner 镜像上线时先跑它。

### 3.2 `verified-root.yml` — 两份 macOS 专属策略 + 探针归档

产出三样 Linux 侧永远拿不到的东西：

```
build/verified-root-os/           原始 OS 成员已签名的策略
build/verified-root-systemos/     原始 SystemOS 成员已签名的策略
build/verified-root-os-xnu-bundle/libVerifiedRootProbe.a   内核侧探针归档
```

流程：检出两个仓库 → `defconfig`/`fetch` → 用 `config` 指定的 defconfig 编出内核编译记录 →
`actions/cache` 复用已解压的镜像成员 → 解出两个 dmg → 编译 host provider 并生成 RSA-3072 owner 密钥对 →
分别对 OS / SystemOS 签名并校验 → 打 `libVerifiedRootProbe.a` → 收集上传。

* artifact `verified-root-inputs`（保留 14 天）：`out/verified-root/` 整棵树（含两个策略目录、探针归档、host 密钥）；
* artifact `kernel-log`（保留 7 天）：`${{ runner.temp }}/kernel.log`。

`env.GITHUB_TOKEN` 必须存在：`prepare-xnu-source.py` 只对 `api.github.com` 发请求，
共享 runner IP 上匿名访问会撞 403 限流，恢复 DriverKit 头文件时报 `HTTP Error 403: rate limit exceeded`。

### 3.3 `turnip-ios.yml` — Turnip 交叉编译 + Adreno Metal 插件

顺序不能调换：先 `--target host` 再 `--target ios`，因为 `scripts/gen-turnip-replay.py` 两个都要。
中间步骤固定三件事：`.cache/mesa-venv` 装 meson/ninja/mako/pyyaml/packaging（脚本硬编码该路径）；
`.cache/mesa-tools/bin` 放 meson/ninja 链接；`bison`/`flex` 必须用 brew 版本（Xcode 自带 2.3 不认 `-W`）。

制品：`build/mesa-tu-ios`（Turnip 的 iOS 构建）与 `build/adreno-metal-plugin`（`NativeAdrenoMetal.bundle`、
`AdrenoPM4.o`、`adrenoprobed`、`trust.json`、plist）。插件产物签的是 4 KiB 页，`trust.json` 里的 CDHash
就是后续 owner trust bundle 需要的模块。

`scripts/check-turnip-darwin-host.py` 的 deferred pressure 用例在共享 runner 上不稳定
（`drivers/Adreno740/tests/hostsim/deferred_bo.cpp:121` 期望 257 次重放，实测 `free=0 unmap=0`），
因此失败时会直编 `plugin_smoke` 并直接跑 `gen-turnip-replay.py`；该脚本本来也只在 `plugin_smoke` 缺失时才调用 checker。

### 3.4 `data-seed.yml` — 可写 Data 卷种子

`build payload` 需要一份 32 MiB 的 APFS 种子（`data_ramdisk`），它是输入而不是构建产物。
流程：下载 IPSW → 解压 `BuildManifest.plist` 定位 OS dmg → `hdiutil attach -nobrowse -readonly` →
`cp -a` 到 `UniXNU/.cache/firmware/target-volumes/os-mount` → `prepare-owner-data-fixture.py`
（要求 `sbin/launchd` sha256 = `8cec9a3c44a443b08e977ccc5d577850316ad428c575dc01f09b018dbd5b6a25`）→
上传 `build/owner-data-fixture/owner-data-32.cdr`（sha256 `740935b1654314e5482f0010242a584d9b1b63f9d8e49d78bda8f49e547c8fd7`）。

`cp -a` 需要 `|| true`：OS 卷里有 `.file` 之类无法复制的条目，中断会让种子缺文件。

### 3.5 `target-volumes.yml` — 取证（结论已定）

挂载两个原始镜像并按 `globs` 搜索候选文件，产出 `harvest/`。
**已用它证明**：OS 与 SystemOS 两个卷上都不存在 trust cache 文件（`find -iname '*trustcache*'` 全空）。
因此 `static-trustcache.bin` 只能从 IPSW 的 `BuildManifest.plist` → `StaticTrustCache` →
`Firmware/044-76040-038.dmg.trustcache` 的内层 IM4P 载荷里取，不在任何卷上。

### 3.6 `hvf-capture.yml` — 结论性死路

`macos-26` runner 是 `Apple M1 (Virtual)`，`sysctl -n kern.hv_support` 恒为 **0**，
`-accel hvf` 无法启用；runner 上也没有 `timeout` 命令。
→ 需要真实 ARM64 虚拟化环境（或 HVF 可用）的活体观测只能另想办法，不要再往这条路上排期。

---

## 4. 产物如何进入构建树

| artifact | 落位 | 后续动作 |
|---|---|---|
| `verified-root-inputs` | `out/<device>/<os_profile>/<config>/tree/build/{verified-root-os,verified-root-systemos,verified-root-os-xnu-bundle}` | `build kernel` 直接消费 |
| `turnip-ios` | `UniXNU/build/{mesa-tu-ios,adreno-metal-plugin}` | `python3 scripts/make-owner-trust-bundle.py` → `owner-trust.bundle` |
| `data-seed` | `sheng/data-seed.dbr`（`unixnu.local.toml` 的 `data_ramdisk`） | `./unixnu build payload` |
| `target-volumes` / `hvf-capture` | 不入树 | 仅作证据 |

---

## 5. 派发与排查

```bash
# 派发（全部为 workflow_dispatch）
gh workflow run verified-root.yml -R chenjin1494/workflows --ref main
gh workflow run turnip-ios.yml     -R chenjin1494/workflows --ref main
gh workflow run data-seed.yml      -R chenjin1494/workflows --ref main \
  -f ipsw='https://updates.cdn-apple.com/2025SpringFCS/fullrestores/122-77166/DCA6CFE8-90E9-4B92-880D-3DE171798E40/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw'

# 观察与取件
gh run list   -R chenjin1494/workflows --workflow turnip-ios.yml -L 3
gh run watch  <run-id> -R chenjin1494/workflows --exit-status
gh run download <run-id> -R chenjin1494/workflows -n turnip-ios -D artifacts/turnip-<run-id>

# 只看某一步的日志
gh run view <run-id> -R chenjin1494/workflows --log | grep -E "PASS:|rc=|Traceback" | tail -20
```

常见失败与对应位置：

| 现象 | 根因 | 处理 |
|---|---|---|
| `A branch or tag with the name … could not be found` | `ref` 写成短 sha | 换 40 位全 sha |
| `Recorded Mesa patch sequence differs` | 检出了上游新提交 | 钉回 `85fda3b3…` + `fetch-depth: 0` |
| `HTTP Error 403: rate limit exceeded` | `api.github.com` 匿名限流 | `env.GITHUB_TOKEN: ${{ github.token }}` |
| `Undefined symbols: _proc_selfpid` | `host_iokit_shim.cpp` 缺 `proc_selfpid` | 保留 `extern "C" int proc_selfpid(void){return (int)getpid();}` |
| `error[E0463]: can't find crate for core` | 缺 iOS target | `rustup target add aarch64-apple-ios --toolchain 1.98.0` |
| `no such include directory: …/EXPORT_HDRS/libsa` | 编译记录里的 `-I` 指向 seed 不产出的目录 | `mkdir -p` 空目录占位 |
| `Expected the actual original UDIF trailer` | 成员是裸镜像而非 UDIF | `--require-udif` 只对 OS 卷启用 |

---

## 6. 仓库内容

```
.github/workflows/
  probe-runner.yml      环境体检
  verified-root.yml     两份策略 + 探针归档（Linux 无法产出）
  turnip-ios.yml        Turnip + Adreno Metal 插件（4 KiB 签名）
  data-seed.yml         Data 卷种子（APFS 32 MiB）
  target-volumes.yml    镜像取证（结论：卷上无 trust cache）
  hvf-capture.yml       HVF 可用性探针（结论：hv_support = 0）
configs/
  sheng_seed_defconfig  不带任何驱动归档的 seed 配置，用于产出内核编译记录
scripts/
  owner-policy.py              build-owner-os-policy.py 的参数化副本，只把镜像、摘要与卷 UUID 变成变量，
                               原始校验一条不少：UDIF trailer、压缩摘要、只读 Disk Image bus 协议、
                               裸字符设备、RSA-3072 锚、逐 4 KiB 块经编译后的 C provider 校验
  build-verified-root-host.sh  把 kits/VerifiedRoot/tests/openssl_provider.c 编成
                               build/verified-root-host/verified-root.dylib，并生成 RSA-3072 owner 密钥对
```

两份策略只有配合本次生成的密钥才可复现，因此 host 密钥随 artifact 一起上传，不要丢弃。

---

## 7. 维护约定

**改动任何 workflow 时，必须在同一次提交里更新本 README。** 这是硬规则，理由很实际：
流水线的输入、pin 和产物落位散落在六个 yml 里，读完代码才能知道某次改动的后果，
而下游构建只认 artifact 名字和路径。

每次改动至少要同步这几处：

| 改了什么 | README 里要动的地方 |
|---|---|
| 新增/删除 workflow | 第 1 节一览表、第 3 节对应小节、第 6 节目录树 |
| 改输入（`workflow_dispatch.inputs`） | 第 1 节输入列、第 3 节该 workflow 小节、第 5 节派发命令 |
| 改 pin（镜像/仓库 sha/Mesa/metal2vulkan/Rust） | 第 2 节 pin 表 |
| 改 artifact 名或上传路径 | 第 1 节产物列、第 4 节落位表 |
| 踩到新的 CI 硬错误 | 第 5 节排错表（现象 → 根因 → 处理） |
| 判定某条路线不可行（如 hvf） | 第 3 节该小节 + 第 1 节状态列标 ❌ |

提交信息用 `ci(<workflow>): …` 开头，README 改动跟随同一次提交，不单开“只改文档”的补丁——
文档和流水线不同步时，后来者会照着错的说明排查。
