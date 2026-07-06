import time
from collections import defaultdict


class MetricsCollector:
    """指标采集器 —— 收集Agent运行过程中的各项指标"""

    def __init__(self):
        self._counters = defaultdict(int)
        self._gauges = {}
        self._latencies = defaultdict(list)
        self._events = []

    def increment(self, name, value=1):
        self._counters[name] += value

    def gauge(self, name, value):
        self._gauges[name] = value

    def record_latency(self, name, seconds):
        self._latencies[name].append(seconds)
        if len(self._latencies[name]) > 1000:
            self._latencies[name] = self._latencies[name][-1000:]

    def record_event(self, name, extra=None):
        self._events.append({
            "name": name,
            "timestamp": time.time(),
            "extra": extra or {},
        })
        if len(self._events) > 10000:
            self._events = self._events[-10000:]

    def get_counters(self):
        return dict(self._counters)

    def get_gauges(self):
        return dict(self._gauges)

    def get_latency_stats(self, name):
        values = self._latencies.get(name, [])
        if not values:
            return {"avg": 0, "p50": 0, "p95": 0, "p99": 0, "count": 0}
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        return {
            "avg": round(sum(sorted_vals) / n, 3),
            "p50": round(sorted_vals[int(n * 0.5)], 3),
            "p95": round(sorted_vals[int(n * 0.95)], 3),
            "p99": round(sorted_vals[int(n * 0.99)], 3),
            "count": n,
            "max": round(sorted_vals[-1], 3),
        }

    def get_all_latencies(self):
        return {k: self.get_latency_stats(k) for k in self._latencies}

    def snapshot(self):
        return {
            "counters": self.get_counters(),
            "gauges": self.get_gauges(),
            "latencies": self.get_all_latencies(),
            "events_count": len(self._events),
        }

    def reset(self):
        self._counters.clear()
        self._gauges.clear()
        self._latencies.clear()
        self._events.clear()
