# 商店审核注意事项（Marketplace Review Notes）

> 2026-08-22 整理。来源：本插件 issue
> [#1428](https://github.com/omacom/omarchy-plugin-marketplace/issues/1428)
> 及兄弟插件 #1468、#1401 的审核往返。供上架前自查与复审对照。

## 一、商店审核机制速览

1. **按精确 HEAD 审核**：修复必须上游提交 → issue 评论附 commit 链接 → 按新 HEAD 复审。
2. **自动化基线**：本仓因 `systemctl --user restart voxtype.service` 被标
   **service-management 能力、review-required**。这不是拒绝；README 里说明用途即可，
   但每次改动基线都会重新列出行号，README 与代码必须同步。
3. **人工复审**只盯供应链完整性与资源/注入边界，精确到 `文件:行号`。

## 二、审核人在意的点（从三单反馈提炼）

| # | 关注点 | 出处 | 判例 |
|---|--------|------|------|
| 1 | 供应链固定：不执行下载物、pin immutable 来源 | #1468 | moving-HEAD → root 构建被打回 |
| 2 | TOCTOU：校验与特权使用之间不得隔用户可写状态 | #1468 | make in user cache 被打回 |
| 3 | **资源无界：下载必须按声明大小截断，先限界后校验** | **#1428 本仓判例** | 「downloads until EOF…unbounded disk space」 |
| 4 | 注入面：外部数据 PlainText 渲染、命令列表传参 | #1401 | AutoText 富文本注入 |
| 5 | 提权纪律：**固定内联命令 + 用户显式触发** | #1428 ONNX | `pkexec voxtype setup onnx --enable` 固定串通过；取消不动现状 |
| 6 | 卸载卫生：不留悬挂钩子 | 提交清单 | explicit consent 条款 |
| 7 | 带回归测试的修复被接受最快 | #1428 修复评论 | 「all 7 bridge tests pass」被写进回复 |

## 三、本仓库反馈与修复状态

| 轮次 | 审核意见 | 修复 |
|------|----------|------|
| 1 | 模型下载读到 EOF 才校验大小/哈希，超大响应可耗尽磁盘 | `94b7dd9`：最多多读 1 字节，超限立即失败 + 回归测试 |
| 2（主动加固） | — | `7e41fe8`：下载前检测 ONNX 引擎可用性；ONNX 启用走用户显式触发的固定命令 `pkexec voxtype setup onnx --enable` |
| 3 | universal paste 三连：① `stdout=PIPE` 全量吞剪贴板才哈希（无界内存）；② 状态目录回退可预测的 `/tmp/voxtype-enhance`；③ marker 裸 follow 不验属主/类型 | 本轮修复：流式分块哈希 + 8 MiB 封顶；去掉 `/tmp` 回退，强制 `XDG_RUNTIME_DIR` 且目录验属主/0700；marker 读写 `O_NOFOLLOW`+fstat 验 regular/属主、0600。回归测试 `tests/test_universal_paste.py` |

### 最新提审更新（2026-08-24）

本轮提交包含以下面向审核和用户体验的修复：

- ARM 安装完成后，模型探测和 engine 切换统一使用已校验的
  `/usr/local/bin/voxtype`，避免 CLI 继续调用包管理器提供的 Whisper 二进制；
- ONNX 缺失时保留结构化错误，并在面板显示可点击的
  `Enable ONNX support (administrator approval required)` 操作；用户确认后
  通过既有固定提权流程安装，成功后自动恢复被阻塞的模型下载；
- 下载进度改为从 stderr 逐行实时解析，不再等进程结束后才更新；
- 未下载任何模型时隐藏 Language 和 Output 控件，避免用户在 Voxtype 没有可用
  转写模型时写入设置；
- 增加 ARM/非 ARM 可执行文件选择回归测试，并修正测试对宿主机架构的依赖。

验证结果：`python3 -m unittest discover -s tests` 共 23 个测试通过，
`git diff --check` 通过。请审核方按本次最新 HEAD 重新扫描并复审；本轮没有新增
提权入口，仍只有用户明确点击后触发的固定 `pkexec` 命令。

HEAD 即本轮修复提交（`6b2cffd` 之后）。本轮增加 ARM/aarch64 平台分支：插件先检测实际可用的
Voxtype 变体，缺少 ONNX 时不下载模型；x86_64 仍使用固定的 `pkexec voxtype setup onnx --enable`，
ARM 则只有用户明确确认后才下载并校验固定版本的官方 `voxtype-0.7.5-linux-aarch64-onnx`，
安装到 `/usr/local/bin/voxtype`，并创建当前用户的 systemd override，不覆盖包管理器拥有的
`/usr/bin/voxtype`。`6bf0803` 新增第二处提权：voxtype 二进制缺失时，
变更类入口先经固定命令 `pkexec pacman -S --noconfirm --needed voxtype-bin` 提供安装
（取消即不动，只读查询不触发），已在 #1428 披露。

## 四、本仓库对照自查要点（侦察发现，复审重点）

- [x] 模型下载：pinned HuggingFace URL + SHA-256/大小双校验 + 尺寸上限（判例修复已落地）
- [x] 特权面两处固定命令（`pkexec voxtype setup onnx --enable`；
      `pkexec pacman -S --noconfirm --needed voxtype-bin`），均用户显式触发、列表传参
- [x] 外部命令全部列表参数，无 shell 拼接；engine 切换委托 voxtype CLI 并回读验证
- [x] ARM ONNX 下载使用固定官方 URL、大小上限和 SHA-256 校验；授权前不写系统路径
- [x] ARM 安装不覆盖 `/usr/bin/voxtype`，仅使用 `/usr/local/bin` 与用户级 service override
- [x] ARM 安装提权边界不重新打开用户可写临时路径；校验后的有界字节通过 stdin
      传给固定内联 helper，再由 root 在 `/usr/local/bin` 原子替换目标文件
- [x] tests/ 24 个用例可跑，覆盖 ARM binary selection、提权字节传输、下载边界、配置回读和 universal paste
- [x] **卸载卫生**：README 卸载节要求先切换到非 universal 输出模式，
      清理 `pre/post_output_command`，再移除插件；模型和其它 Voxtype 数据仍由用户决定是否保留。
- [x] hyprctl dispatch 表达式仅由固定常量组成，没有用户输入插值；后续改动不得引入动态拼接。
- [x] 路径回退使用 `Path.home()` / XDG 路径；没有硬编码 UID 路径。
- [x] 配置变更后重启既有的用户级 `voxtype.service`，README 已说明 Voxtype 不会热加载配置。
