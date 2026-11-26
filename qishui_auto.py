# -*- coding: utf-8 -*-
"""
汽水音乐自动看广告（纯 UI 自动化）

通道：
  主通道 —— adb exec-out screencap + RapidOCR（视频广告是持续渲染的 SurfaceView，
            uiautomator 等不到 idle，实测必然失败，所以 OCR 才是主通道）
  可选   —— adb shell uiautomator dump（部分设备/静态页可用，启动时自动探测，
            连续失败会自动关闭）
  附加   —— input keyevent 164 静音（退出恢复）

声明：只点击界面上真实存在的按钮，不修改 App、不伪造广告观看进度、不加速播放。
      广告该播多久还是多久，本脚本只负责替你点“领取奖励 / 继续观看 / 跳过”。
"""
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime

# Windows 控制台默认 GBK，强制 UTF-8 避免中文 print 报错
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ================== 配置区 ==================
# 领奖/继续类目标（按优先级，先点靠前的）
# 「继续互动」：直播/互动广告中途弹窗，不点会卡住倒计时
REWARD_TARGETS = ["领取奖励", "继续观看", "继续互动"]
# 关闭类目标：广告结束页右上角的「领取成功 ×」胶囊，点它才会关掉广告、弹出奖励弹窗
CLOSE_TARGETS = ["领取成功", "退出不看了", "坚持退出"]
# 「xx秒后 / xx s 后进入直播间 [取消]」：文字需同时含倒计时和「进入直播间」，
# 避免把普通横幅「点击进入直播间」也当成自动跳转
# 已误入直播间时的 OCR 特征（命中 >=2 才判定，避免广告文案误触）
LIVE_ROOM_MARKERS = ["加入了直播间", "说点什么", "本场点赞", "更多直播"]

# 跳过目标（仅当广告本身允许跳过时才会出现）
SKIP_TARGETS = ["跳过广告", "跳过", "关闭广告"]
ENABLE_SKIP = True          # 提前点“跳过”有可能拿不到奖励，视情况关掉

# 避免点到顶部微信等通知横幅后跳走
APP_PACKAGE = "com.soda.music"
# 误入这些包名时自动返回汽水音乐
LEAVE_PACKAGES = {
    "com.tencent.mm",          # 微信
    "com.tencent.mobileqq",    # QQ
    "com.tencent.wework",      # 企业微信
    "com.android.systemui",    # 通知下拉（少见）
}
# 顶部通知带：相对高度；过宽文本默认当通知，禁止点击
NOTIFY_Y_MAX = 0.12
NOTIFY_WIDTH_MIN = 0.50

USE_UIAUTOMATOR = False     # True / False / "auto"。默认关：它看不到弹窗等覆盖层，会抢占 OCR
OCR_SCORE = 0.5             # OCR 置信度下限
FUZZY = True                # 允许轻微识别误差
POLL_INTERVAL = 0.1         # 每轮间隔（截图+OCR 本身约 0.8~1.5s）
OCR_FALLBACK_INTERVAL = 2.5 # 保留：uiautomator 兜底时的最小间隔
CLICK_COOLDOWN = 0.4        # 点击后冷却；同坐标重复点击由 do_click 的 3s 去重兜底
MUTE_ON_START = False       # 默认不动音量；需要广告期间静音再改成 True
OCR_MAX_WIDTH = 720         # OCR 缩放宽度（太小会漏「取消」等小字，别低于 600）
SAVE_CLICK_DEBUG = True     # 命中时保存标注截图
DUMP_PATH = "/sdcard/qishui_dump.xml"
UIA_TIMEOUT = 4             # uiautomator 单次超时；失败会浪费这么久，所以探测后就别频繁试
UIA_MAX_FAIL = 3            # 连续失败几次后自动关闭 uiautomator

# 各目标允许点击的区域（相对屏幕宽高 0~1）
TARGET_ROI = {
    "领取奖励": {"x": (0.20, 0.80), "y": (0.30, 0.78)},
    "继续观看": {"x": (0.20, 0.80), "y": (0.30, 0.78)},
    "继续互动": {"x": (0.20, 0.80), "y": (0.30, 0.78)},
    # 顶部控件避开最上方通知条（约 0~4%），从 4% 起才允许点「领取成功/跳过」
    "领取成功": {"x": (0.55, 1.00), "y": (0.04, 0.18)},
    "退出不看了": {"x": (0.00, 1.00), "y": (0.80, 1.00)},
    "坚持退出": {"x": (0.20, 0.80), "y": (0.55, 0.95)},
    "跳过广告": {"x": (0.55, 1.00), "y": (0.04, 0.18)},
    "跳过":     {"x": (0.55, 1.00), "y": (0.04, 0.18)},
    "关闭广告": {"x": (0.55, 1.00), "y": (0.04, 0.18)},
}
CENTER_TARGETS = {"领取奖励", "继续观看", "继续互动"}
# ===========================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEBUG_DIR = os.path.join(SCRIPT_DIR, "debug_clicks")

click_seq = 0
ocr_engine = None
last_ocr_ts = 0.0
screen_cache = None
muted = False
saved_volume = None
uia_ok = None
uia_fails = 0
last_click = {"target": None, "x": -1, "y": -1, "ts": 0.0}


# ------------------------- adb 基础 -------------------------
def adb(args, binary=False, timeout=20, check_err=False):
    """执行 adb 命令，失败/超时返回 None。
    check_err=True 时把 stderr 一并返回 (stdout, stderr)，便于发现红米拦截点击。"""
    try:
        r = subprocess.run(["adb"] + args, capture_output=True, timeout=timeout)
        out = r.stdout if binary else r.stdout.decode("utf-8", "ignore")
        err = r.stderr.decode("utf-8", "ignore") if r.stderr else ""
        if check_err:
            return out, err, r.returncode
        if r.returncode != 0:
            return None
        return out
    except Exception:
        return (None, "exception", 1) if check_err else None


def screen_size():
    """返回逻辑分辨率 (宽, 高)。
    注意：wm size 可能同时有 Physical 和 Override，screencap / input tap 用的是
    Override（逻辑分辨率），所以取最后一个匹配值。"""
    global screen_cache
    if screen_cache:
        return screen_cache
    out = adb(["shell", "wm", "size"]) or ""
    ms = re.findall(r"(\d+)x(\d+)", out)
    screen_cache = (int(ms[-1][0]), int(ms[-1][1])) if ms else (1080, 2400)
    return screen_cache


def set_screen_size(w, h):
    global screen_cache
    if w and h:
        screen_cache = (int(w), int(h))


def foreground_package():
    """读取当前前台应用包名；失败返回空字符串。"""
    out = adb(["shell", "dumpsys", "window"], timeout=8) or ""
    m = re.search(r"mCurrentFocus=Window\{[^ ]+\s+([a-zA-Z0-9._]+)/", out)
    if not m:
        m = re.search(r"mFocusedApp=.*?([a-zA-Z0-9._]+)/", out)
    if not m:
        out2 = adb(["shell", "dumpsys", "activity", "activities"], timeout=8) or ""
        m = re.search(r"mResumedActivity:.*? ([a-zA-Z0-9._]+)/", out2)
    return m.group(1) if m else ""


def collapse_notifications():
    """收起通知栏/顶部横幅，降低误点微信通知概率。"""
    adb(["shell", "cmd", "statusbar", "collapse"], timeout=5)


def return_to_qishui():
    """用返回键或显式拉起汽水音乐，从微信等页面回到广告流程。"""
    print("  >>> 检测到离开汽水音乐，尝试返回…")
    collapse_notifications()
    adb(["shell", "input", "keyevent", "4"])  # BACK
    time.sleep(0.45)
    pkg = foreground_package()
    if pkg == APP_PACKAGE:
        return True
    # 再退一次（通知点进微信后有时要两下）
    adb(["shell", "input", "keyevent", "4"])
    time.sleep(0.35)
    pkg = foreground_package()
    if pkg == APP_PACKAGE:
        return True
    print(f"  >>> 仍在 {pkg or '未知'}，重新打开汽水音乐")
    adb([
        "shell", "am", "start",
        "-a", "android.intent.action.MAIN",
        "-c", "android.intent.category.LAUNCHER",
        "-p", APP_PACKAGE,
    ], timeout=8)
    time.sleep(1.0)
    return foreground_package() == APP_PACKAGE


def ensure_in_qishui():
    """若前台是微信/QQ 等（多半误点顶部通知），拉回汽水。
    返回 True 表示本轮做了恢复、应跳过点击。广告跳转的抖音/浏览器不在此强制拉回。"""
    pkg = foreground_package()
    if not pkg or pkg == APP_PACKAGE:
        return False
    if pkg in LEAVE_PACKAGES:
        print(f"⚠️  当前前台是 {pkg}（疑似误点通知），自动返回汽水音乐")
        return_to_qishui()
        return True
    return False


def is_notification_banner(node, sw, sh):
    """顶部过宽文本视为通知横幅（微信消息等），禁止点击。"""
    if len(node) < 6:
        return False
    label, cx, cy, x1, y1, x2, y2 = node[0], node[1], node[2], node[3], node[4], node[5], node[6]
    text = label or ""
    if any(k in text for k in ("微信", "WeChat", "QQ", "[微信]", "企业微信")):
        return True
    # 顶部宽横幅：通知中心样式，不是右上角小胶囊
    width_ratio = (x2 - x1) / float(sw or 1)
    if cy <= sh * NOTIFY_Y_MAX and width_ratio >= NOTIFY_WIDTH_MIN:
        n = normalize_text(text)
        # 广告控件例外：领取成功× / 跳过广告 等短胶囊
        if "领取成功" in n or "跳过广告" in n or n in ("跳过", "关闭广告"):
            return False
        return True
    return False


# ------------------------- 文字匹配 -------------------------
def normalize_text(s):
    if not s:
        return ""
    s = s.strip().replace(" ", "").replace("\u3000", "")
    s = re.sub(r"[×xX✕✖·•\|丨\[\]【】()（）\d:：，,。.]+$", "", s)
    s = re.sub(r"^[×xX✕✖·•\|丨]+", "", s)
    s = s.replace("领収", "领取").replace("领职", "领取").replace("领敢", "领取")
    s = s.replace("奨", "奖").replace("奬", "奖").replace("勵", "励")
    return s


def text_match(recognized, target):
    a = normalize_text(recognized)
    b = normalize_text(target)
    if not a:
        return False
    if a == b or b in a:
        return True
    # 识别结果比目标短时，只允许 3 字以上的子串（避免“广告”误配“关闭广告”）
    if len(a) >= 3 and a in b:
        return True
    if not FUZZY:
        return False
    hit = sum(1 for ch in b if ch in a)
    return hit >= max(2, len(b) - 1)


def in_roi(cx, cy, sw, sh, target):
    roi = TARGET_ROI.get(target, {"x": (0.0, 1.0), "y": (0.0, 1.0)})
    (x0, x1), (y0, y1) = roi["x"], roi["y"]
    return x0 * sw <= cx <= x1 * sw and y0 * sh <= cy <= y1 * sh


# ------------------------- 通道一：OCR（主） -------------------------
def ocr_available():
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401
        return True
    except Exception:
        return False


def get_ocr():
    global ocr_engine
    if ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        ocr_engine = RapidOCR()
    return ocr_engine


def screenshot():
    import cv2
    import numpy as np
    data = adb(["exec-out", "screencap", "-p"], binary=True, timeout=10)
    if not data:
        return None
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is not None:
        h, w = img.shape[:2]
        set_screen_size(w, h)  # 以真实截图为准，避免 wm size 分辨率不一致
    return img


def ocr_nodes(screen):
    """返回 [(label, cx, cy), ...]，坐标为原图像素"""
    import cv2
    import numpy as np
    h, w = screen.shape[:2]
    scale = 1.0
    img = screen
    if w > OCR_MAX_WIDTH:
        scale = OCR_MAX_WIDTH / float(w)
        img = cv2.resize(screen, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    result, _ = get_ocr()(img, use_cls=False)
    if not result:
        return []
    inv = 1.0 / scale
    items = []
    for row in result:
        if not row or len(row) < 3:
            continue
        box, text, score = row[0], row[1], float(row[2])
        if score < OCR_SCORE:
            continue
        pts = np.array(box, dtype=np.float32)
        cx = float(pts[:, 0].mean()) * inv
        cy = float(pts[:, 1].mean()) * inv
        x1 = int(round(float(pts[:, 0].min()) * inv))
        x2 = int(round(float(pts[:, 0].max()) * inv))
        y1 = int(round(float(pts[:, 1].min()) * inv))
        y2 = int(round(float(pts[:, 1].max()) * inv))
        items.append((str(text), int(round(cx)), int(round(cy)), x1, y1, x2, y2))
    return items


# ------------------------- 通道二：uiautomator（可选） -------------------------
def dump_nodes(timeout=20):
    """返回 [(label, cx, cy), ...]；失败返回 None"""
    adb(["shell", "rm", "-f", DUMP_PATH], timeout=timeout)
    adb(["shell", "uiautomator", "dump", "--compressed", DUMP_PATH], timeout=timeout)
    xml = adb(["shell", "cat", DUMP_PATH], timeout=timeout)
    if not xml or "<hierarchy" not in xml:
        return None
    xml = xml[xml.find("<hierarchy"):]
    end = xml.rfind(">")
    if end > 0:
        xml = xml[: end + 1]
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None

    nodes = []
    for n in root.iter("node"):
        label = (n.get("text") or "").strip() or (n.get("content-desc") or "").strip()
        if not label:
            continue
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds") or "")
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        nodes.append((label, (x1 + x2) // 2, (y1 + y2) // 2, x1, y1, x2, y2))
    return nodes


def uia_available():
    """启动探测一次；失败则本次会话关闭。"""
    global uia_ok
    if uia_ok is not None:
        return uia_ok
    if USE_UIAUTOMATOR is False:
        uia_ok = False
    elif USE_UIAUTOMATOR is True:
        uia_ok = True
    else:
        print("· 探测 uiautomator …")
        t = time.time()
        uia_ok = dump_nodes(timeout=UIA_TIMEOUT) is not None
        print(f"  {'✅ 可用' if uia_ok else '❌ 不可用（等不到 idle），改用 OCR'}"
              f"（{time.time() - t:.1f}s）")
    return uia_ok


# ------------------------- 点击 -------------------------
def save_debug(screen, x, y, target, label):
    if not SAVE_CLICK_DEBUG or screen is None:
        return
    try:
        import cv2
        os.makedirs(DEBUG_DIR, exist_ok=True)
        vis = screen.copy()
        cv2.drawMarker(vis, (x, y), (0, 0, 255), cv2.MARKER_CROSS, 60, 3)
        cv2.circle(vis, (x, y), 28, (0, 0, 255), 3)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = re.sub(r"[^\w\u4e00-\u9fff]+", "_", target)
        cv2.imwrite(os.path.join(DEBUG_DIR, f"{ts}_{click_seq:04d}_{safe}_{x}_{y}.png"), vis)
    except Exception:
        pass


def do_click(x, y, target, label, source, screen=None):
    """点击并抑制 3 秒内同一坐标的重复点击。
    红米/小米若未开「USB调试（安全设置）」，input tap 会被系统拒绝，这里会明确报错。"""
    global click_seq
    now = time.time()
    if (target == last_click["target"]
            and abs(x - last_click["x"]) < 20
            and abs(y - last_click["y"]) < 20
            and now - last_click["ts"] < 3.0):
        return False

    click_seq += 1
    last_click.update(target=target, x=x, y=y, ts=now)
    print(f"  >>> 点击 #{click_seq} [{datetime.now():%H:%M:%S}] "
          f"{target} <- 「{label}」 @({x},{y}) via {source}")
    # 领取成功胶囊要偏右点「×」，只点文字中间在部分机型上关不掉广告
    _, err, code = adb(["shell", "input", "tap", str(x), str(y)], check_err=True)
    if code != 0 or (err and "SecurityException" in err):
        print("❌ 点击被系统拦截（INJECT_EVENTS）。红米请打开：")
        print("   设置 → 更多设置 → 开发者选项 → USB调试（安全设置）")
        print("   打开后重新插线授权，再运行 start.bat")
    save_debug(screen, x, y, target, label)
    return True


# 「xx秒后 / xx s 后进入直播间 [取消]」：数字 + 秒/s/S + 后，且同段含「进入直播间」
LIVE_ENTER_RE = re.compile(r"\d+\s*[秒sSｓＳ]\s*后")


def try_cancel_live_enter(nodes, source, screen=None):
    """检测到「4s后进入直播间」这类倒计时时，点「取消」拦下来。
    注意：若同屏已有「继续观看/领取奖励」，应由上层先点领奖，不要在这里抢点。"""
    if not nodes:
        return False

    def is_countdown(lbl):
        return bool(LIVE_ENTER_RE.search(lbl)) and ("进入直播间" in lbl)

    if not any(is_countdown(n[0]) for n in nodes):
        return False

    # 优先找独立的「取消」节点
    for n in nodes:
        if normalize_text(n[0]) == "取消":
            print(f"  >>> 拦截「进入直播间」，点取消 <- 「{n[0]}」 @({n[1]},{n[2]})")
            do_click(n[1], n[2], "取消", n[0], source, screen)
            return True

    # 兜底：OCR 把「6s后进入直播间取消」并成一段时，点该段右侧「取消」
    for n in nodes:
        lbl = n[0]
        if ("取消" in lbl) and is_countdown(lbl):
            x1, x2 = n[3], n[5]
            tx = int(x2 - max(10, (x2 - x1) * 0.10))
            print(f"  >>> 拦截「进入直播间」，点取消(合并段右侧) <- 「{n[0]}」 @({tx},{n[2]})")
            do_click(tx, n[2], "取消", n[0], source, screen)
            return True

    return False


def find_and_click_targets(nodes, targets, source, screen=None):
    """在给定目标列表里找可点项并点击；命中返回目标名，否则 None。"""
    if not nodes or not targets:
        return None
    sw, sh = screen_size()
    for target in targets:
        cands = []
        for node in nodes:
            label, cx, cy = node[0], node[1], node[2]
            if not text_match(label, target):
                continue
            if not in_roi(cx, cy, sw, sh, target):
                continue
            # 过滤微信等顶部通知横幅，避免误点跳转
            if is_notification_banner(node, sw, sh):
                continue
            cands.append(node)
        if not cands:
            continue

        if target in CENTER_TARGETS:
            midx, midy = sw * 0.5, sh * 0.5
            cands.sort(key=lambda c: (c[1] - midx) ** 2 + (c[2] - midy) ** 2)
        else:
            cands.sort(key=lambda c: (-c[1], c[2]))

        node = cands[0]
        label, cx, cy = node[0], node[1], node[2]
        if target == "领取成功" and len(node) >= 6:
            x1, x2 = node[3], node[5]
            cx = int(x2 - max(6, (x2 - x1) * 0.08))
            cx = min(sw - 8, cx + max(12, int((x2 - x1) * 0.15)))
        do_click(cx, cy, target, label, source, screen)
        return target
    return None


def is_live_room(nodes):
    """是否已误入直播间（抖音等）。需命中多个特征，避免广告页「直播间观看时长」误判。"""
    if not nodes:
        return False
    labels = [n[0] for n in nodes]
    hit = 0
    for marker in LIVE_ROOM_MARKERS:
        if any(marker in lbl for lbl in labels):
            hit += 1
    return hit >= 2


def exit_live_room(nodes, source, screen=None):
    """退出直播间：优先点文案「退出不看了」，否则点右上角 ×，再按返回键兜底。"""
    sw, sh = screen_size()
    # 精选直播面板上的「退出不看了」
    for n in nodes or []:
        if text_match(n[0], "退出不看了") and in_roi(n[1], n[2], sw, sh, "退出不看了"):
            do_click(n[1], n[2], "退出不看了", n[0], source, screen)
            return "退出不看了"

    # 直播间右上角关闭 ×（图标无字；K60 实测约 1380,200 @1440x3200）
    x = int(sw * 0.958)
    y = int(sh * 0.0625)
    print(f"  >>> 检测到直播间，点右上角关闭 @({x},{y})")
    do_click(x, y, "退出直播间", "右上角×", source, screen)
    time.sleep(0.4)
    # 若仍像直播间，再偏右上补点一次 + 返回键
    print("  >>> 直播间兜底：补点并按返回键")
    adb(["shell", "input", "tap", str(min(sw - 20, x + 30)), str(max(80, y - 20))])
    time.sleep(0.2)
    adb(["shell", "input", "keyevent", "4"])
    return "退出直播间"


def match_and_click(nodes, source, screen=None):
    """点击优先级：
    1) 领取奖励 / 继续观看 / 继续互动（主流程，绝不能被底部「取消进直播间」抢走）
    2) 取消「Ns后进入直播间」（没有领奖按钮时才拦）
    3) 已进直播间则退出
    4) 领取成功 / 退出不看了 / 跳过
    """
    if not nodes:
        return None

    # 1. 领奖/继续最优先
    hit = find_and_click_targets(nodes, REWARD_TARGETS, source, screen)
    if hit:
        return hit

    # 2. 没有「继续观看」等时，再拦自动进直播间
    if try_cancel_live_enter(nodes, source, screen):
        return "取消"

    # 3. 已经进了直播间：退出
    if is_live_room(nodes):
        return exit_live_room(nodes, source, screen)

    # 4. 关广告 / 跳过
    targets = CLOSE_TARGETS + (SKIP_TARGETS if ENABLE_SKIP else [])
    return find_and_click_targets(nodes, targets, source, screen)


# ------------------------- 静音 -------------------------
def get_music_volume():
    """返回媒体音量索引；失败 None"""
    out = adb(["shell", "cmd", "media_session", "volume", "--stream", "3", "--get"]) or ""
    m = re.search(r"volume is (\d+)", out)
    return int(m.group(1)) if m else None


def toggle_mute():
    # KEYCODE_VOLUME_MUTE：在“原音量”和 0 之间切换
    adb(["shell", "input", "keyevent", "164"])


def mute_on_start():
    global muted, saved_volume
    if not MUTE_ON_START:
        return
    v = get_music_volume()
    if v is None:
        print("⚠️  读不到媒体音量，跳过自动静音（不影响点击）")
        return
    if v == 0:
        print("🔇 当前已是静音，跳过")
        return
    saved_volume = v
    toggle_mute()
    time.sleep(0.3)
    if get_music_volume() == 0:
        muted = True
        print(f"🔇 已静音（原音量 {v}，退出时恢复）")
    else:
        print("⚠️  静音未生效，继续运行")


def unmute():
    global muted
    if not muted:
        return
    # 只有当前确实是 0 才切回，避免把用户自己调过的音量又打开
    if get_music_volume() == 0:
        toggle_mute()
    muted = False
    print("🔊 已恢复音量")


# ------------------------- 主循环 -------------------------
def main():
    global last_ocr_ts, uia_fails

    use_ocr = ocr_available()
    use_uia = uia_available()
    sw, sh = screen_size()

    print("🚀 汽水音乐自动看广告脚本启动")
    print(f"   逻辑分辨率: {sw}x{sh}")
    print(f"   主通道: {'OCR' if use_ocr else '（OCR 不可用）'}   "
          f"uiautomator: {'开' if use_uia else '关'}")
    print(f"   领奖目标: {REWARD_TARGETS}")
    print(f"   跳过目标: {SKIP_TARGETS if ENABLE_SKIP else '已禁用'}")
    print("   停止请按 Ctrl + C")
    print("-" * 50)

    if not use_ocr and not use_uia:
        print("❌ 没有可用通道：请 pip install -r requirements.txt")
        return

    # 启动时探测能否模拟点击（红米默认常拦截）
    _, tap_err, tap_code = adb(
        ["shell", "input", "tap", str(max(1, sw - 2)), "1"], check_err=True
    )
    if tap_code != 0 or (tap_err and "SecurityException" in tap_err):
        print("❌ 当前无法模拟点击：红米拦截了 ADB 点击")
        print("   请打开：设置 → 更多设置 → 开发者选项 → USB调试（安全设置）")
        print("   打开后拔掉数据线再插上，手机点「允许」，然后重新运行脚本")
        return

    mute_on_start()
    idle = 0
    try:
        while True:
            clicked = False

            # 误点顶部微信通知后会跳走：每轮先检查前台，不在汽水则拉回
            if ensure_in_qishui():
                idle = 0
                time.sleep(0.6)
                continue

            # 主通道：OCR。奖励弹窗等覆盖层 uiautomator 抓不到，必须靠 OCR
            if use_ocr:
                # 先收起通知栏，减少点到横幅
                if idle % 8 == 0:
                    collapse_notifications()
                screen = screenshot()
                if screen is not None and match_and_click(ocr_nodes(screen), "ocr", screen):
                    clicked = True

            # 兜底：OCR 没找到时才用 uiautomator
            if not clicked and use_uia:
                nodes = dump_nodes(timeout=UIA_TIMEOUT)
                if nodes is None:
                    uia_fails += 1
                    if uia_fails >= UIA_MAX_FAIL:
                        print(f"⚠️  uiautomator 连续失败 {uia_fails} 次，本次会话关闭")
                        use_uia = False
                else:
                    uia_fails = 0
                    if match_and_click(nodes, "uia"):
                        clicked = True

            if clicked:
                idle = 0
                time.sleep(CLICK_COOLDOWN)
                continue

            idle += 1
            if idle % 10 == 0:
                print(f"· 等待中…（{idle} 轮，未发现可点按钮）")
            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        unmute()


if __name__ == "__main__":
    main()
