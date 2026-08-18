@echo off
cd /d C:\sites\ashiraai\app
C:\sites\ashiraai\runtime\python312-embed\python.exe -m celery -A src.core.agent_celery_app.agent_celery_app beat --schedule=C:\sites\ashiraai\agent-runtime-beat.db --loglevel=INFO >> C:\sites\ashiraai\logs\agent-recovery.log 2>&1
