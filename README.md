# MaixCAM R329 智能安防监控系统

基于 **Sipeed MaixCAM R329** (RISC-V) 的 AI 智能监控系统，支持人体检测、刀具检测、枪支/设备检测，并自动录制视频和推流。

## 仓库结构

```
├── modules/                          # AI 模型文件
│   ├── person.bin                    # 人体检测模型权重 (YOLOv5, 12MB)
│   ├── person.mud                    # 人体检测模型描述符 (224×224)
│   ├── knife_detect.cvimodel         # 刀具检测模型权重 (YOLOv5, 7.1MB)
│   ├── knife_detect.mud              # 刀具检测模型描述符
│   ├── gun_detect.cvimodel           # 枪支/设备检测模型权重 (YOLOv5, 7.1MB)
│   └── gun_detect.mud                # 枪支/设备检测模型描述符
│
└── R329_person/R329_person/
    └── main.py                       # 设备端主程序 (MicroPython/MaixPy)
```

## 功能特性

### 🎯 三重 AI 检测

| 检测类型 | 模型 | 置信度阈值 | 说明 |
|---------|------|-----------|------|
| 人体检测 | `person.mud` | 0.45 | YOLOv5, 224×224 RGB 输入 |
| 刀具检测 | `knife_detect.mud` | 0.78 | 检测到人体后触发 |
| 枪支/设备检测 | `gun_detect.mud` | 0.80 | 检测到人体后触发 |

### 🔄 有限状态机 (FSM)

```
IDLE (待机) → DETECTING (检测中, 5秒窗口) → RECORDING (录制中) → IDLE
```

- **IDLE**: 上电自动进入检测，显示实时画面
- **DETECTING**: 5 秒内搜索人体目标，超时回 IDLE
- **RECORDING**: 发现人员自动录制，人员消失 3 秒后停止

### 📹 双摄像头录制

- **RGB 摄像头**: 模型原生分辨率，用于 AI 检测 + 屏幕预览
- **YUV 摄像头**: 1280×720，用于 HEVC 视频编码
- 录制帧率: 10fps，码率 5Mbps
- 同步保存 JPEG 帧序列 (`{时间戳}_{序号:04d}.jpg`)

### 🌐 内嵌 Web 管理后台

- HTTP 服务器运行在 **8080 端口**
- Vue 3 + Element Plus 现代化 UI
- 实时监控页面：MJPEG 视频流 + 检测状态面板 + 告警横幅
- 录像回放页面：文件浏览、序列播放、下载、删除
- 支持移动端适配 (safe-area-inset)
- 危险等级自动标注（人员持刀、持设备、长时间逗留）

### 📡 MJPEG 推流

- 实时视频流推送至 `http://<ip>:8000/stream`
- 可供其他设备或浏览器拉流观看

### ⏰ NTP 时间同步

- 开机自动通过 NTP (`ntp.aliyun.com`) 同步北京时间
- 备用 HTTP 时间同步 (`worldtimeapi.org`)
- 支持浏览器端手动校时
- 断网后通过 SD 卡持久化时间 + 内部计时推算

### 📡 UART 串口告警

- `/dev/ttyS0`, 115200 baud
- 检测到人员时发送 `warning\r\n`
- 人员消失时发送 `end\r\n`

### 🔌 WiFi 连接

- 支持多种连接方式 (MaixPy `network.WLAN` / `maix.wifi` / `wpa_supplicant`)
- 自动 fallback，最大兼容性

## 部署方式

### 方式一：MaixVision 安装

使用 [MaixVision](https://wiki.sipeed.com/maixvision/) 桌面工具安装 `dist/maix-unico-v1.0.0.zip`

### 方式二：手动部署

将以下文件复制到设备 app 目录：

```
main.py          # 主程序
app.yaml         # 应用清单
person.mud       # 人体模型描述符
person.bin       # 人体模型权重
knife_detect.mud # 刀具模型描述符
knife_detect.cvimodel  # 刀具模型权重
gun_detect.mud   # 枪支模型描述符
gun_detect.cvimodel    # 枪支模型权重
```

### 修改 Wi-Fi 配置

部署前修改 `main.py` 中的 Wi-Fi 凭据：

```python
WIFI_SSID = "你的WiFi名称"
WIFI_PASSWORD = "你的WiFi密码"
```

## 使用说明

1. 设备上电后自动连接 WiFi
2. 屏幕显示设备 IP 地址（如 `http://192.168.1.100:8080`）
3. **自动开始检测**（无需任何指令），检测到人员自动录制
4. 浏览器访问设备 IP 查看实时监控和录像回放
5. 录像文件保存在 SD 卡 `/sd/detections/` 目录

### Web 管理界面路由

| 路径 | 功能 |
|------|------|
| `/` | 主页（Vue SPA 管理后台） |
| `/api/status` | 获取检测状态 JSON |
| `/api/sequences` | 获取录像序列列表 |
| `/api/files` | 获取文件列表 |
| `/snapshot.jpg` | 获取当前帧快照 |
| `/file/<filename>` | 下载/查看文件 |
| `/play-seq/<prefix>` | 序列帧播放器 |
| `/delete-seq/<prefix>` | 删除整个录像序列 |

## 硬件平台

- **主控**: Sipeed MaixCAM R329 SOM (RISC-V 双核)
- **摄像头**: 双摄像头（RGB + YUV）
- **存储**: MicroSD 卡
- **网络**: 2.4G WiFi

## 技术栈

- **运行环境**: MicroPython / MaixPy
- **AI 框架**: MaixPy NN (YOLOv5)
- **视频编码**: HEVC (H.265) 硬件编码
- **前端**: Vue 3 + Element Plus (内嵌单文件 HTML)
- **后端**: 原生 Socket HTTP Server

## 许可证

GNU General Public License v3.0
