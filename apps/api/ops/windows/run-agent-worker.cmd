@echo off
cd /d C:\sites\ashiraai\app
C:\sites\ashiraai\runtime\python312-embed\python.exe -m celery -A src.core.agent_celery_app.agent_celery_app worker --pool=solo --concurrency=1 --queues=agent_runtime --hostname=agent-runtime@ashiraai --loglevel=INFO >> C:\sites\ashiraai\logs\agent-worker.log 2>&1
