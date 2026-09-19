"""Audit logging for the RAG subsystem, reusing the agent JSONL audit log."""

from __future__ import annotations

try:  # Package import (backend.rag.*)
    from ..agent.audit import JsonlAuditLog
except ImportError:  # Flat import (backend on sys.path)
    from agent.audit import JsonlAuditLog

__all__ = ["JsonlAuditLog"]
