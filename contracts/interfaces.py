# 网闸AI设备智能体 - 接口契约
# 四人以此文件为准，不要随意修改

EXECUTION_RESULT_EXAMPLE = {
    "execution_id": "exec-1234567890",
    "status": "completed",   # completed | no_match | error
    "matched_strategies": ["high_risk_traffic_block"],
    "steps": [
        {
            "step_id": "security_audit_01",
            "agent": "security_agent",
            "action": "audit",
            "status": "dispatched"
        }
    ]
}

STRATEGY_EXAMPLE = {
    "strategy_id": "my_policy",
    "name": "我的策略",
    "enabled": True,
    "priority": 50,
    "triggers": [{"field": "port", "operator": "eq", "value": 22}],
    "steps": [{"step_id": "s1", "agent": "security_agent", "action": "audit", "params": {}, "timeout": 30}]
}

EXECUTION_REQUEST_EXAMPLE = {
    "source_ip": "192.168.1.1",
    "dest_ip": "10.0.0.1",
    "protocol": "tcp",
    "port": 22,
    "content": "",
}
