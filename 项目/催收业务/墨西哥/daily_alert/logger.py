"""
生产日志模块 — 每日运行日志

日志文件: logs/daily_alert_YYYYMMDD.log
记录: 开始/结束时间、耗时、国家、业务日期、异常数量、错误信息
"""

import os
import sys
from datetime import datetime


class Logger:
    """每日预警专用日志器"""

    def __init__(self, run_date, business_date, stage="D0", country="ALL"):
        self.run_date = run_date
        self.business_date = business_date
        self.stage = stage
        self.country = country
        self.start_time = datetime.now()
        self.end_time = None
        self.error_count = 0
        self.errors = []
        self.anomaly_count = 0

        # Profile 计时（v1.0.1）
        self._profile = {}           # {label: elapsed_seconds}
        self._profile_start = None   # current timing start
        self._cache_stats = None     # set by set_cache_stats()

        # 确保 logs/ 目录存在
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.logs_dir = os.path.join(script_dir, "logs")
        os.makedirs(self.logs_dir, exist_ok=True)

        # 日志文件名: daily_alert_YYYYMMDD.log
        log_filename = f"daily_alert_{run_date.replace('-', '')}.log"
        self.log_path = os.path.join(self.logs_dir, log_filename)

        self._write_header()

    def _write_header(self):
        """写入日志头部"""
        lines = [
            "=" * 60,
            f"  首逾智能预警系统 — 运行日志",
            f"  运行日期: {self.run_date}  |  业务日期: {self.business_date}",
            f"  阶段: {self.stage}  |  国家: {self.country}",
            f"  开始时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 60,
            "",
        ]
        self._write_lines(lines)

    def _write_lines(self, lines):
        """追加写入多行文本"""
        # 同时在控制台打印（前 3 行头部）
        with open(self.log_path, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

    def info(self, msg):
        """普通信息"""
        line = f"[INFO]  {datetime.now().strftime('%H:%M:%S')}  {msg}"
        self._write_lines([line])

    def warn(self, msg):
        """警告"""
        line = f"[WARN]  {datetime.now().strftime('%H:%M:%S')}  {msg}"
        self._write_lines([line])

    def error(self, msg):
        """错误（不终止运行）"""
        self.error_count += 1
        self.errors.append(msg)
        line = f"[ERROR] {datetime.now().strftime('%H:%M:%S')}  {msg}"
        self._write_lines([line])

    def fatal(self, msg):
        """致命错误（将终止运行）"""
        self.error_count += 1
        self.errors.append(msg)
        line = f"[FATAL] {datetime.now().strftime('%H:%M:%S')}  {msg}"
        self._write_lines([line])

    def set_anomaly_count(self, count):
        """记录异常数量"""
        self.anomaly_count = count

    # ---- Profile 计时（v1.0.1） ----

    def profile_start(self, label):
        """开始计时"""
        self._profile_start = (label, datetime.now())

    def profile_end(self):
        """结束当前计时并记录"""
        if self._profile_start:
            label, t0 = self._profile_start
            elapsed = (datetime.now() - t0).total_seconds()
            self._profile[label] = elapsed
            self._profile_start = None

    def set_cache_stats(self, stats):
        """记录 API 缓存统计"""
        self._cache_stats = stats

    # ---- finish ----

    def finish(self, success=True, exit_code=0):
        """写入日志尾部"""
        self.end_time = datetime.now()
        duration = (self.end_time - self.start_time).total_seconds()

        status = "✅ 成功" if success else "❌ 失败"

        lines = [
            "",
            "-" * 60,
            f"  结束时间: {self.end_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"  运行耗时: {duration:.1f}s",
            f"  运行状态: {status}",
            f"  异常数量: {self.anomaly_count}",
            f"  错误数量: {self.error_count}",
        ]

        if self.errors:
            lines.append("  错误详情:")
            for err in self.errors:
                lines.append(f"    • {err[:200]}")

        # ---- Profile 输出（v1.0.1） ----
        if self._cache_stats:
            lines.append("")
            lines.append("-" * 60)
            lines.append("  [Profile] API 请求统计")
            lines.append("")
            cs = self._cache_stats
            lines.append(f"  API 请求: {cs['calls']}")
            lines.append(f"  缓存读取: {cs['hits']}")
            lines.append(f"  Cache Hit Rate: {cs['hit_rate']:.1f}%")
            lines.append("")
            for api_id, s in cs.get("per_api", {}).items():
                lines.append(f"  {api_id}")
                lines.append(f"    calls: {s['calls']}")
                lines.append(f"    rows:  {s['rows']}")
                lines.append(f"    time:  {s['time_sec']:.2f}s")

        if self._profile:
            if not self._cache_stats:
                lines.append("")
                lines.append("-" * 60)
            lines.append("")
            lines.append("  [Profile] 各阶段耗时")
            for label, elapsed in self._profile.items():
                lines.append(f"  {label}: {elapsed:.2f}s")

        lines.append("=" * 60)
        lines.append("")

        self._write_lines(lines)

        # 同时打印到控制台
        print(f"\n  📝 日志已保存: {self.log_path}")
        print(f"  {'✅ 成功' if success else '❌ 失败'} | "
              f"耗时: {duration:.1f}s | 异常: {self.anomaly_count} | 错误: {self.error_count}")

        return exit_code
