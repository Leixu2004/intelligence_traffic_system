# 车辆检测公开数据集 · 资源清单与转换工具

> 配套文档：`docs/datasets/车辆检测数据集资源与接入指南.md`（含视角选择、项目模块映射、合规说明）。
> 本目录脚本用于把《智慧交通车辆检测数据集与视角选择指南》推荐的四个公开数据集，
> 转换成本项目可直接训练 / 直接读取的格式。链接核验时间：**2026-09-16**。

## 1. 资源清单（按对本项目的优先级排序）

本项目是**路侧固定机位**的车辆检测 + 车牌识别 + 违章/流量系统，车辆检测器当前用官方
COCO 版 `yolov8n.pt`（`config.py` 中 `VEHICLE_CLASSES=[2,3,5,7]`）。因此优先级如下：

| 优先级 | 数据集 | 视角 | 体量（官方） | 标注/格式 | 许可 | 在本项目的用途 |
|---|---|---|---|---|---|---|
| ★★★ | **UA-DETRAC** | 路侧固定（北京/天津，24 路口，960×540@25fps） | 100 段视频、14 万+ 帧、121 万框；训练图 5.22GB/60 段、测试图 3.94GB/40 段 | 序列 XML（v1 四类 car/bus/van/others；**v3 增六车型+颜色**）、天气/光照/遮挡 | CC BY-NC-SA 3.0 | **微调车辆检测器主力数据**；多目标跟踪/计数；雨雾夜鲁棒性 |
| ★★☆ | **CitySim** (UCF-SST) | 无人机俯视（12 地点，30fps，约 19 小时） | 1140 分钟视频轨迹，旋转框+速度+车道 | 轨迹 CSV（frameNum/carId/四角点/speed/laneId） | **申请制**，教学/研究 | 合流/分流/交织安全事件；轨迹流量进 LSTM；数字孪生(SUMO/CARLA) |
| ★★☆ | **BDD100K** | 车载前视（但天气/光照最全） | 10 万张检测图（7 万训/1 万验/2 万测），约 9.5GB，约 147 万框 | JSON（box2d，10 类，含天气/时段标签） | 教育/研究/非营利免费；商用入 BDD/BAIR Commons | 夜间/雨天难例**增强**；红绿灯/交通标志类别扩展 |
| ★☆☆ | **KITTI** | 车载前视 + 激光雷达（德国） | 目标检测 7481 训练图（Ultralytics 切分 5985/1496） | 2D/3D 框、8 类、LiDAR | CC BY-NC-SA 3.0 | 仅在扩展车载/ADAS、多模态融合时使用 |

> 视角结论：**当前固定路侧业务首选 UA-DETRAC；轨迹/安全分析用 CitySim；BDD100K 只做增强；
> KITTI 留给未来车载方向。** 车载视角数据与路侧机位存在域差，不要用车载数据替代主力域数据。

## 2. 下载地址

### UA-DETRAC（路侧，最匹配）
- 官网/下载页（需免费注册登录后下载；旧的 `/Data/*.zip` 直链已 301 迁移）：
  - https://detrac-db.rit.albany.edu/download
  - https://detrac-db.rit.albany.edu/Detection
  - 务必下载 **DETRAC-Train-Images** 与 **DETRAC-Train-Annotations-XML-v3**（v3 才有细分车型）。
- 免注册镜像（任选其一，遵守原许可）：
  - 完整原始数据（Kaggle，约 11GB）：https://www.kaggle.com/datasets/bratjay/ua-detrac-orig/data
  - 轻量 YOLO 子集（5 段、约 17MB，可先跑通流程）：https://www.kaggle.com/datasets/xyz6674/ua-detrac-custom
  - Roboflow Universe 搜索 “UA-DETRAC”，导出格式选 **YOLOv8**：https://universe.roboflow.com
- 论文：https://arxiv.org/abs/1511.04136

### CitySim（无人机轨迹，申请制）
- 仓库（含 wiki 字段说明、示例、工具）：https://github.com/ozheng1993/UCF-SST-CitySim-Dataset
- 字段文档：仓库 wiki「Home」；申请表在 `asset/MainPage/Data_Request_Form.pdf`，
  填好发 **citysim.ucfsst@gmail.com**，审核通过后获得完整 CSV。
- 论文：https://arxiv.org/abs/2208.11036 ；配套冲突识别工具 A.R.C.I.S：https://github.com/ozheng1993/A-R-C-I-S

### BDD100K（天气/光照最全）
- 官网用户门户（免费注册）：https://bdd-data.berkeley.edu （下载 `100K Images` + `Detection 2020 Labels`）
- 免注册替代：
  - Internet Archive 镜像：https://archive.org/details/bdd100k （`bdd100k_images.zip`、`bdd100k_labels.zip`）
  - 已转 YOLO 的 Kaggle 镜像：https://www.kaggle.com/datasets/paulmaxencebaraton/bdd100k-for-yolov5/data
  - FiftyOne 数据集库：https://docs.voxel51.com/dataset_zoo/datasets/bdd100k.html
- 论文：https://arxiv.org/abs/1805.04687

### KITTI（车载/多模态，未来方向）
- 官网：https://www.cvlibs.net/datasets/kitti/ （目标检测 `data_object_image2` + `data_object_label_2`）
- **最省事方式**：Ultralytics 已内置 KITTI（自动下载、自动转 YOLO，5985/1496，8 类）：
  ```bash
  yolo train data=kitti.yaml model=yolov8n.pt epochs=50 imgsz=640
  ```
  文档：https://docs.ultralytics.com/datasets/detect/kitti/

## 3. 转换与训练（本目录脚本）

脚本均为纯标准库 / pandas，已用合成样本端到端验证。建议在项目虚拟环境
`intel-transportation/.venv` 中运行。

### 3.1 UA-DETRAC → YOLO → 微调车辆检测器（推荐主线）
```powershell
# 1) 转换（v3 标注；图片默认硬链接进标准目录，几乎不额外占空间）
python detrac_to_yolo.py `
  --xml-root   D:/datasets/DETRAC/Train-Annotations-XML-v3 `
  --images-root D:/datasets/DETRAC/DETRAC-train-data `
  --out D:/datasets/DETRAC/yolo_detrac `
  --classes project4 --val-ratio 0.1 --link hardlink

# 2) 微调 + 自动导出 ONNX(opset18)
python train_vehicle_detector.py --data D:/datasets/DETRAC/yolo_detrac/dataset.yaml `
  --weights yolov8n.pt --epochs 80 --imgsz 960 --batch 16 `
  --project runs --name vehicle_detrac
```
- `--classes`：`project4`(默认，car/van/bus/truck，对齐本项目)、`detrac4`(官方四类)、`vehicle`(单类)。
- 按**序列**切分 train/val，避免同视频相邻帧跨集泄漏；`ignored_region` 不出框；`out_of_view` 框自动剔除。
- v1 XML 不含每车细分类别（脚本会归到默认类 car）；要区分 van/truck 请用 **v3** XML。

### 3.2 BDD100K → YOLO（夜间/雨天增强、红绿灯扩展）
```powershell
python bdd_to_yolo.py `
  --labels D:/datasets/bdd100k/labels/bdd100k_labels_images_train.json `
  --labels D:/datasets/bdd100k/labels/bdd100k_labels_images_val.json `
  --images-root D:/datasets/bdd100k/images/100k `
  --out D:/datasets/bdd100k/yolo_night_rain `
  --vehicle-only --timeofday night --weather rainy
# 去掉 --timeofday/--weather 即全量；去掉 --vehicle-only 保留官方 10 类（含 traffic light/sign）
```

### 3.3 CitySim → 项目 60 秒流量 CSV（进 LSTM 数据链）
```powershell
python citysim_to_flow.py --input D:/datasets/CitySim `
  --out ../lstm_sources/processed/citysim --bin-seconds 60 --with-speed
```
产出与 `backend/prediction/data.py` 聚合帧契约同构：
`series_id,bucket_start_seconds,bin_seconds,vehicle_count,entering_vehicle_count,source_file`
（`vehicle_count`=桶内活跃唯一车辆，`entering_vehicle_count`=首帧进入车辆；另输出 per_series 分文件与车速统计。）

## 4. 合规红线
- UA-DETRAC、KITTI 为 **CC BY-NC-SA 3.0**：须署名、**仅限非商业**、衍生作品同协议共享。
- BDD100K 数据**教育/研究/非营利免费**，商业用途须加入 BDD/BAIR Commons。
- CitySim 为**申请制**，按 UCF-SST 协议用于学术研究，不得公开再分发原始数据。
- 以上数据均**不能直接用于商业生产或对外再分发**；实训/课程/研究可用，正式上线须改用有授权的现场数据。
