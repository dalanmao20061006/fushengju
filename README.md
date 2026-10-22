# 浮生居 · 引擎 POC（llama.cpp / Android arm64）

这个目录**不是 App 代码**，只是引擎层的验证工装：
用 GitHub Actions 从 llama.cpp 官方源码交叉编译，拿到能在手机上直接跑的
`llama-cli` / `llama-bench`，先把三件事测清楚：

1. 4B 模型 + 8K 上下文，在 8 Gen 3 上到底多少 tok/s、多少内存
2. **OpenCL（Adreno）在这台机上到底能不能用**（这是最大的不确定项）
3. 静态构建的二进制能不能在 `/data/local/tmp` 直接跑（不依赖 App）

---

## 一、先回答：你手里那批旧 `.so` 能不能用？

**能当「对照组」，不能当「基线」。** 先做 10 秒体检：

```powershell
python scripts\check-so.py "D:\你的旧库目录"
```

判定规则：

| 体检结果 | 含义 | 能不能用 |
|---|---|---|
| `[FAIL] glibc` | Termux / proot / 桌面 Linux 编的 | **App 里 dlopen 不了**，只能当跑分参照 |
| `[OK] Bionic` | NDK / 从 APK 里扒的 | 技术上能加载，但要过下面三关 |
| `[WARN] libc++_shared` | 依赖共享 C++ 运行时 | 必须一起打包，漏了启动即崩 |

即便是 Bionic，也要过三关，任何一关不过都不该当基线：

1. **版本成套**：`libllama.so` / `libggml.so` / `libggml-base.so` / `libggml-cpu.so` 必须是同一次构建的产物。混版本 = 启动即崩，而且崩在 native 层，Java 侧连异常都收不到。
2. **编译选项未知**：有没有开 `dotprod` / `i8mm` / `fp16`？没开就跑不满 8 Gen 3；开了但没开对，换台机器就 SIGILL。跑分不可比。
3. **符号/ABI 匹配**：我们的 JNI wrapper 是按某个 `llama.h` 版本写的，`llama_*` 的签名/结构体大小变了就是未定义行为。

**所以：旧库留着当对照组（同模型、同提示词，跟纯净构建对跑，差多少一目了然），基线用官方源码新编。**

---

## 二、云端构建（GitHub Actions）

> 说明：构建在 GitHub 的机器上跑，不在你的 FX-8300 上跑——省 30–60 分钟/次。

### 1. 建仓库并推上去

```bash
cd F:\WorkSpace\浮生居-poc
git init
git add .
git commit -m "engine poc: llama.cpp android build"
git branch -M main
git remote add origin https://github.com/dalanmao20061006/fushengju.git
git push -u origin main
```

> 仓库名随意，私有公开都行。公开仓库 Actions 分钟数不计费；私有仓库每月 2000 分钟免费额度，够跑几十次。

### 2. 跑一次

GitHub → 仓库 → **Actions** → 左侧 `build-llama-android` → **Run workflow**：

| 参数 | 建议值 | 说明 |
|---|---|---|
| `llama_ref` | `master` | 也可以填具体 tag（如 `b5xxx`），要可复现就填 tag |
| `ndk_version` | `r27c` | 稳定，支持 Android 15 |
| `with_opencl` | **先 false** | 第一轮先拿 CPU 基线；第二轮再开 true 测 OpenCL |

跑完在 **Artifacts** 下载：

- `llama-android-arm64-cli` → `llama-cli`、`llama-bench`、`SHA256SUMS.txt`
- `llama-android-arm64-shared` → `lib/*.so` + `include/*.h`（后面给 App 用）

### 3. 上手机跑

```powershell
# 手机已连 ADB（你这边无线调试是通的）
adb push llama-cli llama-bench /data/local/tmp/
adb shell chmod 755 /data/local/tmp/llama-cli /data/local/tmp/llama-bench

# 模型也推上去（或直接用手机里已有的路径）
adb push .\models\Cupid-Qwen3-4B-Q4_K_M.gguf /data/local/tmp/model.gguf
```

**跑分（CPU 基线）：**

```bash
adb shell "/data/local/tmp/llama-bench -m /data/local/tmp/model.gguf -p 512 -n 128 -t 6 -ngl 0"
```

**测 8K 上下文能不能撑住：**

```bash
adb shell "/data/local/tmp/llama-cli -m /data/local/tmp/model.gguf -c 8192 -t 6 -n 64 -no-cnv -p '你好，做个自我介绍'"
```

**测 OpenCL（第二轮，构建时 `with_opencl=true`）：**

```bash
adb shell "/data/local/tmp/llama-bench -m /data/local/tmp/model.gguf -p 512 -n 128 -ngl 99"
# 同时看它有没有报 "ggml_opencl: ... device"；没有就是没起来
```

### 4. 结果怎么判

| 观测 | 判定 |
|---|---|
| `-ngl 0` 生成 ≥ 10 t/s | CPU 基线达标（对应你 Luna-7B 的 10 t/s 经验） |
| `-ngl 99` 比 `-ngl 0` 快 ≥ 30% | OpenCL 值得做 |
| `-ngl 99` 反而更慢 / 报错 | OpenCL 判死，P0 定案 CPU-only |
| 8K 上下文 OOM / 被系统杀 | 降 4K，或模型换更小量化 |

---

## 三、给后续 App 用（这轮先不接）

`llama-android-arm64-shared` 里的 `lib/*.so` 直接丢进 `app/src/main/jniLibs/arm64-v8a/`，
`include/*.h` 给 JNI 层编译用。**注意**：它们之间必须成套，不要和旧库混用。

---

## 四、目录

```
浮生居-poc/
├─ .github/workflows/build-android.yml   # 云端构建
├─ scripts/
│  ├─ check-so.py                        # 旧 .so 体检（Bionic / glibc / libc++）
│  └─ bench-device.ps1                   # 一键推机 + 跑分
└─ README.md
```

## 五、这份 POC 不做什么

- 不写 App、不碰 UI、不建数据库
- 不改 llama.cpp 源码
- 不碰 8080 端口（引擎后续走 AIDL，不占端口）
