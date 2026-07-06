import asyncio
import time


class Message:
    """Agent之间传递的消息"""

    def __init__(self, sender, target, msg_type, payload, trace_id=""):
        self.sender = sender
        self.target = target
        self.msg_type = msg_type
        self.payload = payload
        self.trace_id = trace_id or f"trace-{int(time.time() * 1000)}"
        self.timestamp = time.time()
        self.id = f"msg-{self.trace_id}-{int(time.time() * 1000000) % 1000000}"

    def to_dict(self):
        return {
            "id": self.id,
            "sender": self.sender,
            "target": self.target,
            "type": self.msg_type,
            "payload": self.payload,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
        }


class MessageBus:
    """基于订阅/发布的消息总线，支持Agent间异步通信

    注意: 生命周期 Hook 已迁移到 LangGraph Callback 机制（system/callbacks.py）
    MessageBus 保留消息订阅/发布能力，供 Agent 内部通信使用。
    """

    def __init__(self):
        self._subscribers = {}
        self._history = []
        self._max_history = 10000

    def subscribe(self, msg_type, callback):
        if msg_type not in self._subscribers:
            self._subscribers[msg_type] = []
        self._subscribers[msg_type].append(callback)

    def unsubscribe(self, msg_type, callback):
        if msg_type in self._subscribers:
            self._subscribers[msg_type] = [
                cb for cb in self._subscribers[msg_type] if cb != callback
            ]

    def publish(self, message):
        self._history.append(message)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        for callback in self._subscribers.get(message.msg_type, []):
            try:
                callback(message)
            except Exception:
                pass

    async def publish_async(self, message):
        self._history.append(message)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        tasks = []
        for callback in self._subscribers.get(message.msg_type, []):
            if asyncio.iscoroutinefunction(callback):
                tasks.append(callback(message))
            else:
                try:
                    callback(message)
                except Exception:
                    pass
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_history(self, msg_type=None, trace_id=None, limit=100):
        filtered = self._history
        if msg_type:
            filtered = [m for m in filtered if m.msg_type == msg_type]
        if trace_id:
            filtered = [m for m in filtered if m.trace_id == trace_id]
        return [m.to_dict() for m in filtered[-limit:]]

    def clear(self):
        self._history.clear()
