# OpenSpec: 方案 A - 基于传统 CV 的车牌自适应角点检测与透视校正 (LPR-CORNER-SPEC-v1.0)

> **版本**: 1.0.0  
> **适用范围**: 针对仅输出轴对齐外接矩形 (HBB) 的 YOLOv8 模型，通过图像后处理自适应提取倾斜车牌的真实 4 个顶点，并进行透视拉平。

## 1. 算法流水线设计 (Algorithm Pipeline)

当 YOLOv8 给出一个车牌的 ROI (Region of Interest) 图像时，内部可能包含倾斜的车牌以及部分背景。提取真实角点的流水线如下：

1. **灰度化与平滑 (Grayscale & Blur)**
   - 转换 ROI 为单通道灰度图。
   - 使用高斯滤波 (GaussianBlur, $5\times5$ 核) 消除图像高频噪点。

2. **边缘检测 (Edge Detection)**
   - 使用 Canny 边缘检测算子 (低阈值 50, 高阈值 200)，提取车牌轮廓边缘线。

3. **形态学闭运算 (Morphological Closing)** *(关键鲁棒性增强)*
   - 车牌边框在图像中可能会断裂。使用膨胀 (Dilation) 后腐蚀 (Erosion) 的闭运算连接断裂的边缘，形成封闭的多边形。

4. **轮廓提取与排序 (Contour Extraction)**
   - 使用 `findContours` (模式 `RETR_EXTERNAL`) 提取外部轮廓。
   - 按轮廓面积 (Contour Area) 降序排列，取前 5 个最大轮廓。

5. **多边形逼近 (Polygon Approximation)**
   - 使用 `approxPolyDP` 算法对轮廓进行多边形逼近，精度参数 $\epsilon$ 设为轮廓周长的 `2% ~ 4%`。

6. **启发式过滤 (Heuristic Filtering)**
   - **顶点校验**: 逼近后的多边形必须恰好有 4 个顶点。
   - **凸包校验**: 该四边形必须是凸多边形 (`cv2.isContourConvex`)。
   - **面积校验**: 四边形面积必须大于整个 ROI 面积的 $30\%$，防止把车牌内部的某个字符识别为边框。

7. **后处理与降级 (Fallback)**
   - 如果找到符合条件的 4 个顶点，将其送入 `warp_plate` 进行单应性透视变换。
   - 如果所有轮廓都不符合条件（例如极端光照、车牌无边框），触发**降级机制**：直接使用原始 ROI 的 4 个角点进行原图返回。

## 2. 数据契约与接口

- **输入**: `plate_roi` (np.ndarray, BGR 格式的车牌局部截图)
- **输出**: `rectified_plate` (np.ndarray, 透视校正后的正向水平车牌图像)
