"""Adapters for self-hosted NVIDIA NIM containers.

Every client here talks to a container the operator runs on their own GPU
host (``docker run --gpus all nvcr.io/nim/...``). Nothing in this package may
be pointed at the public build.nvidia.com trial endpoints from application
code: those log inputs, and customer/tenant data never leaves the deployment
(``docs/project-handoff.md`` "LLM boundary",
``docs/nvidia-nim-harness-agents-plan-2026-09-16.md`` §1).
"""

from .http import NimError, NimHttp, NimUnavailableError

__all__ = ["NimError", "NimHttp", "NimUnavailableError"]
