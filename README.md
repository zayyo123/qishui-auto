# 汽水音乐自动看广告（Android）

基于 **ADB 截图 + RapidOCR** 的纯 UI 自动化脚本：自动点击「领取奖励 / 继续观看 / 继续互动 / 领取成功」等按钮，循环看广告领取免费听时长。

> 本仓库基于 [cvabm/qishui](https://github.com/cvabm/qishui) 改进，补充了红米点击权限检测、直播间退出、防误点微信通知、点击优先级修正等。

**适用场景**：Android 手机 + Windows 电脑（USB 调试）  
**参考 App 版本**：汽水音乐 `19.1.0`（文案随版本变化时，改 `qishui_auto.py` 顶部配置即可）

---

## 功能

- 自动点 **领取奖励 / 继续观看 / 继续互动**
- 广告结束后点右上角 **领取成功 ×**（偏右点 ×，更稳）
- 同屏有领奖按钮时，**优先领奖**，不会被底部「Ns后进入直播间取消」抢走
- 没有领奖按钮时，拦截 **自动进入直播间**
- 已误入直播间时：点右上角关闭 ×，必要时按返回键退出
- 防止误点顶部 **微信/QQ 通知**：过滤横幅、收起通知栏；若已跳进微信会自动返回汽水音乐
- 启动时检测 ADB 模拟点击是否被系统拦截（红米常见问题）

脚本**不会**：改包、加速广告、伪造观看进度。广告该播多久仍播多久，只替你点按钮。

---

## 原理

| 通道 | 做法 | 说明 |
|---|---|---|
| 主通道 | `adb exec-out screencap` + RapidOCR | 视频广告是持续渲染的 SurfaceView，`uiautomator` 等不到 idle，实测必失败 |
| 可选 | `adb shell uiautomator dump` | 默认关闭；读不到奖励弹窗覆盖层，容易误点 |
| 附加 | 前台包名检测 / 返回键 / 收起通知栏 | 防误进微信、退出直播间 |

---

## 环境要求

| 项目 | 要求 |
|---|---|
| 系统 | Windows 10/11（脚本含 `.bat` 启动器） |
| Python | **3.10 ~ 3.12**（3.14 可能装不上 `onnxruntime`） |
| ADB | [platform-tools](https://developer.android.com/tools/releases/platform-tools) 并加入 PATH |
| 手机 | Android，开启 USB 调试；**红米/小米必须再开「USB 调试（安全设置）」** |
| App | 汽水音乐，先手动进入「看广告」页面 |

---

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/zayyo123/qishui-auto.git
cd qishui-auto
```

### 2. 安装依赖

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

依赖：`opencv-python`、`numpy`、`rapidocr-onnxruntime`（缺一不可）。

### 3. 手机设置（重要）

1. **设置 → 我的设备 → 全部参数** → 连续点「OS 版本 / MIUI 版本」7 次，开启开发者模式  
2. **设置 → 更多设置 → 开发者选项**，打开：
   - **USB 调试**
   - **USB 调试（安全设置）**（允许通过 USB 调试修改权限或**模拟点击**）← 红米必开，否则脚本“点了没反应”
3. 用数据线连接电脑，通知栏 USB 模式选 **传输文件**
4. 手机弹出「允许 USB 调试」→ 勾选始终允许
5. 电脑验证：

```bash
adb devices
```

应显示 `xxxxxx    device`。若是 `unauthorized`，在手机上点允许。

### 4. 运行

1. 手机打开汽水音乐，进入看广告页面，**保持亮屏不锁屏**
2. 双击 `start.bat`
3. 停止：窗口里按 `Ctrl + C`

也可以手动运行：

```bash
.venv\Scripts\python.exe qishui_auto.py
```

---

## 点击优先级（当前逻辑）

1. **领取奖励 / 继续观看 / 继续互动**（主流程，最高优先）  
2. 取消「Ns后进入直播间」（仅当没有上面的领奖按钮时）  
3. 已进入直播间 → 关闭退出  
4. **领取成功 / 退出不看了 / 坚持退出**  
5. 跳过广告（可配置关闭）

---

## 项目结构

```text
qishui-auto/
├── qishui_auto.py      # 主脚本（配置在文件顶部）
├── requirements.txt    # Python 依赖
├── start.bat           # Windows 启动器
├── README.md
└── debug_clicks/       # 运行后生成的点击标注截图（已 gitignore）
```

---

## 常用配置（`qishui_auto.py` 顶部）

| 配置 | 作用 |
|---|---|
| `REWARD_TARGETS` | 领奖/继续按钮文案 |
| `CLOSE_TARGETS` | 「领取成功」「退出不看了」「坚持退出」 |
| `SKIP_TARGETS` / `ENABLE_SKIP` | 是否自动点跳过（提前跳过可能没奖励） |
| `LEAVE_PACKAGES` | 误入后自动拉回的包名（微信/QQ 等） |
| `APP_PACKAGE` | 汽水音乐包名，默认 `com.soda.music` |
| `TARGET_ROI` | 各按钮允许点击的屏幕区域，防误点 |
| `POLL_INTERVAL` / `CLICK_COOLDOWN` | 轮询间隔 / 点击冷却 |
| `MUTE_ON_START` | 启动是否静音（默认关） |
| `USE_UIAUTOMATOR` | 默认 `False`，不要随便开 |
| `SAVE_CLICK_DEBUG` | 是否保存点击标注图到 `debug_clicks/` |

App 升级后按钮文案变了：把新文案加进对应 `*_TARGETS` 即可。

---

## 红米 / 小米必看

脚本日志若出现：

```text
当前无法模拟点击：红米拦截了 ADB 点击
```

或日志里狂刷「点击 #N 领取奖励」但手机完全不动，说明 **没有 INJECT_EVENTS 权限**：

1. 打开 **USB 调试（安全设置）**
2. 关闭再打开「USB 调试」
3. 拔掉数据线再插上，重新授权
4. 仍不行则 **重启手机** 后再连

验证点击是否通：

```bash
adb shell input tap 500 500
```

无报错且手机有反应即正常。若报 `SecurityException: INJECT_EVENTS`，权限仍未生效。

---

## 排错

| 现象 | 处理 |
|---|---|
| `adb devices` 空 | 驱动/线材/USB 模式；红米可装小米手机助手驱动 |
| 截图失败 / 一直等待 | 检查是否锁屏、`adb devices` 是否在线 |
| 识别到了但不点 | 开「USB 调试（安全设置）」 |
| 一直点「取消进直播间」不点「继续观看」 | 请使用本仓库最新脚本（已修正优先级） |
| 误进微信 | 已加防通知；仍发生时看是否点到了通知横幅 |
| 点错位置 | 查看 `debug_clicks/` 标注图，调整 `TARGET_ROI` |
| 文案识别不到 | App 升级后改 `REWARD_TARGETS` 等配置 |

---

## 注意事项

1. 仅供学习交流；请遵守 App 用户协议与当地法规。  
2. 每日广告额度有限，额度用完后不要空跑。  
3. 不要提交 `.venv/`、`debug_clicks/`、手机截图等本地文件。  
4. 分辨率：`wm size` 可能同时有 Physical / Override；脚本以截图实际像素为准。

---

## 致谢

- 原始项目：[cvabm/qishui](https://github.com/cvabm/qishui)
