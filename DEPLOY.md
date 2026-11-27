# LineDogPet 部署说明（conda 版）

> 状态：**已跑通并完成改造**。
> 代码仓库：https://github.com/stargre/linedog-pet （本仓库，已与上游解耦）
> 运行数据：`D:\LineDogPet`（不再占用 C 盘）

## 1. 环境

| 项 | 值 |
|---|---|
| conda 环境名 | `linedog` |
| 环境路径 | `%USERPROFILE%\.conda\envs\linedog` |
| Python | 3.12.16（conda-forge） |
| 解释器 | `...\linedog\python.exe`（控制台） / `pythonw.exe`（无终端） |
| PyQt6 | 6.12.1（Qt 6.12.0） |
| requests | 2.34.2 |

为什么必须 3.12：`panels.py` 用了海象运算符（≥3.8），仓库自带的 `.pyc` 是 3.12 字节码。
系统默认 `python` 是 **3.7.9**，直接跑会 `SyntaxError`。

一次性重建环境：
```powershell
conda create -y -n linedog python=3.12
& "$env:USERPROFILE\.conda\envs\linedog\python.exe" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple PyQt6 requests
```

## 2. 启动方式

**方式 A（推荐）**：双击 `LineDogPet\启动桌宠.bat`
- 走 `pythonw.exe`，不弹黑框；
- 入口是 ASCII 的 `LineDogPetv2.8.3/main.py`，避免中文文件名在非 UTF-8 代码页下解析失败。

**方式 B（命令行）**：
```powershell
cd "...\LineDogPet\LineDogPetv2.8.3"
& "$env:USERPROFILE\.conda\envs\linedog\python.exe" main.py
```

**方式 C（conda activate）**：
```powershell
conda activate linedog
cd "...\LineDogPet\LineDogPetv2.8.3"
python main.py
```

程序有单实例互斥（`Global\SmartPet_SingleInstance`），重复启动会自动退出，不会开一堆。

## 3. 确认在跑

```powershell
Get-Process pythonw -ErrorAction SilentlyContinue | Select-Object Id, @{n='RAM_MB';e={[math]::Round($_.WorkingSet64/1MB)}}
```

- 托盘出现小狗图标，桌面出现桌宠；
- 中键点狗 → 对话窗；右键 → 挠下巴/插件菜单；左键单击 → 摸头；长按拖拽 → 抱起；
- `Ctrl+Alt+D` → 隐藏/显示。

> 截图提示：PowerShell 的 `CopyFromScreen`（GDI BitBlt）**抓不到**这只狗。
> 它是 `WA_TranslucentBackground` 窗口，走 `UpdateLayeredWindow`（逐像素 Alpha），
> GDI 截屏默认排除这类窗口（加 `CAPTUREBLT` 也无效）。可用 DWM 属性 `cloaked=0` +
> `IsWindowVisible=1` + `WindowFromPoint` 命中测试来验证，人眼是可见的。

## 4. 数据目录（本分支已迁到 D 盘）

| 内容 | 路径 |
|---|---|
| 配置 | `D:\LineDogPet\config.json` |
| 日记 / 里程碑 / 收藏册 | `D:\LineDogPet\diary.db` |
| 聊天记录 | `D:\LineDogPet\chat_history.db`（首次聊天后生成） |
| 插件 / 插件存档 | `D:\LineDogPet\plugins\`、`plugin_data\` |
| 崩溃日志 | `%TEMP%\smartpet_crash.log` |

- 代码里由 `config.DATA_ROOT = r"D:\LineDogPet"` 决定；该目录不可写时自动回退 `%APPDATA%\LineDogPet`。
- 老的 `%APPDATA%\LineDogPet` 已加入 `_legacy_data_dirs()`：换机或回退时首次启动会自动把旧数据复制过来（不删原件）。
- 本次迁移已把原 `%APPDATA%\LineDogPet` 下的 `config.json` / `diary.db`(+wal/shm) / `plugins` / `plugin_data` 全部搬走，C 盘副本已删除，数据完整性用 SHA256 校验一致（7 条日记事件、7 个「第一次」、收藏册 1 项均在）。

## 5. 开机自启：已关闭

- `DEFAULT_CONFIG["autostart"]` 改为 `False`，全新安装不会偷偷写注册表；
- 注册表项 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\LineDogPet` **已删除**；
- 需要时在 设置 → 桌面/快捷键 → 勾选「开机自启」即可重新写入。

## 6. 与上游的解耦（本分支改动）

| 位置 | 处理 |
|---|---|
| `config.UPDATE_CHECK_URLS` / `UPDATE_CHECK_URL` | 清空 → 不再请求上游 releases 接口 |
| `config.PLUGIN_REPO_URL` / `PLUGIN_INDEX_URLS` / `PLUGIN_GUIDE_URL` | 清空 → 插件仅本地 zip/文件夹安装 |
| `panels` 关于页 / 更新提示 / 插件菜单 | 对空值做条件渲染，不再出现上游 Gitee/GitCode 入口 |
| `config.RELEASE_GITHUB` | 改为本项目 `stargre/linedog-pet` |
| 启动 10 秒后的自动检查更新 | 已在逻辑层短路，不再发任何网络请求 |

**AI 功能仍然是唯一会主动联网的地方**（用户自行填 API Key，默认关闭）。

## 7. 当前实测基线

| 指标 | 数值 |
|---|---|
| 常驻内存 | 约 150 MB |
| 线程数 | 17 |
| 窗口 | 150×150，置顶、无边框、逐像素透明 |
| 位置持久化 | 重启后回到上次位置（本次为 1489,6） |

## 8. 停止 / 重启

```powershell
# 停止
Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process
# 或托盘右键 → 退出
```

重启：双击 `启动桌宠.bat`。

## 9. 已知与代码一致的坑

- `requirements.txt` 只有 `PyQt6`，**漏了 requests**（AI 用）；建议改成两行。
- 仓库自带的 `smartpet/__pycache__/` 里 `gifplayer`、`bongomap` 只有 `.pyc` 无源码，且**无人 import**，属死代码（已被 `.gitignore` 排除，不再进仓库）。
- README 里「CPU 紧张踱步」「浏览器探头偷看」在 v2.8.3 已下线（函数是空实现）。

## 10. v2.9 新增：AI Agent 三件套

| 新增模块 | 作用 |
|---|---|
| `smartpet/perception.py` | 桌面环境感知：3s 采样前台窗口/全屏/闲置/CPU/音乐，提炼事件流 |
| `smartpet/memory.py` | 长期记忆：sqlite（facts / episodes），关键词召回，容量上限自动淘汰 |
| `smartpet/llm.py` | OpenAI 兼容客户端：流式 SSE + function calling 增量拼装 |
| `smartpet/tools.py` | 工具注册表 + 13 个内置工具（说/走/大小/睡/玩/喂/记忆/看环境…） |
| `smartpet/agent.py` | 对话主循环：人设 + 环境 + 记忆 → LLM → 工具 → 再推理 |

**新增数据文件**：`D:\LineDogPet\memory.db`（AI 记忆）。设置 → AI 对话 → 「AI 能力（Agent）」里可开关并一键清空。

**接入方式**：`app.PetApp.__init__` 里创建 `memory / perception / agent`；`handle_chat()` 改为走 Agent（AI 未开启时自动退回本地正则指令）；`ChatDialog` 增加流式方法 `start_reply / append_reply / add_note / finish_reply`。

**自测**（在仓库根目录执行，用 `linedog` 环境）：

```powershell
& "$env:USERPROFILE\.conda\envs\linedog\python.exe" -m unittest discover -s tests -v
& "$env:USERPROFILE\.conda\envs\linedog\python.exe" tests\e2e_chat.py
```

- `tests/test_memory.py`：记忆读写/覆盖/检索/上限/清空
- `tests/test_perception.py`：窗口分类、快照渲染、事件提炼、winapi 异常降级
- `tests/test_agent.py`：mock LLM 服务 + 工具调用 + 流式增量 + 非流式回退
- `tests/e2e_chat.py`：真实 `PetApp`（离屏）→ 对话框 → Agent → 记忆落库 → 气泡，17 项断言

## 11. 往 GitHub 推送（本机需要代理）

本机直连 GitHub 443 会被阻断，而 **git 默认不读 Windows 系统代理**（Python/浏览器会读）。
所以推送要显式带代理：

```powershell
# 单次
git -c http.proxy=http://127.0.0.1:7897 push

# 或固化到本仓库（代理端口以实际为准，可在系统设置里看到）
git config http.proxy http://127.0.0.1:7897
# 取消：git config --unset http.proxy
```

拉取同理。若代理没开，会看到 `Failed to connect to github.com port 443`。
