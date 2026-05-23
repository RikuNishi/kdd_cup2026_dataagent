"""
LLM呼び出しサンプルスクリプト
model: hosted_vllm/qwen35-35b-a3b
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from data_agent_baseline.agents.model import ModelMessage, OpenAIModelAdapter

MODEL = "hosted_vllm/qwen35-35b-a3b"
API_BASE = "http://10.164.41.191:4000/v1"
API_KEY = os.environ.get("LLM_API_KEY", "sk-fbhnF73RYfBE4ia2Goe7RQ")

adapter = OpenAIModelAdapter(
    model=MODEL,
    api_base=API_BASE,
    api_key=API_KEY,
    temperature=0.0,
    max_output_tokens=512,
    request_timeout_seconds=60.0,
    max_retries=2,
)

messages = [
    ModelMessage(role="user", content="Hello! Please introduce yourself briefly."),
]

response = adapter.complete(messages)
print(response)
