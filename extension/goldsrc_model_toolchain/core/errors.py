"""Stable error protocol shared by the operator and runtime API."""

from __future__ import annotations

from typing import Any


class ToolchainError(RuntimeError):
    def __init__(
        self,
        phase: str,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.phase = str(phase)
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})
        super().__init__(f"[{self.phase}:{self.code}] {self.message}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }
