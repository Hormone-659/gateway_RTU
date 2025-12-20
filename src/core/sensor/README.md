# core/sensor 目录说明

存放传感器相关的核心逻辑，包括：数据模型、单位转换、阈值判断等。

## 文件说明

- `vibration_model.py`
  - 定义振动传感器数据结构：
    - `VibrationSample`：描述单轴振动采样（原始寄存器值 + 换算后的工程量 + 单位）。
    - `LocationAxesSample`：描述一个安装位置的三轴数据（vx/vy/vz）。
  - 提供单位转换函数：
    - `raw_to_speed(raw, scale=None)`：将 Modbus 寄存器原始值转换为振动速度（mm/s），默认缩放系数为 `raw/10` (0.1)。
    - 这些函数可以被 UI 和后台服务共同使用，保持现场标定逻辑统一。

- `threshold_engine.py`
  - 对现有的 `gateway.sensor.threshold_analyzer` 做轻量封装，提供统一阈值判断接口：
    - `SimpleThresholdConfig`：保存三级阈值（level1/level2/level3）。
    - `SpeedThresholdEngine`：内部使用 `ThresholdConfig` 和 `MultiChannelThresholdAnalyzer`，对外提供：
      - `evaluate_xyz(vx, vy, vz) -> int`：三轴振动速度 → 0/1/2/3 故障等级 (取最大值)。
      - `evaluate_single(value) -> int`：单通道兼容接口。
  - 后台采集服务和 UI 都应通过这里来做“速度→阈值等级”的转换。

