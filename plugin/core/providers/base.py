from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any

class AIProvider(ABC):
    id = "base"
    @abstractmethod
    def list_models(self) -> list[str]:
        raise NotImplementedError
    @abstractmethod
    def chat(self, messages: list[dict[str, Any]], model: str) -> str:
        raise NotImplementedError
