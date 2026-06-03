# MaixCAM R329 模型转换完整流程

本文档记录了 YOLOv5 模型从 PyTorch `.pt` 到 MaixCAM 可用的 `.cvimodel` + `.mud` 的完整转换过程。

## 环境要求

### 训练/导出环境 (x86 PC)
- Python 3.8+
- PyTorch + CUDA
- YOLOv5 仓库（本仓库 `yolov5/` 目录）
- onnx, onnxruntime

```bash
pip install -r yolov5/requirements.txt
pip install onnx onnx-simplifier onnxruntime
```

### 转换环境 (TPU-MLIR)
- TPU-MLIR 工具链（Sophgo 官方提供）
- 包含三个核心工具：`model_transform.py`、`run_calibration.py`、`model_deploy.py`

> 工具链安装参考：[TPU-MLIR](https://github.com/sophgo/tpu-mlir)

---

## 完整转换流水线

```
yolov5s.pt ──[export.py]──▶ best.onnx
                                  │
   ┌──────────────────────────────┘
   │  TPU-MLIR 工具链
   ├── ① model_transform.py   (ONNX → MLIR)
   ├── ② run_calibration.py   (生成 INT8 量化表)
   └── ③ model_deploy.py      (MLIR → cvimodel)
                                  │
                                  ▼
                          detect.cvimodel
                                  +
                          detect.mud  (手写描述符)
```

---

## 第一步：`.pt` → `.onnx`

使用 YOLOv5 官方的 `export.py` 将训练好的 PyTorch 模型导出为 ONNX 格式。

```bash
cd yolov5
python export.py \
  --weights ../model_convert/yolov5s.pt \
  --include onnx \
  --img-size 320 320
```

| 参数 | 说明 |
|------|------|
| `--weights` | 训练好的 `.pt` 权重路径 |
| `--include onnx` | 导出格式为 ONNX |
| `--img-size` | 模型输入尺寸，如 320×320 或 224×224 |

输出文件：`best.onnx`（约 28MB，FP32）

---

## 第二步：`.onnx` → `.mlir`（中间表示）

使用 TPU-MLIR 的 `model_transform.py`，将 ONNX 转为 MLIR 中间表示。

```bash
model_transform.py \
  --model_name knife_detect \
  --model_def ../best.onnx \
  --input_shapes [[1,3,320,320]] \
  --mean "0,0,0" \
  --scale "0.00392156862745098,0.00392156862745098,0.00392156862745098" \
  --keep_aspect_ratio \
  --pixel_format rgb \
  --channel_format nchw \
  --output_names "/model.24/m.0/Conv_output_0,/model.24/m.1/Conv_output_0,/model.24/m.2/Conv_output_0" \
  --mlir knife_detect.mlir
```

### 参数详解

| 参数 | 说明 |
|------|------|
| `--model_name` | 模型名称，自定义 |
| `--model_def` | 输入的 ONNX 文件路径 |
| `--input_shapes` | 输入尺寸，NCHW 格式，batch=1 |
| `--mean` | 均值，RGB 三通道填 0（不做均值减法） |
| `--scale` | 缩放因子，`1/255 ≈ 0.0039`，将像素值归一化到 [0,1] |
| `--pixel_format` | 输入图像格式，`rgb` |
| `--channel_format` | 张量通道布局，`nchw` |
| `--output_names` | YOLOv5 三个检测头的输出节点名（固定） |
| `--mlir` | 输出的 MLIR 文件名 |

> **output_names 说明**：YOLOv5 有三个检测头，对应 `/model.24/m.0/Conv_output_0`、`/model.24/m.1/Conv_output_0`、`/model.24/m.2/Conv_output_0`。这个名称是固定的，取决于 YOLOv5 模型结构。

---

## 第三步：生成 INT8 量化校准表

```bash
run_calibration.py knife_detect.mlir \
  --dataset ../images \
  --input_num 200 \
  -o knife_detect_cali_table
```

| 参数 | 说明 |
|------|------|
| `knife_detect.mlir` | 上一步生成的 MLIR 文件 |
| `--dataset` | 校准图片目录（200 张代表性图片） |
| `--input_num` | 校准用的图片数量 |
| `-o` | 输出的量化表文件名 |

> 校准图片建议使用训练集或实际场景的 200 张图片。这一步的目的是统计每一层的数值分布，用于 INT8 量化。

---

## 第四步：部署为 `.cvimodel`

```bash
model_deploy.py \
  --mlir knife_detect.mlir \
  --quantize INT8 \
  --quant_input \
  --calibration_table knife_detect_cali_table \
  --processor cv181x \
  --model knife_detect.cvimodel
```

| 参数 | 说明 |
|------|------|
| `--mlir` | MLIR 文件 |
| `--quantize INT8` | INT8 量化（大幅减小体积、加速推理） |
| `--quant_input` | 同时量化输入层 |
| `--calibration_table` | 上一步的量化表 |
| `--processor cv181x` | 目标芯片：CV181x（R329 的 NPU） |
| `--model` | 输出的 cvimodel 文件名 |

输出文件：`knife_detect.cvimodel`（约 7MB）

---

## 第五步：编写 `.mud` 模型描述符

`.mud` 是 MaixPy 加载模型所需的 INI 格式配置文件，告诉框架：用什么模型文件、模型类型、输入预处理参数、anchor 尺寸、标签名。

### 新版格式（cvimodel 类型）

用于 `knife_detect`、`gun_detect` 等：

```ini
[basic]
type = cvimodel
model = knife_detect.cvimodel

[extra]
model_type = yolov5
input_type = rgb
mean = 0, 0, 0
scale = 0.00392156862745098, 0.00392156862745098, 0.00392156862745098
anchors = 10,13, 16,30, 33,23, 30,61, 62,45, 59,119, 116,90, 156,198, 373,326
labels = knife
```

### 旧版格式（aipu 类型）

用于 `person` 检测：

```ini
[basic]
type = aipu
bin = person.bin
param =

[inputs]
input0 = 224,224,3,127.5, 127.5, 127.5,0.0078125, 0.0078125, 0.0078125

[outputs]
output0 = 7,7,30

[extra]
outputs_scale = 6.56008
inputs_scale=
```

### 字段说明

| 字段 | 说明 |
|------|------|
| `[basic]` | |
| `type` | 模型类型：`cvimodel`（新版）或 `aipu`（旧版） |
| `model` / `bin` | 模型权重文件路径 |
| `[extra]` | |
| `model_type` | 模型架构：`yolov5` |
| `input_type` | 输入颜色空间：`rgb` |
| `mean` | 均值，三通道，通常为 `0,0,0` |
| `scale` | 缩放因子，`1/255 ≈ 0.0039` |
| `anchors` | YOLOv5 的 anchor 尺寸（9 对，从小到大排列） |
| `labels` | 检测标签名（单类检测就一个词） |
| `[inputs]` | （旧格式）输入层：宽,高,通道数,均值,缩放值 |

---

## 完整脚本示例

以下是 `model_convert/` 中的实际脚本：

### convert.sh（刀具检测）

```bash
#!/bin/bash
set -e

MODEL_NAME="knife_detect"
INPUT_W=320
INPUT_H=320

# Step 1: ONNX -> MLIR
model_transform.py \
  --model_name ${MODEL_NAME} \
  --model_def ../best.onnx \
  --input_shapes [[1,3,${INPUT_H},${INPUT_W}]] \
  --mean "0,0,0" \
  --scale "0.00392156862745098,0.00392156862745098,0.00392156862745098" \
  --keep_aspect_ratio \
  --pixel_format rgb \
  --channel_format nchw \
  --output_names "/model.24/m.0/Conv_output_0,/model.24/m.1/Conv_output_0,/model.24/m.2/Conv_output_0" \
  --mlir ${MODEL_NAME}.mlir

# Step 2: Calibration
run_calibration.py ${MODEL_NAME}.mlir \
  --dataset ../images \
  --input_num 200 \
  -o ${MODEL_NAME}_cali_table

# Step 3: Deploy
model_deploy.py \
  --mlir ${MODEL_NAME}.mlir \
  --quantize INT8 \
  --quant_input \
  --calibration_table ${MODEL_NAME}_cali_table \
  --processor cv181x \
  --model ${MODEL_NAME}.cvimodel
```

---

## 模型对照表

| 模型 | 输入尺寸 | ONNX 大小 | cvimodel 大小 | 检测目标 |
|------|---------|-----------|---------------|---------|
| person | 224×224 | - | 12MB (.bin) | 人体 |
| knife_detect | 320×320 | 28MB | 7.1MB | 刀具 |
| gun_detect | 320×320 | 28MB | 7.1MB | 枪支/设备 |

> INT8 量化后体积缩小约 4 倍，NPU 推理速度也大幅提升。

---

## 常见问题

### 1. output_names 怎么确认？

可以用 Netron 打开 ONNX 文件，找到最后的三个 Conv 输出节点，名称通常为 `/model.24/m.0/Conv_output_0` 等。

### 2. 校准图片要多少张？

官方建议 100-500 张，200 张足以。图片应覆盖典型场景（不同光照、角度、背景）。

### 3. cvimodel 能在 PC 上测试吗？

不能，只能运行在 CV181x/CV180x NPU 上。PC 端可以用 `onnxruntime` 测试 `.onnx` 文件。

### 4. mud 新旧格式有什么区别？

- **旧版 `aipu`**：权重是 `.bin` 纯数据文件，预处理参数在 `[inputs]` 里
- **新版 `cvimodel`**：权重是 `.cvimodel` 打包格式（含量化信息），预处理参数在 `[extra]` 里

新版是 MaixPy 推荐的方式，更加简洁。
