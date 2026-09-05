# 03 · voxtype 实现原理

> 基于 voxtype-bin 0.7.5-1（本机实测 + 官方文档整理）。

## 全链路

```
F9 按下
  → Hyprland 快捷键（bindings.lua 绑定 voxtype record 相关命令）
    → daemon 开始录音（PipeWire 默认源，16kHz 单声道 WAV，上限 60s）
      → 松开 F9 / 再按一次（toggle 模式）
        → 本地 ASR 引擎推理
          → [output.post_process] 外部命令（可选，文本 stdin→stdout）
            → 按 output.mode 输出：type（模拟键击）/ clipboard / paste
```

## 架构组件

| 组件 | 实现 |
| --- | --- |
| 常驻方式 | systemd 用户服务 `voxtype.service`：`ExecStart=/usr/bin/voxtype daemon`，`PartOf=graphical-session.target`，崩溃 5s 自动重启 |
| 录音 | ALSA/PipeWire 默认输入设备；录音期间自动暂停 MPRIS 媒体播放器（Spotify 等） |
| 状态输出 | `$XDG_RUNTIME_DIR/voxtype/state` 写入 `idle/recording/transcribing`，供顶栏/面板集成读取；顶栏在 `recording` 时让话筒从当前主题色缓慢呼吸到黄色 |
| OSD | 可选子进程画浮动音量条式反馈（voxtype-osd-quickshell 变体可用） |

## 七引擎与两套二进制

引擎分两个二进制家族，`/usr/bin/voxtype` 是符号链接，由 `voxtype setup onnx --enable/--disable` 切换（需 root，因为写 /usr/bin）：

| 家族 | 二进制变体 | 引擎 | 语言 |
| --- | --- | --- | --- |
| whisper.cpp（GGML） | avx2 / avx512 / vulkan / cuda-12 / cuda-13 / migraphx | whisper | 99 语通用 |
| ONNX Runtime | onnx-avx2 / onnx-cuda-12/13 / onnx-rocm / migraphx | **sensevoice** / paraformer / dolphin / parakeet / moonshine / omnilingual / cohere | 见下表 |

| 引擎 | 语言 | 架构特点 |
| --- | --- | --- |
| **SenseVoice** ← 当前使用 | **zh, en, ja, ko, yue** | CTC 非自回归，一次前向即出全文，极快 |
| Paraformer | zh+en / zh+yue+en | 非自回归，中文优先 |
| Dolphin | 40 语 + 22 中文方言 | 听写优化，无英文 |
| Parakeet | 英文 | FastConformer TDT，英文榜第一梯队 |
| Moonshine | 英文（社区 ja/zh/ko/ar tiny） | 低资源低延迟 |
| Omnilingual | 1600+ 语言 | wav2vec2 CTC，小语种覆盖 |

**动态加载**：配置里可同时声明多个引擎的模型段，内存只为活跃引擎付费；空闲可卸载（on_demand_loading）。

## 模型管理

- 模型目录：`~/.local/share/voxtype/models/`
- Whisper 模型可用 `voxtype setup model --download --model <name>` 下载
- ONNX 系引擎（如 SenseVoice）的 setup 子命令是交互式 TUI，脚本化场景需手动放置文件到约定目录名（如 `sensevoice-small-int8/model.int8.onnx` + `tokens.txt`），来源为 HuggingFace `csukuangfj/sherpa-onnx-*` 系列

## 输出管线（本项目关注点）

```
转写文本
  → [output.post_process] command（stdin → stdout，可链式清洗）
    → output.mode 决定出口：
       type      模拟键击逐字输入（wtype 首选 → dotool → ydotool → 剪贴板兜底）
       clipboard 仅复制到剪贴板
       paste     复制 + 合成 Ctrl+V
```

关键配置键（`~/.config/voxtype/config.toml`）：

```toml
[output]
mode = "type"                  # type | clipboard | paste
fallback_to_clipboard = true   # 打字失败回退剪贴板
type_delay_ms = 1              # 逐字延迟

[output.post_process]
command = "..."                # 外部处理命令
timeout_ms = 30000
fallback_on_empty = true       # 空输出是否回退原文（语义待实测）
```

已知缺陷：`type` 模式的合成键击会先经过 fcitx5，中文模式下拉丁字母被 rime 缓冲导致混排顺序错乱——本项目存在的原因。详见 [01-问题分析.md](01-问题分析.md)。

## 快捷键协议

- push-to-talk：按住说话松开出字（默认）
- toggle：按一次开始、再按结束（Hyprland 侧绑定调 `voxtype record toggle`，daemon 内部走 SIGUSR1/SIGUSR2）
- `voxtype status --format json`：机器可读状态，供状态栏轮询
