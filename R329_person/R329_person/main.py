from maix import camera, display, image, nn, app, time, network, video, http
import os
import socket
import json
import _thread
import gc

# ========== 配置 ==========
CONF_TH = 0.45
IOU_TH = 0.45
KNIFE_CONF_TH = 0.78    # 刀具检测阈值
DEVICE_CONF_TH = 0.8     # 枪支检测阈值
SAVE_DIR = "/sd/detections/"
HTTP_PORT = 8080
AUTO_START = True          # True=上电自动检测, False=等待ESP32串口指令
# ========== 共享状态（主循环写入，HTTP线程读取） ==========
latest_frame_jpg = None       # 最新摄像头帧的JPEG字节，用于/snapshot.jpg回退
streamer = None               # MJPEG推流器实例
detection_status = {           # 用于前端告警面板
    'state': 'idle',           # 'idle' | 'detecting' | 'recording'
    'person': False,
    'knife': False,
    'device': False,
    'loitering': False,
    'frames': 0,
    'recording_prefix': '',
}

# WiFi 配置
WIFI_SSID = "brabbit"      # 修改为你的WiFi名称
WIFI_PASSWORD = "88888888"  # 修改为你的WiFi密码

# ========== HTTP服务器功能 ==========
MIME_TYPES = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.mp4': 'video/mp4',
    '.avi': 'video/x-msvideo',
    '.h264': 'video/h264',
    '.hevc': 'video/hevc',
    '.h265': 'video/hevc',
    '.mkv': 'video/x-matroska',
    '.mov': 'video/quicktime',
}

def get_mime_type(filename):
    ext = os.path.splitext(filename)[1].lower()
    return MIME_TYPES.get(ext, 'application/octet-stream')

def get_jpeg_sequences():
    sequences = {}
    try:
        files = os.listdir(SAVE_DIR)
        for f in files:
            name_lower = f.lower()
            if not name_lower.endswith('.jpg'):
                continue
            name_no_ext = f[:-4]
            last_us = name_no_ext.rfind('_')
            if last_us < 0:
                continue
            prefix = name_no_ext[:last_us]
            seq_part = name_no_ext[last_us + 1:]
            if len(seq_part) < 4 or not seq_part.isdigit():
                continue
            if prefix not in sequences:
                sequences[prefix] = []
            sequences[prefix].append(f)
    except:
        pass

    result = {}
    for prefix, flist in sequences.items():
        flist.sort()
        risk_label = ""
        risk_class = ""
        try:
            meta_path = SAVE_DIR + prefix + ".meta"
            if os.path.exists(meta_path):
                meta = json.loads(open(meta_path, "r").read())
                has_knife = meta.get("knife", False)
                has_device = meta.get("device", False)
                is_loitering = meta.get("loitering", False)
                if is_loitering:
                    if has_knife:
                        risk_label = "有人员持刀长时间逗留"
                        risk_class = "danger"
                    elif has_device:
                        risk_label = "有人员长时间持设备逗留"
                        risk_class = "danger"
                    else:
                        risk_label = "有人员长时间逗留"
                        risk_class = "low"
                elif has_knife:
                    risk_label = "危险：人员持刀"
                    risk_class = "danger"
                elif has_device:
                    risk_label = "危险：人员持设备"
                    risk_class = "danger"
                else:
                    risk_label = "无危险"
                    risk_class = "safe"
        except:
            pass
        result[prefix] = {
            'files': flist,
            'count': len(flist),
            'risk_label': risk_label,
            'risk_class': risk_class,
        }
    return dict(sorted(result.items(), reverse=True))

def list_files_html():
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>MaixCAM 监控</title>
    <link rel="stylesheet" href="https://unpkg.com/element-plus/dist/index.css">
    <script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
    <script src="https://unpkg.com/element-plus/dist/index.full.min.js"></script>
    <style>
        :root {
            --bg: #06090f;
            --card: #111827;
            --border: #1f2937;
            --text: #e5e7eb;
            --dim: #9ca3af;
            --red: #ef4444;
            --green: #10b981;
            --yellow: #f59e0b;
            --blue: #3b82f6;
            --purple: #8b5cf6;
            --cyan: #06b6d4;
            --radius: 16px;
            --safe-bottom: env(safe-area-inset-bottom, 12px);
        }
        * { margin:0; padding:0; box-sizing:border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100dvh;
            overflow-x: hidden;
            -webkit-tap-highlight-color: transparent;
            -webkit-font-smoothing: antialiased;
            padding-bottom: calc(72px + var(--safe-bottom));
            background-image:
                radial-gradient(ellipse at 20% 0%, rgba(59,130,246,0.06) 0%, transparent 50%),
                radial-gradient(ellipse at 80% 100%, rgba(139,92,246,0.05) 0%, transparent 50%);
        }

        /* ===== Top Bar ===== */
        .topbar {
            position: sticky; top: 0; z-index: 100;
            background: rgba(17,24,39,0.85);
            backdrop-filter: blur(20px) saturate(180%);
            -webkit-backdrop-filter: blur(20px) saturate(180%);
            padding: 10px 16px;
            display: flex; align-items: center; justify-content: space-between;
            border-bottom: 1px solid rgba(255,255,255,0.06);
        }
        .topbar-left { display:flex; align-items:center; gap:10px; }
        .topbar-avatar {
            width: 34px; height: 34px; border-radius: 10px;
            background: linear-gradient(135deg, #3b82f6, #8b5cf6);
            display: flex; align-items: center; justify-content: center;
            font-size: 18px;
        }
        .topbar-info { display:flex; flex-direction:column; }
        .topbar-title { font-size:15px; font-weight:700; letter-spacing:0.3px; }
        .topbar-sub { font-size:10px; color:var(--dim); }
        .topbar-right { display:flex; align-items:center; gap:10px; }
        .topbar-time { font-size:12px; color:var(--dim); font-variant-numeric:tabular-nums; }
        .live-pill {
            display:flex; align-items:center; gap:5px;
            padding: 5px 12px; border-radius: 20px;
            font-size:10px; font-weight:800; letter-spacing:1px;
            border: 1px solid rgba(255,255,255,0.1);
        }
        .live-pill.off { background: rgba(75,85,99,0.2); color: var(--dim); }
        .live-pill.on {
            background: rgba(239,68,68,0.15);
            color: var(--red);
            border-color: rgba(239,68,68,0.3);
            animation: pillGlow 2s infinite;
        }
        @keyframes pillGlow {
            0%,100% { box-shadow: 0 0 0 0 rgba(239,68,68,0); }
            50% { box-shadow: 0 0 12px 3px rgba(239,68,68,0.15); }
        }
        .live-dot { width:6px; height:6px; border-radius:50%; background:var(--dim); }
        .live-pill.on .live-dot { background: var(--red); animation: dotPulse 1.2s infinite; }
        @keyframes dotPulse {
            0%,100% { transform: scale(1); opacity: 1; }
            50% { transform: scale(1.6); opacity: 0.5; }
        }

        /* ===== Section Header ===== */
        .section-header {
            display:flex; align-items:center; gap:8px;
            margin: 16px 14px 8px;
        }
        .section-icon {
            width: 28px; height: 28px; border-radius: 8px;
            display:flex; align-items:center; justify-content:center;
            font-size:14px;
        }
        .section-icon.cam { background: linear-gradient(135deg, #ef4444, #f97316); }
        .section-icon.stat { background: linear-gradient(135deg, #3b82f6, #06b6d4); }
        .section-label { font-size:13px; font-weight:700; letter-spacing:1px; text-transform:uppercase; color:var(--dim); flex:1; }

        /* ===== Camera ===== */
        .cam-wrapper {
            margin: 0 12px;
            background: #000;
            border-radius: var(--radius);
            overflow: hidden;
            position: relative;
            aspect-ratio: 4/3;
            border: 1px solid rgba(255,255,255,0.08);
            box-shadow: 0 8px 32px rgba(0,0,0,0.4);
        }
        .cam-wrapper img { width:100%; height:100%; object-fit:contain; display:block; }
        .cam-gradient {
            position:absolute; bottom:0; left:0; right:0;
            height: 80px;
            background: linear-gradient(transparent, rgba(0,0,0,0.9));
            pointer-events: none;
        }
        .cam-info {
            position:absolute; bottom:0; left:0; right:0;
            padding: 10px 14px 12px;
            display:flex; align-items:flex-end; justify-content:space-between;
        }
        .cam-state {
            display:flex; align-items:center; gap:6px;
        }
        .cam-dot-big { width:10px; height:10px; border-radius:50%; }
        .cam-dot-big.green { background: var(--green); }
        .cam-dot-big.red { background: var(--red); animation: dotPulse 1s infinite; }
        .cam-dot-big.blue { background: var(--blue); }
        .cam-state-text { font-size:12px; font-weight:800; letter-spacing:1px; }
        .cam-meta { text-align:right; }
        .cam-meta-num { font-size:18px; font-weight:800; color:#fff; }
        .cam-meta-label { font-size:10px; color:var(--dim); }

        /* ===== Alert Banner ===== */
        .alert-banner {
            margin: 8px 12px 0;
            padding: 14px 16px;
            border-radius: var(--radius);
            display:flex; align-items:center; gap:12px;
            position: relative; overflow: hidden;
        }
        .alert-banner::before {
            content:''; position:absolute; inset:0;
            opacity: 0.1;
        }
        .alert-banner.safe {
            background: linear-gradient(135deg, #064e3b, #065f46);
            border: 1px solid rgba(16,185,129,0.3);
        }
        .alert-banner.warn {
            background: linear-gradient(135deg, #2d1115, #3d1115);
            border: 1px solid rgba(239,68,68,0.4);
            animation: alertShimmer 2s infinite;
        }
        .alert-banner.danger {
            background: linear-gradient(135deg, #3d0f0f, #4d0f0f);
            border: 1px solid rgba(239,68,68,0.6);
            animation: alertShimmer 0.8s infinite;
        }
        @keyframes alertShimmer {
            0%,100% { box-shadow: 0 0 8px 0 rgba(239,68,68,0.2), inset 0 0 30px transparent; }
            50% { box-shadow: 0 0 24px 4px rgba(239,68,68,0.3), inset 0 0 30px rgba(239,68,68,0.05); }
        }
        .alert-icon-wrap {
            width:44px; height:44px; border-radius:14px;
            display:flex; align-items:center; justify-content:center;
            font-size:22px; flex-shrink:0;
        }
        .alert-icon-wrap.safe { background: rgba(16,185,129,0.2); }
        .alert-icon-wrap.warn { background: rgba(239,68,68,0.2); }
        .alert-icon-wrap.danger { background: rgba(239,68,68,0.25); animation: iconShake 0.5s infinite; }
        @keyframes iconShake {
            0%,100% { transform: rotate(0); }
            25% { transform: rotate(-5deg); }
            75% { transform: rotate(5deg); }
        }
        .alert-body { flex:1; min-width:0; }
        .alert-body-title { font-size:15px; font-weight:700; }
        .alert-body-sub { font-size:11px; color:rgba(255,255,255,0.5); margin-top:2px; }

        /* ===== Stat Cards ===== */
        .stat-section { margin: 10px 12px 0; }
        .stat-grid {
            display:grid;
            grid-template-columns:1fr 1fr 1fr;
            gap:8px;
        }
        .stat-card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 14px 12px 12px;
            position: relative; overflow: hidden;
            transition: all 0.3s cubic-bezier(0.4,0,0.2,1);
        }
        .stat-card::after {
            content:''; position:absolute; top:0; left:0; right:0; height:3px;
            background: transparent;
            border-radius: 3px 3px 0 0;
            transition: background 0.3s;
        }
        .stat-card.active-danger {
            border-color: rgba(239,68,68,0.4);
            background: linear-gradient(180deg, rgba(239,68,68,0.08), var(--card) 40%);
        }
        .stat-card.active-danger::after { background: var(--red); }
        .stat-card.active-warn {
            border-color: rgba(245,158,11,0.4);
            background: linear-gradient(180deg, rgba(245,158,11,0.08), var(--card) 40%);
        }
        .stat-card.active-warn::after { background: var(--yellow); }
        .stat-card.active-info {
            border-color: rgba(59,130,246,0.4);
            background: linear-gradient(180deg, rgba(59,130,246,0.08), var(--card) 40%);
        }
        .stat-card.active-info::after { background: var(--blue); }
        .stat-icon-wrap {
            width:32px; height:32px; border-radius:10px;
            display:flex; align-items:center; justify-content:center;
            font-size:16px; margin-bottom:8px;
        }
        .stat-icon-wrap.red { background: rgba(239,68,68,0.15); }
        .stat-icon-wrap.yellow { background: rgba(245,158,11,0.15); }
        .stat-icon-wrap.blue { background: rgba(59,130,246,0.15); }
        .stat-icon-wrap.green { background: rgba(16,185,129,0.15); }
        .stat-label { font-size:10px; color:var(--dim); letter-spacing:0.5px; margin-bottom:2px; }
        .stat-value { font-size:14px; font-weight:800; }
        .stat-value.red { color: var(--red); }
        .stat-value.yellow { color: var(--yellow); }
        .stat-value.blue { color: var(--blue); }
        .stat-value.green { color: var(--green); }
        .stat-value.mono { color: var(--text); }

        /* ===== Files Tab ===== */
        .files-header-bar {
            margin: 12px 14px 8px;
            display:flex; align-items:center; gap:10px;
        }
        .files-count-badge {
            background: rgba(59,130,246,0.15);
            color: var(--blue);
            padding: 4px 12px; border-radius: 20px;
            font-size:12px; font-weight:700;
        }
        .file-card {
            margin: 0 12px 8px;
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            padding: 14px;
            display:flex; align-items:center; gap:12px;
            transition: all 0.2s;
            position: relative; overflow: hidden;
        }
        .file-card:active { background: #1a2436; transform: scale(0.98); }
        .file-check { width:20px; height:20px; accent-color:#3b82f6; flex-shrink:0; }
        .file-thumb {
            width: 44px; height: 44px; border-radius: 12px;
            display:flex; align-items:center; justify-content:center;
            font-size:20px; flex-shrink:0;
        }
        .file-thumb.has-risk { background: linear-gradient(135deg, rgba(239,68,68,0.2), rgba(245,158,11,0.2)); }
        .file-thumb.no-risk { background: rgba(59,130,246,0.1); }
        .file-body { flex:1; min-width:0; overflow:hidden; }
        .file-name-row {
            display:flex; align-items:center; gap:8px;
            margin-bottom: 4px;
        }
        .file-ts { font-size:11px; font-family: 'SF Mono', 'Menlo', monospace; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
        .risk-pill {
            padding: 2px 8px; border-radius: 8px;
            font-size:9px; font-weight:700; letter-spacing:0.5px; white-space:nowrap;
        }
        .risk-pill.safe { background: rgba(16,185,129,0.15); color: var(--green); }
        .risk-pill.danger { background: rgba(239,68,68,0.15); color: var(--red); }
        .risk-pill.low { background: rgba(245,158,11,0.15); color: var(--yellow); }
        .file-stats { font-size:10px; color:var(--dim); display:flex; gap:12px; }
        .file-btns { display:flex; gap:4px; flex-shrink:0; }
        .fb {
            width:36px; height:36px; border-radius:10px;
            border:none; display:flex; align-items:center; justify-content:center;
            font-size:15px; cursor:pointer; transition:all 0.15s;
        }
        .fb.play { background: rgba(59,130,246,0.15); color: var(--blue); }
        .fb.play:active { background: rgba(59,130,246,0.3); }
        .fb.dl { background: rgba(16,185,129,0.15); color: var(--green); }
        .fb.dl:active { background: rgba(16,185,129,0.3); }
        .fb.del { background: rgba(239,68,68,0.15); color: var(--red); }
        .fb.del:active { background: rgba(239,68,68,0.3); }

        .empty-state {
            text-align:center; padding:60px 20px;
        }
        .empty-illustration {
            width:80px; height:80px; margin:0 auto 16px;
            border-radius:24px;
            background: linear-gradient(135deg, rgba(59,130,246,0.1), rgba(139,92,246,0.1));
            display:flex; align-items:center; justify-content:center;
            font-size:36px;
        }

        /* ===== Bottom Nav ===== */
        .bottom-nav {
            position:fixed; bottom:0; left:0; right:0; z-index:100;
            background: rgba(17,24,39,0.9);
            backdrop-filter: blur(20px) saturate(180%);
            -webkit-backdrop-filter: blur(20px) saturate(180%);
            border-top: 1px solid rgba(255,255,255,0.06);
            display:flex; padding-bottom: var(--safe-bottom);
        }
        .nav-item {
            flex:1; display:flex; flex-direction:column;
            align-items:center; justify-content:center;
            padding:8px 0 4px; cursor:pointer; gap:3px;
            color: var(--dim); transition: all 0.2s;
            -webkit-tap-highlight-color:transparent;
            user-select:none; position:relative;
        }
        .nav-item.active { color: var(--blue); }
        .nav-item.active::before {
            content:''; position:absolute; top:0; left:50%; transform:translateX(-50%);
            width:24px; height:3px; background:var(--blue); border-radius:0 0 3px 3px;
        }
        .nav-icon-wrap {
            width:36px; height:36px; border-radius:12px;
            display:flex; align-items:center; justify-content:center;
            font-size:20px; transition: all 0.2s;
        }
        .nav-item.active .nav-icon-wrap {
            background: rgba(59,130,246,0.15);
        }
        .nav-label { font-size:10px; font-weight:600; letter-spacing:0.5px; }

        /* ===== Misc ===== */
        ::-webkit-scrollbar { width:0; height:0; }
        .fade-enter-active, .fade-leave-active { transition: opacity 0.25s ease; }
        .fade-enter-from, .fade-leave-to { opacity: 0; }
    </style>
</head>
<body>
    <div id="app">
        <!-- ===== Top Bar ===== -->
        <div class="topbar">
            <div class="topbar-left">
                <div class="topbar-avatar">🛡</div>
                <div class="topbar-info">
                    <span class="topbar-title">MaixCAM 监控</span>
                    <span class="topbar-sub">AI 智能检测系统</span>
                </div>
            </div>
            <div class="topbar-right">
                <span class="topbar-time">{{ clock }}</span>
                <div class="live-pill" :class="status.state!=='idle'?'on':'off'">
                    <span class="live-dot"></span>
                    {{ status.state!=='idle'?'LIVE':'STANDBY' }}
                </div>
            </div>
        </div>

        <!-- ===== Tab: 实时监控 ===== -->
        <div v-show="tab==='live'">
            <!-- Camera -->
            <div class="section-header">
                <div class="section-icon cam">🎥</div>
                <span class="section-label">实时画面</span>
            </div>
            <div class="cam-wrapper">
                <img :src="mjpegUrl" alt="Camera" @error="onCamError">
                <div class="cam-gradient"></div>
                <div class="cam-info">
                    <div class="cam-state">
                        <span class="cam-dot-big" :class="status.state==='idle'?'green':status.state==='detecting'?'blue':'red'"></span>
                        <span class="cam-state-text">{{ status.state==='idle'?'待机中':status.state==='detecting'?'检测中':'录制中 ●' }}</span>
                    </div>
                    <div class="cam-meta" v-if="status.frames>0">
                        <div class="cam-meta-num">{{ status.frames }}</div>
                        <div class="cam-meta-label">帧已保存</div>
                    </div>
                </div>
            </div>

            <!-- Alert Banner -->
            <div class="alert-banner" :class="bannerClass">
                <div class="alert-icon-wrap" :class="bannerClass">
                    {{ bannerIcon }}
                </div>
                <div class="alert-body">
                    <div class="alert-body-title">{{ bannerTitle }}</div>
                    <div class="alert-body-sub">{{ bannerSub }}</div>
                </div>
            </div>

            <!-- Stats -->
            <div class="section-header">
                <div class="section-icon stat">📊</div>
                <span class="section-label">检测状态</span>
            </div>
            <div class="stat-grid stat-section">
                <div class="stat-card" :class="{'active-danger':status.person}">
                    <div class="stat-icon-wrap red">👥</div>
                    <div class="stat-label">人员检测</div>
                    <div class="stat-value" :class="status.person?'red':'mono'">{{ status.person?'⚠ 发现人员':'✔ 安全' }}</div>
                </div>
                <div class="stat-card" :class="{'active-danger':status.knife}">
                    <div class="stat-icon-wrap red">🔪</div>
                    <div class="stat-label">刀具检测</div>
                    <div class="stat-value" :class="status.knife?'red':'mono'">{{ status.knife?'⚠ 发现刀具':'✔ 安全' }}</div>
                </div>
                <div class="stat-card" :class="{'active-danger':status.device}">
                    <div class="stat-icon-wrap yellow">📱</div>
                    <div class="stat-label">设备检测</div>
                    <div class="stat-value" :class="status.device?'red':'mono'">{{ status.device?'⚠ 发现设备':'✔ 安全' }}</div>
                </div>
                <div class="stat-card" :class="{'active-warn':status.loitering}">
                    <div class="stat-icon-wrap yellow">⏰</div>
                    <div class="stat-label">逗留告警</div>
                    <div class="stat-value" :class="status.loitering?'yellow':'mono'">{{ status.loitering?'⚠ 人员逗留':'✔ 正常' }}</div>
                </div>
                <div class="stat-card" :class="{'active-info':status.frames>0}">
                    <div class="stat-icon-wrap blue">📷</div>
                    <div class="stat-label">录制帧数</div>
                    <div class="stat-value blue">{{ status.frames || '---' }}</div>
                </div>
                <div class="stat-card active-info">
                    <div class="stat-icon-wrap green">📅</div>
                    <div class="stat-label">录制标识</div>
                    <div class="stat-value green" style="font-size:9px;word-break:break-all;">{{ status.recording_prefix || '---' }}</div>
                </div>
            </div>
        </div>

        <!-- ===== Tab: 录像回放 ===== -->
        <div v-show="tab==='files'">
            <div class="files-header-bar">
                <span style="font-size:15px;font-weight:700;flex:1;">📁 录像回放</span>
                <span class="files-count-badge">{{ files.length }} 个视频</span>
                <el-button size="small" circle @click="refreshFiles" :loading="loadingFiles" style="background:#1f2937;border:none;color:#e5e7eb;">🔄</el-button>
                <el-button size="small" @click="deleteSelected" :disabled="selected.length===0" style="background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.3);color:#ef4444;font-weight:700;">
                    删除({{selected.length}})
                </el-button>
            </div>

            <div v-if="files.length===0" class="empty-state">
                <div class="empty-illustration">📹</div>
                <div style="font-size:15px;font-weight:700;">暂无录像</div>
                <div style="font-size:12px;color:var(--dim);margin-top:4px;">系统检测到人员后将自动录制</div>
            </div>

            <div v-for="(f,i) in files" :key="f.prefix" class="file-card">
                <input type="checkbox" :value="f.prefix" v-model="selected" class="file-check">
                <div class="file-thumb" :class="f.risk_class==='danger'?'has-risk':'no-risk'">
                    {{ f.risk_class==='danger'?'🚨':'🎬' }}
                </div>
                <div class="file-body">
                    <div class="file-name-row">
                        <span class="file-ts">{{ f.prefix }}</span>
                        <span v-if="f.risk_label" class="risk-pill" :class="f.risk_class">{{ f.risk_label }}</span>
                    </div>
                    <div class="file-stats">
                        <span>📷 {{ f.count }} 帧</span>
                        <span>⏱ 10 fps</span>
                    </div>
                </div>
                <div class="file-btns">
                    <a :href="'/play-seq/'+f.prefix" target="_blank" class="fb play" title="播放">▶</a>
                    <a :href="'/file/'+f.prefix+'.hevc?download=1'" class="fb dl" title="下载">⬇</a>
                    <button class="fb del" @click="deleteOne(f.prefix)" title="删除">🗑</button>
                </div>
            </div>
        </div>

        <!-- ===== Bottom Nav ===== -->
        <div class="bottom-nav">
            <div class="nav-item" :class="{active:tab==='live'}" @click="tab='live'">
                <div class="nav-icon-wrap">🎥</div>
                <span class="nav-label">实时监控</span>
            </div>
            <div class="nav-item" :class="{active:tab==='files'}" @click="tab='files'">
                <div class="nav-icon-wrap">📁</div>
                <span class="nav-label">录像回放</span>
            </div>
        </div>
    </div>

    <script>
        const { createApp, ref, computed, onMounted, onUnmounted } = Vue;
        const app = createApp({
            setup() {
                const tab = ref('live');
                const status = ref({state:'idle',person:false,knife:false,device:false,loitering:false,frames:0,recording_prefix:''});
                const files = ref([]);
                const selected = ref([]);
                const loadingFiles = ref(false);
                const clock = ref('');
                const mjpegUrl = ref('http://'+window.location.hostname+':8000/stream');
                let st=null, ct=null;

                function updateClock() {
                    const n = new Date();
                    clock.value = n.getHours().toString().padStart(2,'0')+':'+n.getMinutes().toString().padStart(2,'0')+':'+n.getSeconds().toString().padStart(2,'0');
                }

                const bannerClass = computed(() => {
                    if (status.value.state==='recording')
                        return (status.value.knife||status.value.device||status.value.loitering)?'danger':'warn';
                    return 'safe';
                });
                const bannerIcon = computed(() => {
                    if (bannerClass.value==='danger') return '🚨';
                    if (bannerClass.value==='warn') return '🔴';
                    return '🛡';
                });
                const bannerTitle = computed(() => {
                    if (bannerClass.value==='danger') {
                        let a=[];
                        if(status.value.knife) a.push('刀具');
                        if(status.value.device) a.push('设备');
                        if(status.value.loitering) a.push('逗留');
                        return '⚠ 危险告警：'+a.join(' + ');
                    }
                    if (status.value.state==='recording') return '录制进行中...';
                    if (status.value.state==='detecting') return '正在搜索目标...';
                    return '系统运行正常';
                });
                const bannerSub = computed(() => {
                    if (status.value.state==='recording') return '已录制 '+status.value.frames+' 帧 | 持续监控中';
                    if (status.value.state==='detecting') return status.value.person?'已发现人员，准备启动录制':'搜索人员中，请稍候...';
                    return '系统待机中，自动检测已就绪';
                });

                async function pollStatus() {
                    try{const r=await fetch('/api/status');status.value=await r.json();}catch(_){}
                }
                async function refreshFiles() {
                    loadingFiles.value=true;
                    try{
                        const r=await fetch('/api/sequences');
                        const d=await r.json();
                        const l=[];
                        for(const[p,i]of Object.entries(d))
                            l.push({prefix:p,count:i.count||0,risk_label:i.risk_label||'',risk_class:i.risk_class||'',files:i.files||[]});
                        files.value=l;
                    }catch(_){}
                    loadingFiles.value=false;
                }
                function deleteOne(p) {
                    if(!confirm('确定删除 "'+p+'" ?'))return;
                    fetch('/delete-seq/'+encodeURIComponent(p),{method:'DELETE'}).then(r=>r.text()).then(()=>refreshFiles()).catch(e=>alert('删除失败: '+e));
                }
                function deleteSelected() {
                    if(selected.value.length===0)return;
                    if(!confirm('确定删除选中的 '+selected.value.length+' 个视频?'))return;
                    Promise.all(selected.value.map(p=>fetch('/delete-seq/'+encodeURIComponent(p),{method:'DELETE'}).then(r=>r.text()))).then(()=>{selected.value=[];refreshFiles();}).catch(e=>alert('删除失败: '+e));
                }
                function onCamError(){}

                onMounted(() => {
                    updateClock();ct=setInterval(updateClock,1000);
                    pollStatus();st=setInterval(pollStatus,500);
                    refreshFiles();
                    const n=new Date();
                    fetch('/api/set-time?t='+encodeURIComponent(n.getFullYear()+'-'+(n.getMonth()+1)+'-'+n.getDate()+'-'+n.getHours()+'-'+n.getMinutes()+'-'+n.getSeconds())).catch(()=>{});
                });
                onUnmounted(() => { clearInterval(st); clearInterval(ct); });

                return {tab,status,files,selected,loadingFiles,clock,mjpegUrl,bannerClass,bannerIcon,bannerTitle,bannerSub,pollStatus,refreshFiles,deleteOne,deleteSelected,onCamError};
            }
        });
        app.use(ElementPlus);
        app.mount('#app');
    </script>
</body>
</html>""".encode('utf-8')

def send_file_response(conn, filepath, download=False):
    try:
        file_size = os.stat(filepath)[6]
        filename = os.path.basename(filepath)
        mime_type = get_mime_type(filename)

        disposition = f'attachment; filename="{filename}"' if download else 'inline'

        header = f"HTTP/1.1 200 OK\r\n"
        header += f"Content-Type: {mime_type}\r\n"
        header += f"Content-Length: {file_size}\r\n"
        header += f"Content-Disposition: {disposition}\r\n"
        header += "Connection: close\r\n\r\n"
        conn.send(header.encode())

        with open(filepath, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                conn.send(chunk)
        return True
    except Exception as e:
        print(f"发送文件失败: {e}")
        return False

def recv_all(conn, total_bytes):
    """读取指定字节数，TCP recv不一定一次返回全部"""
    buf = b''
    remaining = total_bytes
    while remaining > 0:
        chunk = conn.recv(min(remaining, 4096))
        if not chunk:
            break
        buf += chunk
        remaining -= len(chunk)
    return buf

def handle_request(conn, addr):
    try:
        # 以bytes方式读取，避免损坏二进制body数据
        raw_data = conn.recv(4096)
        if not raw_data:
            return

        # 分离header和body
        header_end = raw_data.find(b'\r\n\r\n')
        if header_end < 0:
            return

        headers_raw = raw_data[:header_end].decode('utf-8', errors='ignore')
        body_initial = raw_data[header_end + 4:]

        lines = headers_raw.split('\r\n')
        if not lines:
            return

        parts = lines[0].split(' ')
        if len(parts) < 2:
            return

        method = parts[0]
        path = parts[1]

        # 解析请求头
        headers = {}
        for line in lines[1:]:
            if ':' in line:
                key, val = line.split(':', 1)
                headers[key.strip().lower()] = val.strip()

        content_length = int(headers.get('content-length', '0'))
        content_type = headers.get('content-type', '')

        # 读取完整body（加上初始recv中已读取的部分）
        body_bytes = body_initial
        if content_length > len(body_initial):
            body_bytes += recv_all(conn, content_length - len(body_initial))

        print(f"[{addr[0]}] {method} {path}")

        if path == '/' or path == '/index.html':
            body = list_files_html()
            response = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
            conn.send(response)

        elif path.startswith('/file/'):
            filename = path[6:].split('?')[0]
            filepath = SAVE_DIR + filename
            download = '?download=1' in path

            if os.path.exists(filepath):
                send_file_response(conn, filepath, download)
            else:
                body = b"File not found"
                response = f"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                conn.send(response)

        elif path == '/api/files':
            try:
                files = os.listdir(SAVE_DIR)
                files = [f for f in files if f.lower().endswith(('.jpg', '.jpeg', '.png', '.mp4', '.avi', '.h264', '.hevc', '.h265', '.mkv', '.mov'))]
                body = json.dumps(files).encode()
            except:
                body = b"[]"
            response = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
            conn.send(response)

        elif path.startswith('/delete-seq/'):
            try:
                prefix = path[12:].split('?')[0]
                try:
                    import urllib
                    prefix = urllib.parse.unquote(prefix)
                except:
                    pass

                deleted = 0
                errors = []
                try:
                    all_files = os.listdir(SAVE_DIR)
                    prefix_lower = prefix.lower()
                    for f in all_files:
                        name_lower = f.lower()
                        if name_lower.startswith(prefix_lower) and (
                            name_lower.endswith('.jpg') or
                            name_lower == prefix_lower + '.hevc' or
                            name_lower == prefix_lower + '.h265' or
                            name_lower == prefix_lower + '.mp4'
                        ):
                            filepath = SAVE_DIR + f
                            try:
                                os.remove(filepath)
                                deleted += 1
                                print(f"删除文件: {f}")
                            except Exception as e2:
                                errors.append(f)
                except Exception as e2:
                    errors.append(str(e2))

                if deleted > 0:
                    body = f"视频 '{prefix}' 已删除 ({deleted} 个文件)".encode('utf-8')
                    response = f"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                else:
                    body = f"未找到视频 '{prefix}' 的文件".encode('utf-8')
                    response = f"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                conn.send(response)
            except Exception as e:
                body = f"Error: {e}".encode('utf-8')
                response = f"HTTP/1.1 500 Internal Server Error\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                conn.send(response)

        elif path.startswith('/delete/'):
            try:
                filename = path[8:].split('?')[0]
                try:
                    import urllib
                    filename = urllib.parse.unquote(filename)
                except:
                    pass

                filepath = SAVE_DIR + filename

                if not os.path.abspath(filepath).startswith(os.path.abspath(SAVE_DIR)):
                    body = b"Invalid file path"
                    response = f"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                    conn.send(response)
                elif os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                        print(f"删除文件: {filename}")
                        body = f"文件 '{filename}' 已删除".encode('utf-8')
                        response = f"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                        conn.send(response)
                    except Exception as e:
                        body = f"删除失败: {e}".encode('utf-8')
                        response = f"HTTP/1.1 500 Internal Server Error\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                        conn.send(response)
                else:
                    body = b"File not found"
                    response = f"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                    conn.send(response)
            except Exception as e:
                body = f"Error: {e}".encode('utf-8')
                response = f"HTTP/1.1 500 Internal Server Error\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                conn.send(response)


        elif path.startswith('/play/'):
            try:
                filename = path[6:].split('?')[0]
                try:
                    import urllib
                    filename = urllib.parse.unquote(filename)
                except:
                    pass

                filepath = SAVE_DIR + filename
                ext = os.path.splitext(filename)[1].lower()

                if not os.path.abspath(filepath).startswith(os.path.abspath(SAVE_DIR)):
                    body = b"Invalid file path"
                    response = f"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                    conn.send(response)
                elif not os.path.exists(filepath):
                    body = b"File not found"
                    response = f"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                    conn.send(response)
                else:
                    if ext in ['.hevc', '.h265', '.h264']:
                        player_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>视频播放 - {filename}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #1a1a1a; color: white; }}
        h1 {{ color: #fff; }}
        .video-container {{
            max-width: 1280px;
            margin: 20px auto;
            background: #000;
            border-radius: 8px;
            overflow: hidden;
        }}
        video {{ width: 100%; display: block; }}
        .notice {{
            background: #ff9800;
            color: #000;
            padding: 15px;
            border-radius: 8px;
            margin: 20px auto;
            max-width: 800px;
        }}
        .notice h3 {{ margin-top: 0; }}
        .notice ul {{ margin-bottom: 0; padding-left: 20px; }}
        .back-btn {{
            background: #0066cc;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
            margin-bottom: 20px;
        }}
        .back-btn:hover {{ background: #0052a3; }}
    </style>
</head>
<body>
    <a href="/" class="back-btn">返回文件列表</a>
    <h1>{filename}</h1>

    <div class="video-container">
        <video id="videoPlayer" controls>
            <source src="/file/{filename}" type="video/mp4">
            您的浏览器不支持 video 标签
        </video>
    </div>

    <div class="notice" id="notice" style="display: none;">
        <h3>⚠️ 提示：浏览器可能无法直接播放此格式</h3>
        <p>视频文件格式: <strong>{ext}</strong></p>
        <p>建议：</p>
        <ul>
            <li><strong>方法1（推荐）：</strong>下载视频文件，使用 <a href="https://www.videolan.org/" style="color: #000; text-decoration: underline;">VLC 播放器</a> 打开</li>
            <li><strong>方法2：</strong>在支持的浏览器中尝试播放（如Chrome最新版可能支持HEVC）</li>
            <li><strong>方法3：</strong>使用 <a href="/file/{filename}?download=1" style="color: #000; text-decoration: underline;">下载视频</a> 后播放</li>
        </ul>
    </div>

    <script>
        var video = document.getElementById('videoPlayer');
        video.addEventListener('error', function() {{
            document.getElementById('notice').style.display = 'block';
        }});

        video.addEventListener('loadedmetadata', function() {{
            console.log('视频加载成功');
        }});

        var playPromise = video.play();
        if (playPromise !== undefined) {{
            playPromise.then(function() {{
                console.log('视频开始播放');
            }}).catch(function(error) {{
                document.getElementById('notice').style.display = 'block';
            }});
        }}
    </script>
</body>
</html>"""
                    else:
                        player_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>视频播放 - {filename}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #1a1a1a; color: white; }}
        h1 {{ color: #fff; }}
        .video-container {{
            max-width: 1280px;
            margin: 20px auto;
            background: #000;
            border-radius: 8px;
            overflow: hidden;
        }}
        video {{ width: 100%; display: block; }}
        .back-btn {{
            background: #0066cc;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
            margin-bottom: 20px;
        }}
        .back-btn:hover {{ background: #0052a3; }}
    </style>
</head>
<body>
    <a href="/" class="back-btn">返回文件列表</a>
    <h1>{filename}</h1>

    <div class="video-container">
        <video id="videoPlayer" controls autoplay>
            <source src="/file/{filename}" type="video/mp4">
            您的浏览器不支持 video 标签
        </video>
    </div>
</body>
</html>"""

                    body = player_html.encode('utf-8')
                    response = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                    conn.send(response)
            except Exception as e:
                body = f"Error: {e}".encode('utf-8')
                response = f"HTTP/1.1 500 Internal Server Error\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                conn.send(response)

        elif path.startswith('/play-seq/'):
            try:
                prefix = path[10:].split('?')[0]
                try:
                    import urllib
                    prefix = urllib.parse.unquote(prefix)
                except:
                    pass

                seq_files = []
                try:
                    all_files = os.listdir(SAVE_DIR)
                    prefix_lower = prefix.lower()
                    prefix_len = len(prefix)
                    for f in all_files:
                        name_lower = f.lower()
                        if not name_lower.endswith('.jpg'):
                            continue
                        if not name_lower.startswith(prefix_lower):
                            continue
                        rest = f[prefix_len:]
                        if not rest.startswith('_'):
                            continue
                        rest = rest[1:]
                        if not rest.lower().endswith('.jpg'):
                            continue
                        num_part = rest[:-4]
                        if len(num_part) >= 4 and num_part.isdigit():
                            seq_files.append(f)
                    seq_files.sort()
                except:
                    pass

                if not seq_files:
                    all_files_str = ""
                    try:
                        all_files = os.listdir(SAVE_DIR)
                        all_files.sort(reverse=True)
                        all_files_str = "<br>".join(all_files[:50])
                    except:
                        all_files_str = "(无法读取目录)"

                    diag_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>无播放帧 - {prefix}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #1a1a1a; color: white; }}
        h2 {{ color: #e74c3c; }}
        .info {{ background: #333; padding: 15px; border-radius: 8px; margin: 10px 0; }}
        .file-list {{ background: #222; padding: 15px; border-radius: 8px; max-height: 400px; overflow-y: auto; font-size: 13px; font-family: monospace; }}
        .back-btn {{ color: white; text-decoration: none; padding: 10px 20px; background: #0066cc; border-radius: 4px; display: inline-block; margin-bottom: 20px; }}
    </style>
</head>
<body>
    <a href="/" class="back-btn">返回文件列表</a>
    <h2>未找到可播放的帧文件</h2>
    <div class="info">
        <p><strong>搜索前缀:</strong> {prefix}</p>
        <p><strong>搜索目录:</strong> {SAVE_DIR}</p>
        <p><strong>匹配模式:</strong> {{prefix}}_0000.jpg (4位数字序号)</p>
    </div>
    <p>目录中实际存在的文件 (前50个):</p>
    <div class="file-list">{all_files_str if all_files_str else "(空目录)"}</div>
    <p style="color:#e67e22;margin-top:20px;">
        如果看到 .hevc/.jpg 文件但无法播放，可能是因为录制时编码器未写入帧数据。
    </p>
</body>
</html>"""

                    body = diag_html.encode('utf-8')
                    response = f"HTTP/1.1 404 Not Found\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                    conn.send(response)
                else:
                    frame_count = len(seq_files)
                    frame_urls = ['/file/' + f for f in seq_files]
                    frame_urls_json = json.dumps(frame_urls)

                    player_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>序列播放 - {prefix}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 0; background: #1a1a1a; color: white; text-align: center; }}
        h1 {{ color: #fff; padding: 10px; font-size: 16px; margin: 0; }}
        .player-container {{
            max-width: 1280px;
            margin: 0 auto;
            position: relative;
        }}
        img {{ width: 100%; display: block; }}
        .controls {{
            padding: 15px;
            background: #222;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 15px;
            flex-wrap: wrap;
        }}
        .controls button {{
            background: #0066cc;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 14px;
        }}
        .controls button:hover {{ background: #0052a3; }}
        .controls button:disabled {{ background: #555; cursor: not-allowed; }}
        .controls .play-btn {{ background: #27ae60; width: 80px; }}
        .controls .play-btn:hover {{ background: #219a52; }}
        .controls .play-btn.paused {{ background: #e67e22; }}
        .controls label {{ font-size: 14px; }}
        .controls input[type="range"] {{ width: 100px; }}
        .frame-info {{ font-size: 14px; color: #aaa; min-width: 100px; }}
        .back-btn {{
            color: white;
            text-decoration: none;
            padding: 10px 20px;
            background: #444;
            border-radius: 4px;
            display: inline-block;
            margin: 10px;
        }}
        .back-btn:hover {{ background: #555; }}
        .loading {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%); font-size: 24px; color: #fff; }}
    </style>
</head>
<body>
    <a href="/" class="back-btn">返回文件列表</a>
    <h1>{prefix} (共 {frame_count} 帧, 10fps)</h1>

    <div class="player-container">
        <div class="loading" id="loading">加载中...</div>
        <img id="frame" src="" alt="frame" style="display:none;">
    </div>

    <div class="controls">
        <button class="play-btn paused" id="playBtn" onclick="togglePlay()">播放</button>
        <button onclick="prevFrame()" title="上一帧">|◀</button>
        <button onclick="nextFrame()" title="下一帧">▶|</button>
        <span class="frame-info" id="frameInfo">0 / {frame_count}</span>
        <input type="range" id="seekBar" min="0" max="{frame_count - 1}" value="0" oninput="seekTo(this.value)">
        <label>速度:</label>
        <select id="speedSelect" onchange="changeSpeed(this.value)">
            <option value="200">5fps</option>
            <option value="100" selected>10fps</option>
            <option value="67">15fps</option>
            <option value="50">20fps</option>
            <option value="33">30fps</option>
        </select>
    </div>

    <script>
        var frames = {frame_urls_json};
        var currentFrame = 0;
        var playing = false;
        var timer = null;
        var intervalMs = 100;
        var loadedCount = 0;

        var preloaded = new Array(frames.length);

        function preloadImages() {{
            for (var i = 0; i < frames.length; i++) {{
                var img = new Image();
                img.onload = (function(idx) {{
                    return function() {{
                        loadedCount++;
                        if (loadedCount >= frames.length) {{
                            document.getElementById('loading').style.display = 'none';
                            document.getElementById('frame').style.display = 'block';
                        }}
                    }};
                }})(i);
                img.src = frames[i];
                preloaded[i] = img;
            }}
        }}

        function showFrame(idx) {{
            if (idx < 0) idx = 0;
            if (idx >= frames.length) idx = frames.length - 1;
            currentFrame = idx;
            document.getElementById('frame').src = frames[idx];
            document.getElementById('seekBar').value = idx;
            document.getElementById('frameInfo').textContent = (idx + 1) + ' / ' + frames.length;
        }}

        function togglePlay() {{
            playing = !playing;
            var btn = document.getElementById('playBtn');
            if (playing) {{
                btn.textContent = '暂停';
                btn.classList.remove('paused');
                playLoop();
            }} else {{
                btn.textContent = '播放';
                btn.classList.add('paused');
                clearTimeout(timer);
            }}
        }}

        function playLoop() {{
            if (!playing) return;
            if (currentFrame >= frames.length - 1) {{
                currentFrame = 0;
            }} else {{
                currentFrame++;
            }}
            showFrame(currentFrame);
            timer = setTimeout(playLoop, intervalMs);
        }}

        function nextFrame() {{
            playing = false;
            var btn = document.getElementById('playBtn');
            btn.textContent = '播放';
            btn.classList.add('paused');
            clearTimeout(timer);
            showFrame(currentFrame + 1);
        }}

        function prevFrame() {{
            playing = false;
            var btn = document.getElementById('playBtn');
            btn.textContent = '播放';
            btn.classList.add('paused');
            clearTimeout(timer);
            showFrame(currentFrame - 1);
        }}

        function seekTo(idx) {{
            var wasPlaying = playing;
            playing = false;
            clearTimeout(timer);
            showFrame(parseInt(idx));
            if (wasPlaying) {{
                playing = true;
                playLoop();
            }}
        }}

        function changeSpeed(val) {{
            intervalMs = parseInt(val);
            document.getElementById('speedSelect').value = val;
        }}

        showFrame(0);
        preloadImages();
    </script>
</body>
</html>"""

                    body = player_html.encode('utf-8')
                    response = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                    conn.send(response)
            except Exception as e:
                body = f"Error: {e}".encode('utf-8')
                response = f"HTTP/1.1 500 Internal Server Error\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                conn.send(response)

        elif path.startswith('/api/set-time'):
            try:
                t_param = ""
                if '?' in path:
                    qs = path.split('?', 1)[1]
                    for param in qs.split('&'):
                        if '=' in param and param.startswith('t='):
                            t_param = param[2:]
                            break
                if t_param:
                    save_beijing_time(t_param)
                    print(f"浏览器设置时间: {t_param}")
                    body = b"OK"
                else:
                    body = b"Missing t parameter"
            except:
                body = b"Error"
            response = f"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
            conn.send(response)

        elif path == '/api/sequences':
            try:
                sequences = get_jpeg_sequences()
                body = json.dumps(sequences).encode()
            except:
                body = b"{}"
            response = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
            conn.send(response)

        elif path == '/api/status':
            try:
                body = json.dumps(detection_status).encode()
            except:
                body = b'{"state":"unknown"}'
            response = f"HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
            conn.send(response)

        elif path.startswith('/snapshot.jpg'):
            try:
                jpg_data = latest_frame_jpg
                if jpg_data:
                    response = f"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpg_data)}\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n".encode() + jpg_data
                    conn.send(response)
                else:
                    body = b"Camera not ready"
                    response = f"HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                    conn.send(response)
            except Exception as e:
                body = f"Error: {e}".encode()
                response = f"HTTP/1.1 500 Internal Server Error\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                conn.send(response)


        else:
            body = b"Not Found"
            response = f"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
            conn.send(response)

    except Exception as e:
        print(f"处理请求异常: {e}")
    finally:
        conn.close()

def http_server_main():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('0.0.0.0', HTTP_PORT))
        s.listen(5)
        print(f"HTTP服务器启动: http://0.0.0.0:{HTTP_PORT}")

        while True:
            try:
                conn, addr = s.accept()
                handle_request(conn, addr)
            except Exception as e:
                print(f"接受连接异常: {e}")
    except Exception as e:
        print(f"HTTP服务器启动失败: {e}")

def start_http_server():
    try:
        _thread.start_new_thread(http_server_main, ())
        print("HTTP服务器线程已启动")
    except Exception as e:
        print(f"启动HTTP服务器线程失败: {e}")

def get_ip():
    try:
        wlan = network.WLAN(network.STA_IF)
        if wlan.active():
            return wlan.ifconfig()[0]                                                                                                                                                                      
    except:
        pass
    return None

# ========== WiFi连接 ==========
def connect_wifi(ssid, password, timeout_sec=30, status_cb=None):
    """连接WiFi网络，返回IP地址。自动探测可用的WiFi API"""
    def log(msg):
        print(msg)
        if status_cb:
            try:
                status_cb(msg)
            except:
                pass

    log(f"WiFi: {ssid}...")

    _attrs = [a for a in dir(network) if not a.startswith('_')]
    log(f"Attrs: {_attrs}")

    _wifi_cls = None
    for _name in ['WLAN', 'WiFi', 'wifi', 'Wifi', 'Station', 'STA', 'NIC']:
        if _name in _attrs:
            _wifi_cls = getattr(network, _name)
            log(f"Use: network.{_name}")
            break

    if _wifi_cls:
        try:
            _wlan = _wifi_cls(network.STA_IF)
            _wlan.active(True)
            time.sleep_ms(500)
            if not _wlan.isconnected():
                _wlan.connect(ssid, password)
                for _i in range(timeout_sec * 2):
                    if _wlan.isconnected():
                        _ip = _wlan.ifconfig()[0]
                        log(f"OK: {_ip}")
                        return _ip
                    time.sleep_ms(500)
            else:
                _ip = _wlan.ifconfig()[0]
                log(f"OK: {_ip}")
                return _ip
            log("timeout")
            return None
        except Exception as e:
            log(f"err: {e}")

    try:
        from maix import wifi as _mw
        _mw.connect(ssid, password)
        time.sleep_ms(5000)
        _ip = _mw.get_ip()
        if _ip:
            log(f"maix.wifi OK: {_ip}")
            return _ip
    except Exception as e:
        log(f"maix.wifi: {e}")

    log("Try: wpa_supplicant...")
    try:
        _conf = 'ctrl_interface=/var/run/wpa_supplicant\nnetwork={\n    ssid="' + ssid + '"\n    psk="' + password + '"\n}\n'
        with open('/tmp/wpa.conf', 'w') as _f:
            _f.write(_conf)
        import os as _os
        _os.system('killall wpa_supplicant 2>/dev/null')
        time.sleep_ms(300)
        _os.system('wpa_supplicant -B -i wlan0 -c /tmp/wpa.conf')
        time.sleep_ms(2000)
        for _i in range(timeout_sec):
            _os.system('udhcpc -i wlan0 -t 2 -n -q 2>/dev/null')
            time.sleep_ms(1000)
            try:
                _out = _os.popen('ifconfig wlan0 2>/dev/null').read()
                _pos = _out.find('inet addr:')
                if _pos < 0:
                    _pos = _out.find('inet ')
                if _pos >= 0:
                    _rest = _out[_pos + 5:]
                    if _rest.startswith('addr:'):
                        _rest = _rest[5:]
                    _end = _rest.find(' ')
                    if _end > 0:
                        _ip = _rest[:_end]
                        log(f"wpa OK: {_ip}")
                        return _ip
            except:
                pass
        log("wpa timeout")
    except Exception as e:
        log(f"wpa: {e}")

    log("All failed")
    return None

# ========== NTP网络时间同步 ==========
NTP_SERVER = "ntp.aliyun.com"
_beijing_time_str = "auto_start"
_sync_device_time = 0
_sync_real_str = ""

def http_get(url):
    try:
        url = url.replace("http://", "")
        host, path = url.split("/", 1)
        path = "/" + path
        addr = socket.getaddrinfo(host, 80)[0][-1]
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(addr)
        s.send(f"GET {path} HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        resp = b""
        while True:
            chunk = s.recv(1024)
            if not chunk:
                break
            resp += chunk
        s.close()
        body_start = resp.find(b"\r\n\r\n")
        return resp[body_start + 4:] if body_start > 0 else resp
    except Exception as e:
        print(f"HTTP请求失败: {e}")
        return None

def sync_ntp_time():
    global _beijing_time_str, _sync_device_time, _sync_real_str

    try:
        import ntptime
        ntptime.host = NTP_SERVER
        ntptime.settime()
        t = time.localtime(int(time.time()) + 28800)
        if t[0] >= 2026:
            _time_str = f"{t[0]}-{t[1]}-{t[2]}-{t[3]}-{t[4]}-{t[5]}"
            _beijing_time_str = _time_str
            _sync_device_time = time.time()
            _sync_real_str = _time_str
            print(f"NTP时间同步成功: {_beijing_time_str}")
            return True
    except Exception as e:
        print(f"ntptime失败: {e}")

    try:
        body = http_get("http://worldtimeapi.org/api/timezone/Asia/Shanghai")
        if body:
            data = json.loads(body)
            dt = data.get("datetime", "")
            if "T" in dt:
                date_part, time_part = dt.split("T")
                time_part = time_part.split(".")[0]
                _time_str = date_part + "-" + time_part.replace(":", "-")
                _beijing_time_str = _time_str
                _sync_device_time = time.time()
                _sync_real_str = _time_str
                print(f"HTTP时间同步成功: {_beijing_time_str}")
                return True
    except Exception as e:
        print(f"HTTP时间同步失败: {e}")

    return False

def get_beijing_time_str():
    global _sync_device_time, _sync_real_str
    if _sync_device_time > 0 and _sync_real_str:
        elapsed = int(time.time() - _sync_device_time)
        if 0 < elapsed < 86400:
            return add_seconds_to_timestamp(_sync_real_str, elapsed)
        else:
            print(f"时间跳变检测: elapsed={elapsed}, 回退到缓存时间")
    return _beijing_time_str

def save_beijing_time(time_str):
    global _beijing_time_str, _sync_device_time, _sync_real_str
    _beijing_time_str = time_str
    _sync_device_time = time.time()
    _sync_real_str = time_str
    try:
        with open("/sd/beijing_time.txt", "w") as f:
            f.write(time_str)
        print(f"时间已同步: {time_str}")
    except:
        pass

def add_seconds_to_timestamp(ts_str, delta_sec):
    try:
        parts = list(map(int, ts_str.split('-')))
        if len(parts) != 6:
            raise ValueError("fmt")
        y, m, d, h, mi, s = parts
        s += int(delta_sec)
        if s >= 60:
            mi += s // 60
            s %= 60
        if mi >= 60:
            h += mi // 60
            mi %= 60
        if h >= 24:
            d += h // 24
            h %= 24
        return f"{y}-{m}-{d}-{h}-{mi}-{s}"
    except:
        return f"{ts_str}_{int(delta_sec):02d}"

try:
    os.mkdir(SAVE_DIR)
except OSError:
    pass

# ========== 初始化 ==========
_boot_msgs = ["", "", "", "", "", "", "", ""]
try:
    disp = display.Display()
    def _boot_status(msg):
        _boot_msgs.pop(0)
        _boot_msgs.append(str(msg)[:28])
        try:
            _img = image.Image(240, 240, image.Format.FMT_RGB888)
            _img.draw_string(5, 5, "MaixCAM Boot", color=image.COLOR_GREEN, scale=1.3)
            for _i, _l in enumerate(_boot_msgs):
                _img.draw_string(5, 28 + _i * 24, _l, color=image.COLOR_WHITE, scale=1)
            disp.show(_img)
        except:
            pass
    _boot_status("Booting...")
except Exception as e:
    print(f"屏幕初始化失败: {e}")
    disp = None
    def _boot_status(msg):
        print(f"[BOOT] {msg}")

wifi_ip = connect_wifi(WIFI_SSID, WIFI_PASSWORD, status_cb=_boot_status)

try:
    if os.path.exists("/sd/beijing_time.txt"):
        _beijing_time_str = open("/sd/beijing_time.txt", "r").read().strip()
        print(f"已从SD卡恢复时间: {_beijing_time_str}")
except:
    pass

sync_ntp_time()
start_http_server()

try:
    streamer = http.JpegStreamer()
    streamer.start()
    print(f"MJPEG推流已启动: http://0.0.0.0:8000/stream")
except Exception as e:
    print(f"MJPEG推流启动失败: {e}")
    streamer = None

print("=" * 40)
print("HTTP服务器已启动!")
device_http_url = ""
if wifi_ip:
    device_http_url = "http://" + wifi_ip + ":8080"
    print("WiFi访问地址: " + device_http_url)
else:
    ip = get_ip()
    if ip:
        device_http_url = "http://" + ip + ":8080"
        print("访问地址: " + device_http_url)
    else:
        print("未检测到网络连接")
        device_http_url = "No network"
print("=" * 40)

if disp:
    try:
        _img = image.Image(240, 240, image.Format.FMT_RGB888)
        _img.draw_string(10, 20, "MaixCAM Ready", color=image.COLOR_GREEN, scale=1.5)
        _img.draw_string(10, 55, "Auto-detecting...", color=image.COLOR_WHITE, scale=1)
        if device_http_url != "No network":
            _img.draw_string(10, 90, device_http_url, color=image.COLOR_YELLOW, scale=1)
        else:
            _img.draw_string(10, 90, "No network!", color=image.COLOR_RED, scale=1)
            _img.draw_string(10, 115, "SSID: " + WIFI_SSID, color=image.COLOR_WHITE, scale=1)
        disp.show(_img)
    except Exception as e:
        print("屏幕更新失败: " + str(e))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

MODEL_FILES = ["person.mud", "person.bin", "knife_detect.mud", "knife_detect.cvimodel", "gun_detect.mud", "gun_detect.cvimodel"]
SD_SOURCES = ["/sd/", "/sd/modules/"]
for _f in MODEL_FILES:
    _run_path = os.path.join(SCRIPT_DIR, _f)
    if os.path.exists(_run_path):
        continue
    for _sd_dir in SD_SOURCES:
        _sd_path = _sd_dir + _f
        try:
            if os.path.exists(_sd_path):
                _data = open(_sd_path, 'rb').read()
                with open(_run_path, 'wb') as _out:
                    _out.write(_data)
                print(f"已从SD卡复制: {_f} ({len(_data)} bytes)")
                break
        except Exception as _e:
            print(f"从SD卡复制 {_f} 失败: {_e}")

model_path = os.path.join(SCRIPT_DIR, "person.mud")

try:
    model = nn.YOLOv5(model=model_path, dual_buff=True)
    print("模型加载成功")
except:
    model = nn.YOLOv5(model="/root/models/yolov5s.mud", dual_buff=True)
    print("使用备用模型")

# 加载刀具检测模型（含安全自检）
knife_model_path = os.path.join(SCRIPT_DIR, "knife_detect.mud")
knife_model = None
try:
    knife_model = nn.YOLOv5(model=knife_model_path)
    _test_img = image.Image(knife_model.input_width(), knife_model.input_height(), knife_model.input_format())
    knife_model.detect(_test_img, conf_th=0.9, iou_th=0.9)
    print("刀具模型加载成功（已通过推理测试）")
except Exception as e:
    print(f"刀具模型加载/测试失败，已禁用: {e}")
    knife_model = None

# 加载设备检测模型（含安全自检）
device_model_path = os.path.join(SCRIPT_DIR, "gun_detect.mud")
device_model = None
try:
    device_model = nn.YOLOv5(model=device_model_path)
    _test_img = image.Image(device_model.input_width(), device_model.input_height(), device_model.input_format())
    device_model.detect(_test_img, conf_th=0.9, iou_th=0.9)
    print("设备模型加载成功（已通过推理测试）")
except Exception as e:
    print(f"设备模型加载/测试失败，已禁用: {e}")
    device_model = None

# ========== 状态变量 ==========
STATE_IDLE = 0
STATE_DETECTING = 1
STATE_RECORDING = 2

current_state = STATE_IDLE
state_start_time = 0
last_save_time = 0
saved_count = 0
batch_number = 0
cam = None
cam_yuv = None
disp = None
video_encoder = None
video_file_handle = None
current_video_file = None

timestamp_str = get_beijing_time_str()
print(f"当前北京时间: {timestamp_str}")
recording_prefix = ""
mission_start_time = 0
last_person_time = 0
person_start_time = 0
detect_start_time = 0
idle_start_time = 0          # 进入IDLE的时间戳，用于冷却期控制
recording_has_knife = False
recording_has_device = False
recording_is_loitering = False

# ========== 函数 ==========
def start_camera():
    global cam, cam_yuv, disp
    try:
        if cam is None:
            try:
                cam = camera.Camera(model.input_width(), model.input_height(), model.input_format())
            except:
                cam = camera.Camera(model.input_width(), model.input_height())
            if disp is None:
                disp = display.Display()
            print("RGB摄像头已开启（实时预览+检测）")
            return True
    except Exception as e:
        print(f"RGB摄像头开启失败: {e}")
    return False

def start_yuv_camera():
    """启动 YUV 摄像头（常驻模式：整个程序生命周期只启动一次，避免 VPSS 反复 deinit 导致驱动崩溃）"""
    global cam_yuv
    if cam_yuv is not None:
        return  # 已在运行
    try:
        cam_yuv = camera.Camera(1280, 720, image.Format.FMT_YVU420SP)
        print("YUV摄像头已开启（常驻，永不关闭）")
    except Exception as e:
        print(f"YUV摄像头启动失败: {e}")
        cam_yuv = None

def destroy_encoder():
    """安全销毁编码器（保留 VPSS/YUV 摄像头不动）"""
    global video_encoder
    if video_encoder:
        video_encoder = None
        gc.collect()
        time.sleep_ms(500)  # 等 VENC 驱动层完全退出
        print("编码器已安全销毁（VPSS 保持运行）")

def stop_yuv_camera():
    global cam_yuv
    try:
        if cam_yuv:
            cam_yuv.close()
            cam_yuv = None
            print("YUV摄像头已关闭")
    except:
        pass

def stop_camera():
    global cam, cam_yuv, disp
    try:
        if cam:
            cam.close()
            cam = None
        if cam_yuv:
            cam_yuv.close()
            cam_yuv = None
        disp = None
        print("摄像头已关闭")
    except:
        pass

_stream_frame_count = 0

def update_preview_frame(img):
    global latest_frame_jpg, streamer, _stream_frame_count
    try:
        jpg = img.to_jpeg()
        if streamer:
            streamer.write(jpg)
        latest_frame_jpg = jpg.to_bytes()
        _stream_frame_count += 1
    except Exception as e:
        pass

# ========== 主循环 ==========
def main_loop():
    global video_encoder, video_file_handle, current_video_file, cam, cam_yuv, disp, current_state
    global recording_has_knife, recording_has_device, recording_is_loitering
    global state_start_time, last_save_time, saved_count, batch_number
    global timestamp_str, recording_prefix, mission_start_time, last_person_time, person_start_time, detect_start_time, idle_start_time
    global streamer

    while not app.need_exit():
        current_time = time.time()

        # ===== STATE_IDLE =====
        if current_state == STATE_IDLE:
            try:
                img = cam.read()
                update_preview_frame(img)
                img.draw_string(5, 5, "STANDBY", color=image.COLOR_GREEN, scale=1.5)
                img.draw_string(5, 30, "Waiting...", color=image.COLOR_WHITE, scale=1)
                disp.show(img)
            except Exception as e:
                pass

            detection_status['state'] = 'idle'
            detection_status['person'] = False
            detection_status['knife'] = False
            detection_status['device'] = False
            detection_status['loitering'] = False
            detection_status['frames'] = 0
            detection_status['recording_prefix'] = ''

            # 冷却期：录制结束或检测超时后等待3秒再重新检测，避免死循环
            if current_time - idle_start_time < 3:
                time.sleep_ms(100)
                continue

            print("自动开始检测人体...")
            detect_start_time = current_time
            current_state = STATE_DETECTING
            batch_number += 1
            saved_count = 0
            last_save_time = 0
            print(f"开始检测人体（5秒超时）")

            time.sleep_ms(100)
            continue

        # ===== STATE_DETECTING =====
        elif current_state == STATE_DETECTING:
            if current_time - detect_start_time >= 5:
                print("5秒内未检测到人体，回到待机状态")
                current_state = STATE_IDLE
                idle_start_time = current_time
                continue

            try:
                img = cam.read()
            except:
                current_state = STATE_IDLE
                idle_start_time = current_time
                continue

            update_preview_frame(img)

            try:
                objs = model.detect(img, conf_th=CONF_TH, iou_th=IOU_TH)
            except:
                objs = []

            person_found = False
            for obj in objs:
                if obj.class_id == 0:
                    person_found = True
                    break

            detection_status['state'] = 'detecting'
            detection_status['person'] = person_found
            detection_status['knife'] = False
            detection_status['device'] = False
            detection_status['frames'] = 0
            detection_status['recording_prefix'] = ''

            if person_found:
                knife_detected = False
                if knife_model:
                    try:
                        knife_objs = knife_model.detect(img, conf_th=KNIFE_CONF_TH, iou_th=IOU_TH)
                        for obj in knife_objs:
                            if obj.class_id == 0:
                                knife_detected = True
                                break
                    except:
                        pass
                device_detected = False
                if device_model:
                    try:
                        device_objs = device_model.detect(img, conf_th=DEVICE_CONF_TH, iou_th=IOU_TH)
                        for obj in device_objs:
                            if obj.class_id == 0:
                                device_detected = True
                                break
                    except:
                        pass
                detected_items = []
                if knife_detected:
                    detected_items.append("刀具")
                if device_detected:
                    detected_items.append("设备")
                if detected_items:
                    print(f"检测到人体+{'、'.join(detected_items)}！开始录制")
                else:
                    print("检测到人体！开始录制")
                recording_has_knife = knife_detected
                recording_has_device = device_detected
                recording_is_loitering = False
                person_start_time = current_time
                last_person_time = current_time
                current_state = STATE_RECORDING
                last_save_time = 0
                saved_count = 0

            time.sleep_ms(50)
            continue

        # ===== STATE_RECORDING =====
        elif current_state == STATE_RECORDING:
            global video_encoder, video_file_handle, current_video_file, cam_yuv

            try:
                img = cam.read()
            except:
                if video_file_handle:
                    try:
                        video_file_handle.close()
                        print(f"视频保存完成: {current_video_file}")
                    except:
                        pass
                    video_file_handle = None
                destroy_encoder()  # 销毁编码器，但 YUV 摄像头/VPSS 保持不变
                current_state = STATE_IDLE
                idle_start_time = current_time
                continue

            if video_encoder is None:
                try:
                    start_yuv_camera()  # 首次调用会启动，后续调用立即返回
                    recording_prefix = get_beijing_time_str()
                    current_video_file = f"{SAVE_DIR}{recording_prefix}.hevc"
                    print(f"录制时间: {recording_prefix}")

                    bitrate = 5000000
                    time_base = 1000
                    framerate = 10
                    video_encoder = video.Encoder(bitrate=bitrate, time_base=time_base, framerate=framerate, width=1280, height=720)
                    video_file_handle = open(current_video_file, 'wb')
                    print(f"开始录制视频: {current_video_file}")
                except Exception as e:
                    print(f"视频编码器初始化失败: {e}")
                    video_encoder = None
                    video_file_handle = None

            try:
                objs = model.detect(img, conf_th=CONF_TH, iou_th=IOU_TH)
            except:
                objs = []

            person_found = False
            for obj in objs:
                if obj.class_id == 0:
                    person_found = True
                    img.draw_rect(obj.x, obj.y, obj.w, obj.h,
                                 color=image.COLOR_RED, thickness=2)
                    img.draw_string(obj.x, obj.y - 20, f'person:{obj.score:.2f}',
                                   color=image.COLOR_RED, scale=1.2)
                    break

            knife_detected = False
            if person_found and knife_model:
                try:
                    knife_objs = knife_model.detect(img, conf_th=KNIFE_CONF_TH, iou_th=IOU_TH)
                    for obj in knife_objs:
                        if obj.class_id == 0:
                            knife_detected = True
                            recording_has_knife = True
                            img.draw_rect(obj.x, obj.y, obj.w, obj.h,
                                         color=image.COLOR_YELLOW, thickness=2)
                            img.draw_string(obj.x, obj.y - 20, f'knife:{obj.score:.2f}',
                                           color=image.COLOR_YELLOW, scale=1.2)
                except:
                    pass

            device_detected = False
            if person_found and device_model:
                try:
                    device_objs = device_model.detect(img, conf_th=DEVICE_CONF_TH, iou_th=IOU_TH)
                    for obj in device_objs:
                        if obj.class_id == 0:
                            device_detected = True
                            recording_has_device = True
                            img.draw_rect(obj.x, obj.y, obj.w, obj.h,
                                         color=image.COLOR_BLUE, thickness=2)
                            img.draw_string(obj.x, obj.y - 20, f'device:{obj.score:.2f}',
                                           color=image.COLOR_BLUE, scale=1.2)
                except:
                    pass

            if person_found:
                last_person_time = current_time
                if current_time - person_start_time >= 20:
                    recording_is_loitering = True

            detection_status['state'] = 'recording'
            detection_status['person'] = person_found
            detection_status['knife'] = knife_detected or recording_has_knife
            detection_status['device'] = device_detected or recording_has_device
            detection_status['loitering'] = recording_is_loitering
            detection_status['frames'] = saved_count
            detection_status['recording_prefix'] = recording_prefix

            img.draw_string(5, 5, "REC", color=image.COLOR_RED, scale=2)
            img.draw_string(5, 30, f"Frames: {saved_count}", color=image.COLOR_GREEN, scale=1)
            _y = 55
            if recording_is_loitering:
                img.draw_string(5, _y, "LOITERING!", color=image.COLOR_RED, scale=2)
                _y += 25
            if knife_detected:
                img.draw_string(5, _y, "KNIFE!", color=image.COLOR_YELLOW, scale=2)
                _y += 25
            if device_detected:
                img.draw_string(5, _y, "DEVICE!", color=image.COLOR_BLUE, scale=2)
                _y += 25
            disp.show(img)

            update_preview_frame(img)

            if person_found and current_time - last_save_time >= 0.1:
                if video_encoder and video_file_handle and cam_yuv:
                    try:
                        img_yuv = cam_yuv.read()
                        frame = video_encoder.encode(img_yuv)
                        video_file_handle.write(frame.to_bytes())
                    except Exception as e:
                        print(f"视频编码失败: {e}")

                try:
                    jpg_filename = f"{SAVE_DIR}{recording_prefix}_{saved_count:04d}.jpg"
                    img.save(jpg_filename)
                except Exception as e:
                    print(f"JPEG保存失败: {e}")

                saved_count += 1
                if saved_count % 10 == 0:
                    print(f"已录制 {saved_count} 帧")
                last_save_time = current_time

            if not person_found and (current_time - last_person_time >= 3):
                print(f"人体消失3秒，停止录制，保存视频: {current_video_file}")
                try:
                    meta = {"knife": recording_has_knife, "device": recording_has_device, "loitering": recording_is_loitering}
                    meta_path = f"{SAVE_DIR}{recording_prefix}.meta"
                    with open(meta_path, "w") as _mf:
                        _mf.write(json.dumps(meta))
                    print(f"元数据已保存: {meta_path} (knife={recording_has_knife}, device={recording_has_device}, loitering={recording_is_loitering})")
                except Exception as _me:
                    print(f"元数据保存失败: {_me}")
                if video_file_handle:
                    try:
                        video_file_handle.close()
                        print(f"视频保存完成: {current_video_file}")
                    except:
                        pass
                    video_file_handle = None
                destroy_encoder()  # 销毁编码器，但 YUV 摄像头/VPSS 保持不变
                current_state = STATE_IDLE
                idle_start_time = current_time
                continue

    if video_file_handle:
        try:
            video_file_handle.close()
            print(f"视频保存完成: {current_video_file}")
        except:
            pass
        video_file_handle = None
    # 程序退出时才关闭常驻的编码器和 YUV 摄像头
    destroy_encoder()
    time.sleep_ms(500)  # 额外等待，确保驱动层清理完毕
    stop_yuv_camera()
    stop_camera()
    os.sync()

print("启动RGB摄像头（始终在线模式）...")
start_camera()
main_loop()
