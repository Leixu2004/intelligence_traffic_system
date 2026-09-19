# OpenSpec: 交通违章判定规则扩展架构 (LPR-VIOLATION-SPEC-v1.0)

> **版本**: 1.0.0  
> **适用范围**: 智慧交通系统违章逻辑层（压线、违停、超速等业务剥离与扩展）。

## 1. 架构目标
将违章判定逻辑从主程序解耦，提供标准化的规则引擎。每个违章场景对应一个 Checker 类，主程序只需将追踪目标的 `(x1, y1, x2, y2, vehicle_id)` 喂给 Checker，Checker 负责维护内部状态（如时间戳）并返回判定结果。

## 2. 违停检测逻辑 (Illegal Parking)
- **触发条件**: 车辆 BBox（或其中心点）与配置的“禁停区 (No Parking Zone)”发生交集。
- **倒计时追踪**: 
  - 以车辆的 `vehicle_id`（来自 YOLO Track）为 Key，记录首次进入禁停区的时间戳。
  - 每帧刷新计算 `当前时间 - 首次时间`。
- **违章判定**: 当停留时间 $\ge$ 设定的 `PARKING_THRESHOLD`（如 5 秒），且未被抓拍过，则抛出违章事件。
- **状态清理**: 如果车辆离开了禁停区，将其从追踪字典中移除，重置计时。

## 3. 标准接口设计
```python
class BaseViolationChecker:
    def check(self, bbox: list, vehicle_id: int) -> bool:
        pass
        
    def reset(self, vehicle_id: int):
        pass
```
