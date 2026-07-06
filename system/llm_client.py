# 网闸AI设备智能体 - LLM客户端模块
# DeepSeek API封装

import time
import httpx


class LLMClient:
    """DeepSeek API 客户端封装"""

    def __init__(self, api_key, base_url="https://api.deepseek.com/v1",
                 model="deepseek-chat", timeout=60):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    def chat(self, messages, temperature=0.7, max_tokens=4096, stream=False, response_format=None):
        """调用 DeepSeek Chat 接口"""
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        # 支持结构化输出（仿照现有 payload 构造模式）
        if response_format:
            payload["response_format"] = response_format
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        start = time.time()
        resp = self._client.post(url, json=payload, headers=headers)
        elapsed = time.time() - start
        if resp.status_code != 200:
            raise RuntimeError(
                f"DeepSeek API error (HTTP {resp.status_code}): {resp.text}"
            )
        try:
            result = resp.json()
        except Exception as e:
            raise RuntimeError(f"响应JSON解析失败: {e}")
        result["_elapsed_sec"] = round(elapsed, 2)
        return result

    def extract_content(self, response):
        try:
            return response["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            return ""

    def close(self):
        self._client.close()
