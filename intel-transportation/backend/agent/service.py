"""Modern LangChain agent service with isolated short-term memory."""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from .audit import JsonlAuditLog
from .config import AgentSettings
from .contracts import AssistantData
from .prompts import SYSTEM_PROMPT
from .tools import TrafficToolbox, TrafficToolGateway, finish_tool_trace, start_tool_trace

LOGGER = logging.getLogger(__name__)


class AgentUnavailableError(RuntimeError):
    """Raised when the optional LLM-backed agent is not configured or available."""


class TrafficAgentService:
    def __init__(self, settings: AgentSettings, gateway: TrafficToolGateway):
        self.settings = settings
        self.toolbox = TrafficToolbox(gateway)
        self.audit = JsonlAuditLog(settings.audit_path)
        self._agent: Any = None
        self._load_error = ""
        self._initialise()

    def _initialise(self) -> None:
        if not self.settings.enabled:
            self._load_error = "TRAFFIC_AGENT_ENABLED 未启用"
            return
        if not self.settings.api_key:
            self._load_error = "未配置 TRAFFIC_AGENT_API_KEY 或 OPENAI_API_KEY"
            return
        try:
            from langchain.agents import create_agent
            from langchain_openai import ChatOpenAI
            from langgraph.checkpoint.memory import InMemorySaver

            model_kwargs: dict[str, Any] = {
                "model": self.settings.model,
                "api_key": self.settings.api_key,
                "temperature": self.settings.temperature,
                "timeout": self.settings.timeout_seconds,
                "max_retries": self.settings.max_retries,
            }
            if self.settings.base_url:
                model_kwargs["base_url"] = self.settings.base_url
            model = ChatOpenAI(**model_kwargs)
            self._agent = create_agent(
                model=model,
                tools=self.toolbox.as_langchain_tools(),
                system_prompt=SYSTEM_PROMPT,
                checkpointer=InMemorySaver(),
            )
        except ImportError:
            self._load_error = "交通问答 Agent 依赖尚未安装"
            LOGGER.exception("Traffic agent dependencies are unavailable")
        except Exception:  # Agent is optional and must not break prediction API startup.
            self._load_error = "交通问答 Agent 初始化失败"
            LOGGER.exception("Traffic agent initialisation failed")

    @property
    def available(self) -> bool:
        return self._agent is not None

    def health(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.enabled,
            "configured": self.settings.configured,
            "available": self.available,
            "provider": self.settings.provider,
            "model": self.settings.model,
            "memory": "in-process thread_id checkpoint",
            "audit_enabled": True,
            "error": self._load_error,
        }

    @staticmethod
    def _message_text(message: Any) -> str:
        content = getattr(message, "content", message)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            return "\n".join(part for part in parts if part).strip()
        return str(content).strip()

    def query(
        self,
        *,
        question: str,
        thread_id: str,
        checkpoint_id: str | None = None,
    ) -> AssistantData:
        if not self.available:
            raise AgentUnavailableError(self._load_error or "交通问答 Agent 不可用")

        trace_id = uuid4().hex
        prompt = question
        if checkpoint_id:
            prompt = f"默认卡口为 {checkpoint_id}。\n用户问题：{question}"
        token = start_tool_trace()
        started = time.perf_counter()
        evidence = []
        try:
            result = self._agent.invoke(
                {"messages": [{"role": "user", "content": prompt}]},
                config={
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": self.settings.max_iterations * 2 + 1,
                },
            )
            messages = result.get("messages", []) if isinstance(result, dict) else []
            answer = self._message_text(messages[-1]) if messages else ""
            if not answer:
                raise RuntimeError("模型没有返回可显示的回答")
            evidence = finish_tool_trace(token)
        except Exception as exc:
            evidence = finish_tool_trace(token)
            self.audit.append(
                {
                    "trace_id": trace_id,
                    "thread_id": thread_id,
                    "status": "error",
                    "question": question,
                    "checkpoint_id": checkpoint_id,
                    "tool_evidence": [item.model_dump() for item in evidence],
                    "error_type": type(exc).__name__,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            )
            raise RuntimeError("交通问答模型调用失败") from exc

        limitations = [
            "当前数据不能证明真实 OD、旅行时间或拥堵传播。",
            "路线规划和现实交通控制尚未接入，处置动作需要人工审批。",
        ]
        data = AssistantData(
            trace_id=trace_id,
            thread_id=thread_id,
            answer=answer,
            provider=self.settings.provider,
            model=self.settings.model,
            tool_evidence=evidence,
            limitations=limitations,
            requires_approval=False,
            simulation=False,
        )
        self.audit.append(
            {
                "trace_id": trace_id,
                "thread_id": thread_id,
                "status": "success",
                "question": question,
                "checkpoint_id": checkpoint_id,
                "provider": self.settings.provider,
                "model": self.settings.model,
                "tool_evidence": [item.model_dump() for item in evidence],
                "answer": answer,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
        return data
