@echo off
rem Non-secret model routing overrides. Keep production secrets in app\.env untouched.
set "LLM_PROVIDER=chat_compatible"
set "LLM_MODEL=qwen3:8b"
set "LLM_BASE_URL=http://127.0.0.1:11435/v1"
