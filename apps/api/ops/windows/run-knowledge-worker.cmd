@echo off
call C:\sites\ashiraai\llm-runtime.cmd
call C:\sites\ashiraai\knowledge-runtime.cmd
cd /d C:\sites\ashiraai\app
C:\sites\ashiraai\runtime\python312-embed\python.exe -m celery -A src.core.agent_celery_app.agent_celery_app worker --pool=solo --concurrency=1 --queues=knowledge --hostname=knowledge@ashiraai --loglevel=INFO >> C:\sites\ashiraai\logs\knowledge-worker.log 2>&1
