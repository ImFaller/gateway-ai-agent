import re
import time
import os
import tempfile
from pathlib import Path

import yaml

from engine.message_bus import Message


# 运行时策略持久化文件 —— 与 strategies.yaml（出厂默认）分离
# 业界做法：Kubernetes 默认配置 + ConfigMap 用户配置分离；IDE 默认设置 + 用户设置分离
# 加载顺序：先加载 strategies.yaml（基础） → 再加载本文件（运行时添加的）
# 持久化时机：add_strategy / remove_strategy / update_strategy 后同步写盘
# 写盘失败只记日志不抛错，内存策略仍可用（不影响主流程）
RUNTIME_STRATEGIES_FILE = os.path.join(tempfile.gettempdir(), "gateway_ai_runtime_strategies.yaml")


class StrategyCondition:
    """策略条件规则"""

    def __init__(self, field, operator, value):
        self.field = field
        self.operator = operator
        self.value = value

    def evaluate(self, context):
        if context is None:
            return False
        actual = context
        for part in self.field.split("."):
            if isinstance(actual, dict):
                actual = actual.get(part, {})
            else:
                return False

        if self.operator == "eq":
            return actual == self.value
        elif self.operator == "ne":
            return actual != self.value
        elif self.operator == "gt":
            return isinstance(actual, (int, float)) and actual > self.value
        elif self.operator == "lt":
            return isinstance(actual, (int, float)) and actual < self.value
        elif self.operator == "contains":
            return self.value in (actual if isinstance(actual, str) else str(actual))
        elif self.operator == "in":
            return actual in (self.value if isinstance(self.value, list) else [self.value])
        elif self.operator == "regex":
            return bool(re.search(str(self.value), str(actual)))
        elif self.operator == "exists":
            return actual is not None and actual != {}
        return False


class StrategyStep:
    """策略中的一个执行步骤"""

    def __init__(self, step_id, agent, action, params=None, timeout=30):
        self.step_id = step_id
        self.agent = agent
        self.action = action
        self.params = params or {}
        self.timeout = timeout


class Strategy:
    """完整策略定义"""

    def __init__(self, strategy_id, name, description="",
                 enabled=True, priority=100,
                 triggers=None, steps=None, fallback=None):
        self.strategy_id = strategy_id
        self.name = name
        self.description = description
        self.enabled = enabled
        self.priority = priority
        self.triggers = [StrategyCondition(**t) for t in (triggers or [])]
        self.steps = [StrategyStep(**s) for s in (steps or [])]
        self.fallback = fallback or {"action": "reject", "message": "Strategy rejected"}

    def matches(self, context):
        if not self.enabled:
            return False
        return all(t.evaluate(context) for t in self.triggers)

    def to_dict(self):
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "priority": self.priority,
            "triggers": [{"field": t.field, "operator": t.operator, "value": t.value}
                         for t in self.triggers],
            "steps": [{"step_id": s.step_id, "agent": s.agent, "action": s.action,
                       "params": s.params, "timeout": s.timeout} for s in self.steps],
        }

    def to_yaml_dict(self):
        """转成 yaml 可序列化的 dict（id 而非 strategy_id，对齐 strategies.yaml 格式）"""
        return {
            "id": self.strategy_id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "priority": self.priority,
            "triggers": [{"field": t.field, "operator": t.operator, "value": t.value}
                         for t in self.triggers],
            "steps": [{"step_id": s.step_id, "agent": s.agent, "action": s.action,
                       "params": s.params, "timeout": s.timeout} for s in self.steps],
            "fallback": self.fallback,
        }


class StrategyEngine:
    """策略引擎 —— 加载策略 -> 匹配条件 -> 编排Agent执行"""

    # 回收站保留天数（业界共识：Slack/Google Drive 30 天，Notion 30 天）
    TRASH_RETENTION_DAYS = 30
    # 回收站持久化文件名（与运行时策略文件同目录）
    TRASH_FILENAME = "gateway_ai_runtime_strategies.trash.yaml"

    def __init__(self, message_bus=None):
        self.message_bus = message_bus
        self.strategies = []
        self.execution_history = []
        # 回收站：存放被软删除的策略 + 删除时间，可恢复
        # 每个元素是 {"strategy": Strategy, "deleted_at": float_timestamp}
        self._trash = []
        # 标记哪些策略 ID 是运行时添加的（持久化时只写这部分，避免把出厂策略也写进去）
        self._runtime_ids = set()

    def load_from_yaml(self, yaml_path):
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"策略文件不存在: {yaml_path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for s in data.get("strategies", []):
            strategy = Strategy(
                strategy_id=s["id"],
                name=s["name"],
                description=s.get("description", ""),
                enabled=s.get("enabled", True),
                priority=s.get("priority", 100),
                triggers=s.get("triggers", []),
                steps=s.get("steps", []),
                fallback=s.get("fallback"),
            )
            # 出厂策略 is_runtime=False，不参与运行时持久化
            self.add_strategy(strategy, is_runtime=False)

    def load_runtime_strategies(self, runtime_path=None):
        """加载运行时持久化的策略（启动时调用）

        与 load_from_yaml 不同：
        - 不抛 FileNotFoundError（运行时文件可能不存在，是正常情况）
        - 加载后把这些 ID 标记为"运行时"，后续持久化时只写这部分
        """
        path = Path(runtime_path or RUNTIME_STRATEGIES_FILE)
        if not path.exists():
            return  # 首次启动没有运行时文件，正常
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for s in data.get("strategies", []):
                # 同名 ID 的运行时策略覆盖出厂策略（先删后加）
                self.strategies = [x for x in self.strategies if x.strategy_id != s["id"]]
                strategy = Strategy(
                    strategy_id=s["id"],
                    name=s["name"],
                    description=s.get("description", ""),
                    enabled=s.get("enabled", True),
                    priority=s.get("priority", 100),
                    triggers=s.get("triggers", []),
                    steps=s.get("steps", []),
                    fallback=s.get("fallback"),
                )
                self.strategies.append(strategy)
                self._runtime_ids.add(s["id"])
            self.strategies.sort(key=lambda x: x.priority)
        except Exception:
            # 运行时文件损坏不阻塞启动，只打日志
            print(f"[策略引擎] 运行时策略文件加载失败：{path}")

    def _persist_runtime_strategies(self):
        """把当前所有运行时策略写到 yaml 文件

        只写 _runtime_ids 标记的策略，避免把出厂策略也持久化（防止 strategies.yaml 改动被覆盖）
        写失败只打日志不抛错，内存策略仍可用
        """
        runtime_list = [s.to_yaml_dict() for s in self.strategies if s.strategy_id in self._runtime_ids]
        try:
            with open(RUNTIME_STRATEGIES_FILE, "w", encoding="utf-8") as f:
                yaml.dump({"strategies": runtime_list}, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as e:
            print(f"[策略引擎] 运行时策略持久化失败：{str(e)[:100]}")

    @staticmethod
    def _normalize_value(v):
        """值规范化 —— 解决 LLM 输出类型不稳定问题

        LLM 同一个策略可能两次返回不同类型：
        - port: "22"（字符串） vs port: 22（整数）→ 应视为相等
        - description: "" vs description: null → 应视为相等
        - enabled: true vs enabled: 1 → Pydantic 已转 bool，不用处理

        规范化规则：
        - None → ""（统一空值）
        - 字符串数字如 "22" → 22（int）或 22.0（float）
        - 其他保持原样
        """
        if v is None:
            return ""
        if isinstance(v, str):
            # 尝试转 int
            try:
                if v.strip() == "":
                    return ""
                if "." not in v:
                    return int(v)
                return float(v)
            except (ValueError, TypeError):
                return v
        return v

    @classmethod
    def _normalize_params(cls, params):
        """规范化 params 字典（递归处理嵌套）"""
        if not isinstance(params, dict):
            return cls._normalize_value(params)
        return {k: cls._normalize_value(v) for k, v in params.items()}

    @classmethod
    def _strategies_identical(cls, s1, s2):
        """比较两个策略的实质内容是否完全一致

        比较维度：name/description/enabled/priority/triggers/steps
        不比较 strategy_id（外层已确保 ID 相同才会调用本方法）
        不比较 fallback（默认值相同，通常不参与业务判断）

        步骤顺序也参与比较 —— 业界共识：策略的步骤顺序对执行语义是有意义的
        （先 audit 再 alert 与 先 alert 再 audit 是不同策略）

        Returns:
            tuple: (is_identical: bool, reason: str)
                   is_identical=True 时 reason=""; 否则 reason 说明哪个字段不一致
        """
        if s1.name != s2.name:
            return False, f"name 不一致: '{s1.name}' vs '{s2.name}'"
        # description 空字符串和 None 视为相等（LLM 不稳定）
        d1 = s1.description or ""
        d2 = s2.description or ""
        if d1 != d2:
            return False, f"description 不一致: '{d1}' vs '{d2}'"
        if s1.enabled != s2.enabled:
            return False, f"enabled 不一致: {s1.enabled} vs {s2.enabled}"
        if s1.priority != s2.priority:
            return False, f"priority 不一致: {s1.priority} vs {s2.priority}"
        # triggers 列表：长度+顺序+每项内容都要一致（值要规范化比较）
        if len(s1.triggers) != len(s2.triggers):
            return False, f"triggers 数量不一致: {len(s1.triggers)} vs {len(s2.triggers)}"
        for i, (t1, t2) in enumerate(zip(s1.triggers, s2.triggers)):
            if t1.field != t2.field:
                return False, f"triggers[{i}].field 不一致: '{t1.field}' vs '{t2.field}'"
            if t1.operator != t2.operator:
                return False, f"triggers[{i}].operator 不一致: '{t1.operator}' vs '{t2.operator}'"
            v1 = cls._normalize_value(t1.value)
            v2 = cls._normalize_value(t2.value)
            if v1 != v2:
                return False, f"triggers[{i}].value 不一致: {v1!r} vs {v2!r}"
        # steps 列表：长度+顺序+每项内容都要一致（params 嵌套字典也要规范化）
        if len(s1.steps) != len(s2.steps):
            return False, f"steps 数量不一致: {len(s1.steps)} vs {len(s2.steps)}"
        for i, (st1, st2) in enumerate(zip(s1.steps, s2.steps)):
            if st1.step_id != st2.step_id:
                return False, f"steps[{i}].step_id 不一致: '{st1.step_id}' vs '{st2.step_id}'"
            if st1.agent != st2.agent:
                return False, f"steps[{i}].agent 不一致: '{st1.agent}' vs '{st2.agent}'"
            if st1.action != st2.action:
                return False, f"steps[{i}].action 不一致: '{st1.action}' vs '{st2.action}'"
            if st1.timeout != st2.timeout:
                return False, f"steps[{i}].timeout 不一致: {st1.timeout} vs {st2.timeout}"
            p1 = cls._normalize_params(st1.params)
            p2 = cls._normalize_params(st2.params)
            if p1 != p2:
                return False, f"steps[{i}].params 不一致: {p1!r} vs {p2!r}"
        return True, ""

    def find_existing_strategy(self, strategy_id):
        """按 ID 查找已存在的策略，返回 Strategy 或 None"""
        for s in self.strategies:
            if s.strategy_id == strategy_id:
                return s
        return None

    @classmethod
    def _strategies_content_identical(cls, s1, s2):
        """比较两个策略的实质业务内容是否完全一致（不含 ID/name/step_id）

        用于"按内容查重" —— 检测"内容一样但 ID/name 不同"的重复添加
        业务上，两条策略如果 triggers/steps/priority/enabled/description 完全一样，
        就是重复策略（无论 ID/name/step_id 怎么写）。执行时会重复触发同一流量。

        业界做法对照：
        - Git content-addressable：内容相同即同一对象
        - 数据库 UNIQUE 约束：可加在业务内容字段上
        - 网闸场景：两条内容相同的策略会让同一流量被重复处理

        不比较的字段（技术标识符，业务语义无意义）：
        - strategy_id / name：外层已确认不同
        - step_id：LLM 两次生成同一策略时 step_id 经常不同（audit_step vs step1）
          业务上重要的是 agent+action+params+顺序，不是 step 的技术 ID
        - fallback：默认值相同
        """
        d1 = s1.description or ""
        d2 = s2.description or ""
        if d1 != d2: return False
        if s1.enabled != s2.enabled: return False
        if s1.priority != s2.priority: return False
        if len(s1.triggers) != len(s2.triggers): return False
        for t1, t2 in zip(s1.triggers, s2.triggers):
            if t1.field != t2.field: return False
            if t1.operator != t2.operator: return False
            if cls._normalize_value(t1.value) != cls._normalize_value(t2.value): return False
        if len(s1.steps) != len(s2.steps): return False
        for st1, st2 in zip(s1.steps, s2.steps):
            # 不比较 step_id（技术标识符，业务上无意义）
            if st1.agent != st2.agent: return False
            if st1.action != st2.action: return False
            if st1.timeout != st2.timeout: return False
            if cls._normalize_params(st1.params) != cls._normalize_params(st2.params): return False
        return True

    def find_strategy_by_content(self, strategy):
        """按实质内容查找已存在的策略（不看 ID 和 name）

        Returns:
            Strategy 或 None。找到内容完全相同的策略（不同 ID/name）时返回该策略，
            用于在 add_strategy 提示用户已存在哪条同内容策略。
        """
        for s in self.strategies:
            if self._strategies_content_identical(s, strategy):
                return s
        return None

    def add_strategy(self, strategy, is_runtime=True):
        """添加策略（含双重重复检测）

        业界做法对齐：
        - kubectl apply 语义：同 ID 内容一致 → duplicate；同 ID 内容不同 → updated
        - Git content-addressable：不同 ID 但内容完全一致 → duplicate_content（拒绝）

        决策流程：
        1. ID 不存在 → 检查内容是否与现有策略重复
           - 内容也无人重复 → 新增（added）
           - 内容已存在（不同 ID）→ 拒绝（duplicate_content）
        2. ID 存在 → 比较内容
           - 内容完全一致 → 拒绝（duplicate）
           - 内容不同 → 覆盖（updated）

        Args:
            strategy: 要添加的策略对象
            is_runtime: 是否为运行时添加（默认 True）

        Returns:
            tuple: (status: str, reason: str)
                   status ∈ {added, updated, duplicate, duplicate_content}
                   - added: 新策略已添加
                   - updated: 同 ID 内容不同，已覆盖
                   - duplicate: 同 ID 同内容，未做改变
                   - duplicate_content: 不同 ID 但内容完全一致，已拒绝
                   reason: 仅 updated / duplicate_content 时有值，用于日志排查
        """
        existing = self.find_existing_strategy(strategy.strategy_id)
        if existing:
            # 同 ID —— 比较内容
            is_identical, reason = self._strategies_identical(existing, strategy)
            if is_identical:
                return "duplicate", ""
            # 内容不同，覆盖
            self.strategies = [s for s in self.strategies if s.strategy_id != strategy.strategy_id]
            self.strategies.append(strategy)
            self.strategies.sort(key=lambda x: x.priority)
            if is_runtime:
                self._runtime_ids.add(strategy.strategy_id)
                self._persist_runtime_strategies()
            return "updated", reason
        # ID 不存在 —— 检查是否已存在内容完全相同的策略（不同 ID/name）
        content_dup = self.find_strategy_by_content(strategy)
        if content_dup:
            return "duplicate_content", f"已存在内容相同的策略 ID={content_dup.strategy_id} name={content_dup.name}"
        # 全新策略
        self.strategies.append(strategy)
        self.strategies.sort(key=lambda x: x.priority)
        if is_runtime:
            self._runtime_ids.add(strategy.strategy_id)
            self._persist_runtime_strategies()
        return "added", ""

    def remove_strategy(self, strategy_id):
        """软删除策略 —— 移到回收站，30 天内可恢复

        业界做法对照：
        - Slack/Google Drive/Notion：删除先进回收站，30 天后自动清理
        - Git：保留 reflog 可恢复
        - 数据库：DELETE 是硬删除，但配合 binlog 可按时间点恢复

        实现要点：
        1. 从 self.strategies 移除
        2. 加入 self._trash（带 deleted_at 时间戳）
        3. 同步写盘 runtime strategies（删除生效）和 trash（可恢复）
        4. 出厂策略被删也能恢复（trash 不区分来源）

        Returns:
            bool: 是否真的删除了（False 表示策略不存在）
        """
        before = len(self.strategies)
        removed_strategy = None
        for s in self.strategies:
            if s.strategy_id == strategy_id:
                removed_strategy = s
                break
        self.strategies = [s for s in self.strategies if s.strategy_id != strategy_id]
        removed = len(self.strategies) < before
        if removed:
            # 从运行时集合中移除（如果原本是出厂策略被删，也不写回运行时文件）
            self._runtime_ids.discard(strategy_id)
            # 加入回收站（带时间戳）
            import time as _time
            self._trash.append({
                "strategy": removed_strategy,
                "deleted_at": _time.time(),
            })
            self._persist_runtime_strategies()
            self._persist_trash()
        return removed

    def list_trash(self):
        """列出回收站中所有策略（按删除时间倒序）

        Returns:
            list[dict]: 每项 {"strategy_id", "name", "deleted_at", "days_left"}
                        days_left 是剩余保留天数，<=0 表示已过期可清理
        """
        import time as _time
        now = _time.time()
        result = []
        for item in self._trash:
            s = item["strategy"]
            deleted_at = item["deleted_at"]
            days_left = self.TRASH_RETENTION_DAYS - (now - deleted_at) / 86400
            result.append({
                "strategy_id": s.strategy_id,
                "name": s.name,
                "deleted_at": deleted_at,
                "days_left": round(days_left, 1),
            })
        # 按删除时间倒序（最新删的在最上）
        result.sort(key=lambda x: x["deleted_at"], reverse=True)
        return result

    def restore_strategy(self, strategy_id, new_id=None):
        """从回收站恢复策略

        Args:
            strategy_id: 要恢复的策略 ID（回收站中的 ID）
            new_id: 可选，如果原 ID 已被新策略占用，指定一个新 ID 恢复

        Returns:
            tuple: (status, reason)
                   status ∈ {restored, not_in_trash, id_conflict}
                   - restored: 恢复成功
                   - not_in_trash: 回收站中没有该策略
                   - id_conflict: 原 ID 已被占用，需指定 new_id
        """
        # 1. 在回收站中找
        trash_item = None
        for i, item in enumerate(self._trash):
            if item["strategy"].strategy_id == strategy_id:
                trash_item = self._trash.pop(i)
                break
        if not trash_item:
            return "not_in_trash", f"回收站中没有策略 ID={strategy_id}"

        strategy = trash_item["strategy"]

        # 2. 检查 ID 冲突（原 ID 可能已被新策略占用）
        target_id = new_id or strategy.strategy_id
        if self.find_existing_strategy(target_id):
            # ID 冲突，把策略放回回收站，让用户用 new_id 重试
            self._trash.append(trash_item)
            return "id_conflict", f"ID={target_id} 已被现有策略占用，请指定 new_id 恢复"

        # 3. 恢复到主列表
        strategy.strategy_id = target_id
        self.strategies.append(strategy)
        self.strategies.sort(key=lambda x: x.priority)
        # 标记为运行时策略（恢复后纳入持久化）
        self._runtime_ids.add(target_id)
        self._persist_runtime_strategies()
        self._persist_trash()
        return "restored", f"策略 ID={target_id} name={strategy.name} 已恢复"

    def purge_strategy(self, strategy_id):
        """永久删除回收站中的策略（不可恢复）

        Returns:
            bool: 是否真的清除了
        """
        before = len(self._trash)
        self._trash = [t for t in self._trash if t["strategy"].strategy_id != strategy_id]
        if len(self._trash) < before:
            self._persist_trash()
            return True
        return False

    def _load_trash(self, trash_path=None):
        """启动时加载回收站文件，并自动清理过期项

        业界做法：Slack/Google Drive 启动时清理过期回收站
        """
        import time as _time
        if trash_path is None:
            trash_path = Path(tempfile.gettempdir()) / self.TRASH_FILENAME
        else:
            trash_path = Path(trash_path)
        if not trash_path.exists():
            return
        try:
            data = yaml.safe_load(trash_path.read_text(encoding="utf-8")) or {}
            now = _time.time()
            expired_count = 0
            for item in data.get("trash", []):
                deleted_at = item.get("deleted_at", now)
                age_days = (now - deleted_at) / 86400
                if age_days > self.TRASH_RETENTION_DAYS:
                    expired_count += 1
                    continue  # 过期跳过
                s = item.get("strategy")
                if not s:
                    continue
                strategy = Strategy(
                    strategy_id=s["id"],
                    name=s["name"],
                    description=s.get("description", ""),
                    enabled=s.get("enabled", True),
                    priority=s.get("priority", 100),
                    triggers=s.get("triggers", []),
                    steps=s.get("steps", []),
                    fallback=s.get("fallback"),
                )
                self._trash.append({"strategy": strategy, "deleted_at": deleted_at})
            if expired_count > 0:
                print(f"[策略引擎] 已自动清理 {expired_count} 条过期回收站策略")
                # 持久化一次（剔除已过期的）
                self._persist_trash()
        except Exception as e:
            print(f"[策略引擎] 回收站文件加载失败：{e}")

    def _persist_trash(self):
        """把回收站策略写到 yaml 文件

        文件结构：
            trash:
              - strategy: {id, name, ...}
                deleted_at: 1234567890.123
        """
        try:
            trash_path = Path(tempfile.gettempdir()) / self.TRASH_FILENAME
            data = {
                "trash": [
                    {
                        "strategy": item["strategy"].to_yaml_dict(),
                        "deleted_at": item["deleted_at"],
                    }
                    for item in self._trash
                ]
            }
            trash_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        except Exception as e:
            print(f"[策略引擎] 回收站持久化失败：{e}")

    @staticmethod
    def _values_equal(old, new):
        """比较两个值是否相等（处理 list[对象] vs list[dict] 的情况）"""
        if isinstance(old, list) and isinstance(new, list):
            if len(old) != len(new):
                return False
            for o, n in zip(old, new):
                if isinstance(o, (StrategyCondition, StrategyStep)):
                    o_dict = o.__dict__
                else:
                    o_dict = o
                if isinstance(n, (StrategyCondition, StrategyStep)):
                    n_dict = n.__dict__
                else:
                    n_dict = n
                if o_dict != n_dict:
                    return False
            return True
        return old == new

    def update_strategy(self, strategy_id, **kwargs):
        """部分更新策略（PATCH 语义，未传字段保持原值）

        业界做法对照：
        - HTTP PATCH：部分更新，只改传过来的字段
        - kubectl edit：直接改某字段不重写整个 manifest
        - Git：可只改 hunk 不重写整个文件

        与 add_strategy 的区别：
        - add_strategy 要传完整策略（kubectl apply 语义，全量替换）
        - update_strategy 只传要改的字段（PATCH 语义，部分更新）

        支持的字段：
        - name / description / enabled / priority（标量字段直接赋值）
        - triggers / steps（list 字段会整体替换，并把 dict 转成对象）

        Args:
            strategy_id: 要更新的策略 ID
            **kwargs: 要更新的字段（仅传需要改的）

        Returns:
            tuple: (status, reason)
                   status ∈ {updated, not_found, no_change}
                   - updated: 有字段被改动
                   - not_found: 策略 ID 不存在
                   - no_change: 字段值与原值相同，未实际改动
        """
        target = self.find_existing_strategy(strategy_id)
        if not target:
            return "not_found", f"未找到策略 ID={strategy_id}"

        # 收集实际发生变化的字段
        changes = []
        for k, v in kwargs.items():
            if v is None:
                continue  # PATCH 语义：None 表示不更新该字段
            if not hasattr(target, k):
                continue
            old_val = getattr(target, k)
            # triggers/steps 是 list[对象]，新值是 list[dict]，需要转换后比较
            if k == "triggers":
                new_val = [StrategyCondition(**t) for t in (v or [])]
            elif k == "steps":
                new_val = [StrategyStep(**s) for s in (v or [])]
            else:
                new_val = v
            # 比较（标量直接比，list 比长度+内容）
            if self._values_equal(old_val, new_val):
                continue
            setattr(target, k, new_val)
            changes.append((k, old_val, new_val))

        if not changes:
            return "no_change", "所有传入字段与原值相同，未做改动"

        # 重新按优先级排序（priority 可能变了）
        self.strategies.sort(key=lambda x: x.priority)
        # 出厂策略被改后也标记为运行时，确保变更能持久化
        self._runtime_ids.add(strategy_id)
        self._persist_runtime_strategies()

        # 生成可读的变更说明，用于审计日志
        diff_parts = []
        for k, old, new in changes:
            if k in ("triggers", "steps"):
                diff_parts.append(f"{k}已更新")
            else:
                diff_parts.append(f"{k}: {old!r}→{new!r}")
        return "updated", "；".join(diff_parts)

    def evaluate(self, context):
        return [s for s in self.strategies if s.matches(context)]

    async def execute(self, context):
        matched = self.evaluate(context)
        execution_id = f"exec-{int(time.time() * 1000)}"
        result = {
            "execution_id": execution_id,
            "context": context,
            "matched_strategies": [s.strategy_id for s in matched],
            "steps": [],
            "status": "completed",
            "error": None,
        }
        for strategy in matched:
            for step in strategy.steps:
                step_result = {
                    "step_id": step.step_id,
                    "agent": step.agent,
                    "action": step.action,
                    "status": "pending",
                }
                msg = Message(
                    sender="strategy_engine",
                    target=step.agent,
                    msg_type=f"task:{step.action}",
                    payload={
                        "execution_id": execution_id,
                        "strategy_id": strategy.strategy_id,
                        "action": step.action,
                        "params": step.params,
                        "context": context,
                    },
                    trace_id=execution_id,
                )
                if self.message_bus:
                    await self.message_bus.publish_async(msg)
                step_result["status"] = "dispatched"
                step_result["message_id"] = msg.id
                result["steps"].append(step_result)

        if not matched:
            result["status"] = "no_match"
            result["steps"].append({
                "step_id": "fallback",
                "agent": "orchestrator",
                "action": "fallback",
                "status": "no_strategy_matched",
            })

        self.execution_history.append(result)
        return result

    def get_execution_stats(self):
        total = len(self.execution_history)
        succeeded = sum(1 for e in self.execution_history if e["status"] == "completed")
        failed = sum(1 for e in self.execution_history if e["status"] == "error")
        return {
            "total_executions": total,
            "succeeded": succeeded,
            "failed": failed,
            "strategies_count": len(self.strategies),
            "strategies": [s.to_dict() for s in self.strategies],
        }
