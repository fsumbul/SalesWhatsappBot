@echo off
call C:\sites\ashiraai\llm-runtime.cmd
cd /d C:\sites\ashiraai\app
C:\sites\ashiraai\runtime\python312-embed\python.exe -m uvicorn src.main:app --host 127.0.0.1 --port 8001 --proxy-headers >> C:\sites\ashiraai\logs\api.log 2>&1
