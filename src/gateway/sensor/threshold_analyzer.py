import collections
from dataclasses import dataclass
from typing import Deque, List, Optional


@dataclass
class ThresholdConfig:
    """阈值分析配置。

    属性:
        level1: 一级阈值数值
        level2: 二级阈值数值
        level3: 三级阈值数值
        window_size: 用于判断"最近一段时间"的滑动窗口长度（样本数）
        min_spike_count: 在窗口中至少有多少个样本超过某个阈值，才认为达到该级别
        baseline_window: 基线窗口长度，用于估计"平稳期"平均值
        baseline_tol: 判断是否平稳的容差比例，例如 0.1 表示 ±10% 波动视为平稳
    """

    level1: float
    level2: float
    level3: float
    window_size: int = 50
    min_spike_count: int = 3
    baseline_window: int = 50
    baseline_tol: float = 0.1


@dataclass
class ThresholdResult:
    """阈值分析结果。"""

    level: int  # 0=未触发，1/2/3=达到的最高等级
    value: float  # 当前值
    baseline: Optional[float]  # 基线值，如果尚未建立则为 None


class ThresholdAnalyzer:
    """对单一路径数据流做实时阈值识别的分析器。

    使用方式：
        cfg = ThresholdConfig(level1=10, level2=20, level3=30)
        analyzer = ThresholdAnalyzer(cfg)
        for v in data_stream:
            result = analyzer.update(v)
            if result.level > 0:
                print("当前达到阈值等级:", result.level)

    逻辑说明（简化版）：
      1. 首先在 baseline_window 内建立一个"基线"平均值，用来表示"平稳期"水平。
      2. 当窗口中的值相对基线有明显增大，并且超过某个绝对阈值（level1/2/3），
         且在 window_size 窗口内至少出现 min_spike_count 次时，输出对应等级。
      3. 如果同时满足多个阈值，则输出最高等级（3 > 2 > 1）。
    """

    def __init__(self, config: ThresholdConfig) -> None:
        self.config = config
        self._history: Deque[float] = collections.deque(maxlen=config.window_size)
        self._baseline_buf: Deque[float] = collections.deque(maxlen=config.baseline_window)
        self._baseline: Optional[float] = None

    @property
    def baseline(self) -> Optional[float]:
        return self._baseline

    def _update_baseline(self, value: float) -> None:
        """根据新值逐步更新基线估计。"""
        self._baseline_buf.append(value)
        if len(self._baseline_buf) == self.config.baseline_window:
            self._baseline = sum(self._baseline_buf) / len(self._baseline_buf)

    def _is_stable(self) -> bool:
        """判断当前基线窗口是否处于"平稳"状态。

        简单策略：窗口内的最大/最小值相差不超过 baseline_tol * 基线平均值。
        """
        if len(self._baseline_buf) < self.config.baseline_window:
            return False
        baseline = sum(self._baseline_buf) / len(self._baseline_buf)
        if baseline == 0:
            return False
        spread = max(self._baseline_buf) - min(self._baseline_buf)
        return spread <= self.config.baseline_tol * baseline

    def _count_over_threshold(self, threshold: float) -> int:
        """统计窗口中有多少个值超过指定阈值。"""
        return sum(1 for v in self._history if v >= threshold)

    def update(self, value: float) -> ThresholdResult:
        """输入一个新数据点，返回当前阈值等级结果。"""
        # 更新基线估计
        self._update_baseline(value)
        # 更新最近窗口
        self._history.append(value)

        level = 0
        # 若基线尚未稳定，先不做"平稳->突变"的判定，只依据绝对阈值
        baseline = self._baseline

        # 统计超过三个阈值的次数
        c1 = self._count_over_threshold(self.config.level1)
        c2 = self._count_over_threshold(self.config.level2)
        c3 = self._count_over_threshold(self.config.level3)

        # 先按最高阈值判断
        if c3 >= self.config.min_spike_count:
            level = 3
        elif c2 >= self.config.min_spike_count:
            level = 2
        elif c1 >= self.config.min_spike_count:
            level = 1

        # 可选：如果基线已经稳定，可以加入"相对增幅"条件，例如：
        #   当前值 / 基线 > 某个比例，才认为是真正的"从平稳到更大"。
        # 这里示例加一个简单的二次过滤：
        if level > 0 and baseline is not None and self._is_stable():
            ratio = value / baseline if baseline != 0 else 0.0
            # 要求当前值至少比基线高出 20%，避免由于小抖动误报
            if ratio < 1.2:
                level = 0

        return ThresholdResult(level=level, value=value, baseline=baseline)


class MultiChannelThresholdAnalyzer:
    """多通道阈值分析器，方便对 VX/VY/VZ 等多路数据同时判断。

    用法示例：
        cfg = ThresholdConfig(level1=10, level2=20, level3=30)
        analyzer = MultiChannelThresholdAnalyzer(cfg, channels=["VX", "VY", "VZ"])

        # 每次采集到一组三轴数据
        result = analyzer.update({"VX": vx, "VY": vy, "VZ": vz})
        # result.levels 是一个 {channel: level} 的字典
        # result.max_level 是这一组数据中三轴的最高等级
    """

    @dataclass
    class MultiResult:
        levels: dict
        baselines: dict
        max_level: int

    def __init__(self, config: ThresholdConfig, channels: List[str]):
        self._analyzers = {name: ThresholdAnalyzer(config) for name in channels}
        self._channels = list(channels)

    def update(self, values: dict) -> "MultiChannelThresholdAnalyzer.MultiResult":
        levels = {}
        baselines = {}
        max_level = 0
        for name in self._channels:
            v = float(values.get(name, 0.0))
            res = self._analyzers[name].update(v)
            levels[name] = res.level
            baselines[name] = res.baseline
            if res.level > max_level:
                max_level = res.level
        return MultiChannelThresholdAnalyzer.MultiResult(levels=levels, baselines=baselines, max_level=max_level)

