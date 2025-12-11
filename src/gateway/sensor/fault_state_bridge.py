"""传感器故障等级状态共享桥接模块。

用于在 `sensor` 目录下的采集 / 判级逻辑与 `alarm_rtu_ui` 之间传递各部位的 0/1/2/3 故障等级。

当前只实现振动速度类传感器的故障等级，可扩展到其他类型。

设计要点：
- 使用简单的全局字典 + 线程锁存储最近一次的故障等级。
- 由采集程序（如 `vibration_monitor_1.py`）周期性调用 `update_vibration_levels` 写入。
- 由报警 UI（如 `alarm_rtu_ui.py`）周期性调用 `get_latest_levels_for_alarm` 读取快照。
- 不强依赖 UI 或采集代码的存在，如果没有采集程序运行，读取结果为空字典。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, asdict
from typing import Dict, Mapping


@dataclass(frozen=True)
class SensorFaultLevel:
    """单个测点当前故障等级的快照。"""

    sensor_id: str  # 例如 "vib_crank_left"
    level: int  # 0/1/2/3
    sensor_type: str = "vibration"  # 目前仅 vibration，可扩展
    timestamp: float = 0.0  # POSIX 时间戳（秒）


# 全局存储：key 为 sensor_id，例如 "vib_crank_left"，value 为 SensorFaultLevel
_fault_levels: Dict[str, SensorFaultLevel] = {}
_lock = threading.Lock()

# 为跨进程共享，定义一个简单的 JSON 文件路径（相对工程根目录 src 之上的 gateway_RTU）
# 实际路径为：项目根目录下的 sensor_fault_levels.json
_JSON_FILENAME = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sensor_fault_levels.json")


def _save_to_json_unlocked() -> None:
    """在持有 _lock 的前提下，将 _fault_levels 持久化到 JSON 文件。

    JSON 结构示例：
        {
          "vib_crank_left": {"sensor_id": "vib_crank_left", "level": 2, "sensor_type": "vibration", "timestamp": 1730000000.0},
          ...
        }
    """

    try:
        data = {sid: asdict(snap) for sid, snap in _fault_levels.items()}
        with open(_JSON_FILENAME, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        # 失败时静默忽略，避免影响主流程；需要时可以改成打印日志
        pass


def _load_from_json() -> Dict[str, SensorFaultLevel]:
    """从 JSON 文件加载最近一次的故障等级快照，用于跨进程读取。"""

    if not os.path.exists(_JSON_FILENAME):
        return {}
    try:
        with open(_JSON_FILENAME, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}

    result: Dict[str, SensorFaultLevel] = {}
    if not isinstance(raw, dict):
        return {}
    for sensor_id, item in raw.items():
        try:
            level = int(item.get("level", 0))
            sensor_type = str(item.get("sensor_type", "vibration"))
            ts = float(item.get("timestamp", 0.0))
            result[sensor_id] = SensorFaultLevel(
                sensor_id=sensor_id,
                level=level,
                sensor_type=sensor_type,
                timestamp=ts,
            )
        except Exception:
            continue
    return result


def update_vibration_levels(levels: Mapping[str, int]) -> None:
    """由振动采集模块调用，批量更新多个测点的故障等级，并同步写入 JSON 文件。"""

    now = time.time()
    with _lock:
        for sensor_id, lvl in levels.items():
            lvl_clamped = max(0, min(3, int(lvl)))
            _fault_levels[sensor_id] = SensorFaultLevel(
                sensor_id=sensor_id,
                level=lvl_clamped,
                sensor_type="vibration",
                timestamp=now,
            )
        _save_to_json_unlocked()


def get_latest_levels_for_alarm() -> Dict[str, SensorFaultLevel]:
    """供报警 UI 调用，获取当前所有测点的故障等级快照（优先从 JSON 文件加载）。"""

    # 直接从 JSON 文件加载一份最新快照，避免依赖同一进程内存
    return _load_from_json()


# 映射到 alarm_rtu_ui / SensorState 所用的字段名
# 这里定义一个建议的默认映射关系，供 UI 侧使用：
#   sensor_id -> SensorState 字段名
DEFAULT_VIBRATION_TO_STATE_FIELD: Dict[str, str] = {
    "vib_crank_left": "crank_left_level",
    "vib_crank_right": "crank_right_level",
    "vib_tail_bearing": "tail_bearing_level",
    "vib_mid_bearing": "mid_bearing_level",
}


def map_to_state_fields(levels: Mapping[str, SensorFaultLevel]) -> Dict[str, int]:
    """根据默认映射关系，将测点 ID 映射为 SensorState 字段名 -> level。

    例如：{"vib_crank_left": SensorFaultLevel(..., level=2)}
    -> {"crank_left_level": 2}
    """

    result: Dict[str, int] = {}
    for sensor_id, snap in levels.items():
        field = DEFAULT_VIBRATION_TO_STATE_FIELD.get(sensor_id)
        if not field:
            continue
        result[field] = snap.level
    return result

