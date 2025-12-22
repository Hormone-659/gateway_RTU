from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

# ================= 全局状态变量 =================

# 报警文件基础路径
BASE_ALARM_DIR = Path(__file__).parent / "alarm_level"

# 三级报警触发后用于 101 的计时（写 101=82 的触发时刻）
_g_101_trigger_start: float | None = None

# 记录 101 当前值与最近一次变更时间（用于 43501 只跟随 101 的 60 秒规则）
# 注意：这里存储的是代码“认为”的当前状态，或者从RTU回读的状态
_g_101_current: int | None = None
_g_101_changed_at: float | None = None

# 锁存的 43501 当前值（0/1）；None 表示尚未确定
_g_43501_latched: int | None = None


# ================= 数据结构与辅助函数 =================

@dataclass
class SensorState:
    belt_level: int = 0
    mid_bearing_level: int = 0
    tail_bearing_level: int = 0
    horsehead_level: int = 0
    crank_left_level: int = 0
    crank_right_level: int = 0
    line_level: int = 0
    elec_phase_a_ok: bool = True
    elec_phase_b_ok: bool = True
    elec_phase_c_ok: bool = True
    loadpos_ok: bool = True


def _any_sensor_reach_level(state: SensorState, level: int) -> bool:
    return any(
        getattr(state, name) >= level
        for name in [
            "crank_left_level", "crank_right_level", "tail_bearing_level",
            "mid_bearing_level", "horsehead_level", "belt_level",
        ]
    )


def _any_vibration_reach_level(state: SensorState, level: int) -> bool:
    return any(
        getattr(state, name) >= level
        for name in [
            "crank_left_level", "crank_right_level",
            "tail_bearing_level", "mid_bearing_level",
        ]
    )


def _belt_photoelectric_reach_level3(state: SensorState) -> bool:
    return state.belt_level >= 3


def _electrical_missing_count(state: SensorState) -> int:
    return sum([not state.elec_phase_a_ok, not state.elec_phase_b_ok, not state.elec_phase_c_ok])


def _electrical_missing_at_least_one(state: SensorState) -> bool:
    return _electrical_missing_count(state) >= 1


def _electrical_missing_at_least_two(state: SensorState) -> bool:
    return _electrical_missing_count(state) >= 2


def _electrical_all_ok(state: SensorState) -> bool:
    return _electrical_missing_count(state) == 0


def _loadpos_abnormal(state: SensorState) -> bool:
    return not state.loadpos_ok


def _loadpos_normal(state: SensorState) -> bool:
    return state.loadpos_ok


def _clamp(value: int, max_val: int) -> int:
    return max(0, min(value, max_val))


def _update_43501_latched() -> int | None:
    """仅依赖 101 的变化时间锁存 43501。"""
    global _g_43501_latched
    if _g_101_current in (81, 82) and _g_101_changed_at is not None:
        elapsed = time.time() - _g_101_changed_at
        if elapsed >= 60:
            if _g_101_current == 82:
                _g_43501_latched = 1
            elif _g_101_current == 81:
                _g_43501_latched = 0
    return _g_43501_latched


# ================= 核心逻辑函数 =================

def build_rtu_registers(state: SensorState, current_rtu_101: int | None = None) -> Dict[int, int]:
    """
    构建需要写入 RTU 的寄存器字典。
    包含：故障停机逻辑、状态锁定逻辑、3501/43501 状态位逻辑。
    """
    global _g_101_trigger_start, _g_101_current, _g_101_changed_at

    registers: Dict[int, int] = {}

    # ---------------------------------------------------------
    # 0. 同步 101 状态（检测外部/手动修改）
    # ---------------------------------------------------------
    if current_rtu_101 is not None:
        # 如果从硬件读取到了新的 101 值（例如人工写了 81 或 82），更新内存状态
        if _g_101_current != current_rtu_101:
            _g_101_current = current_rtu_101
            _g_101_changed_at = time.time()
            # 如果人工介入（如手动启动），清除三级报警的自动计时器
            if current_rtu_101 == 81:
                _g_101_trigger_start = None

    # ---------------------------------------------------------
    # 1. 优先处理 43501 (远程/就地状态指示)
    # ---------------------------------------------------------
    latched_43501 = _update_43501_latched()
    if latched_43501 == 1:
        # 如果 43501 被锁定为 1，除了写 43501=1 外，不进行其他操作 (视需求而定)
        # 这里的逻辑保持原样，只写这一项
        registers[43501] = 1
        return registers

    # ---------------------------------------------------------
    # 2. 计算当前报警等级 & 中间变量
    # ---------------------------------------------------------
    missing_count = _electrical_missing_count(state)
    electrical_level = 0 if missing_count <= 0 else (1 if missing_count == 1 else 2)
    loadpos_level = 1 if _loadpos_abnormal(state) else 0

    # 综合报警等级
    overall_alarm_level = max(
        0, state.belt_level, state.mid_bearing_level, state.tail_bearing_level,
        state.horsehead_level, state.crank_left_level, state.crank_right_level,
        state.line_level, electrical_level, loadpos_level,
    )
    if overall_alarm_level > 3:
        overall_alarm_level = 3

    # 特殊降级逻辑：如果有3级传感器报警，但电参和载荷正常，则降为2级
    any_sensor_lvl3 = _any_sensor_reach_level(state, 3)
    cond_lvl3_but_normal = any_sensor_lvl3 and _electrical_all_ok(state) and _loadpos_normal(state)
    if cond_lvl3_but_normal and overall_alarm_level >= 3:
        overall_alarm_level = 2

    # ---------------------------------------------------------
    # 3. 填充通用状态寄存器 (3502 - 3520)
    # ---------------------------------------------------------
    registers[3502] = _clamp(overall_alarm_level, 3)
    registers[3503] = 0  # 假设刹车未接入

    # 故障类型判定 (3504)
    fault_type = 0
    any_vib_lvl3 = _any_vibration_reach_level(state, 3)
    belt_lvl3 = state.belt_level >= 3
    elec_bad = _electrical_missing_at_least_one(state)

    if belt_lvl3 and elec_bad:
        fault_type = 1
    elif any_vib_lvl3 and _electrical_all_ok(state):
        fault_type = 3
    elif belt_lvl3 and _electrical_all_ok(state):
        fault_type = 3
    elif any_vib_lvl3 and elec_bad:
        fault_type = 2
    registers[3504] = fault_type

    # 详细位状态
    registers[3505] = 1 if state.crank_left_level >= 1 else 0
    registers[3506] = 1 if state.crank_right_level >= 1 else 0
    registers[3507] = 1 if state.tail_bearing_level >= 1 else 0
    registers[3508] = 1 if state.mid_bearing_level >= 1 else 0
    registers[3509] = 1 if state.horsehead_level >= 1 else 0
    registers[3510] = 1 if state.belt_level >= 1 else 0
    registers[3511] = 1 if missing_count >= 1 else 0
    registers[3512] = 1 if _loadpos_abnormal(state) else 0

    # 详细模拟量等级
    registers[3513] = _clamp(state.crank_left_level, 3)
    registers[3514] = _clamp(state.crank_right_level, 3)
    registers[3515] = _clamp(state.tail_bearing_level, 3)
    registers[3516] = _clamp(state.mid_bearing_level, 3)
    registers[3517] = _clamp(state.horsehead_level, 3)
    registers[3518] = _clamp(state.belt_level, 3)
    registers[3519] = _clamp(electrical_level, 2)
    registers[3520] = 1 if _loadpos_abnormal(state) else 0

    # ---------------------------------------------------------
    # 4. 控制逻辑 (101写操作) 与 状态反馈 (3501写操作)
    # ---------------------------------------------------------

    # 关键标志位
    is_level_3_alarm = (registers.get(3502, 0) == 3)
    is_stopped_state = (_g_101_current == 82)  # 当前是否已处于停机状态

    if is_level_3_alarm:
        # === 场景 A: 正在发生三级报警 ===

        if _g_101_trigger_start is None:
            _g_101_trigger_start = time.time()

        elapsed_l3 = time.time() - _g_101_trigger_start

        # 动作1: 立即写入停机指令 82 (如果还没写过，且状态还没更新为停机)
        # 为了避免总线拥堵，如果已知是82了可以不发，但为了保险起见，
        # 如果3501还没变成1，或者当前还不是82，就发。
        if not is_stopped_state:
            registers[101] = 82

        # 动作2: 计时满 60秒，将 3501 标记为 1 (停机)
        if elapsed_l3 >= 60:
            registers[3501] = 1
        # 未满60秒前，3501 保持原状态 (Float)

    else:
        # === 场景 B: 没有三级报警 (Level 0, 1, 2) ===

        # 清除三级报警计时器
        _g_101_trigger_start = None

        if is_stopped_state:
            # === 关键修正：停机锁定 (Stop Latch) ===
            # 如果机器目前是停机状态 (101=82)，严禁自动重启！
            # 无论现在传感器 Level 降到了 2, 1 还是 0，都必须保持 3501=1。
            # 只有人工发送 101=81 (在步骤0处理) 才能解除此锁定。
            registers[3501] = 1

            # 既然停机了，就不需要重复发 101=82，也不发 81
            if 101 in registers: del registers[101]

        else:
            # === 场景 C: 机器正在运行 (101=81) 且无严重故障 ===

            if overall_alarm_level < 2:
                # 只有 Level 0 或 1，且机器在运行，才确认“运行状态”
                registers[3501] = 0
            elif overall_alarm_level == 2:
                # Level 2 报警：不动作。
                # 不写 101，也不写 3501 (保持原值，通常是 0)
                pass

    # ---------------------------------------------------------
    # 5. 43501 延迟写入逻辑 (当 latched_43501 == 0 时)
    # ---------------------------------------------------------
    if latched_43501 == 0:
        if _g_101_current == 81 and _g_101_changed_at is not None:
            if time.time() - _g_101_changed_at >= 60:
                registers[43501] = 0

    return registers


# ================= 测试演示 =================
if __name__ == "__main__":
    print("--- 场景测试 ---")

    # 1. 初始状态：机器运行中，一切正常
    s = SensorState()
    print("\n1. [正常运行] 读取状态 (假设 RTU 101=81)")
    regs = build_rtu_registers(s, current_rtu_101=81)
    print(f"   -> 写入寄存器: {regs} (预期: 3501=0)")

    # 2. 突发故障：皮带断裂 (Level 3)
    s.belt_level = 3
    s.elec_phase_a_ok = False  # 配合一下变成3级
    print("\n2. [突发故障] 皮带3级报警")
    regs = build_rtu_registers(s, current_rtu_101=81)  # 还没停下来
    print(f"   -> 写入寄存器: {regs} (预期: 101=82)")

    # 3. 模拟过了一会儿，机器停了 (RTU反馈 101=82)，且时间超过60秒
    print("\n3. [故障持续] 机器已停机，且持续60秒")
    # 模拟时间流逝
    if _g_101_trigger_start:
        _g_101_trigger_start -= 61
    regs = build_rtu_registers(s, current_rtu_101=82)
    print(f"   -> 写入寄存器: {regs} (预期: 3501=1)")

    # 4. 关键测试：机器停了导致震动消失，传感器变回正常 (Level 0)
    s.belt_level = 0
    s.elec_phase_a_ok = True
    print("\n4. [幽灵重启测试] 传感器恢复正常 (Level 0)，但没人去现场复位")
    # 注意：此时 current_rtu_101 依然是 82 (停机状态)
    regs = build_rtu_registers(s, current_rtu_101=82)
    print(f"   -> 写入寄存器: {regs}")

    if regs.get(3501) == 1:
        print("   ✅ 测试通过：虽然传感器正常，但由于处于停机锁定状态，3501 保持为 1，未误报运行。")
    else:
        print("   ❌ 测试失败：3501 被错误写成了 0！")

    # 5. 人工复位
    print("\n5. [人工启动] 操作员按下启动按钮 (RTU 变回 81)")
    regs = build_rtu_registers(s, current_rtu_101=81)
    print(f"   -> 写入寄存器: {regs} (预期: 3501=0)")