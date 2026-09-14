@echo off
set NODE_ENV=production
set API_BASE_URL=http://127.0.0.1:8001
set WEB_BASE_URL=https://api.ashiraai.com
cd /d C:\sites\ashiraai\releases\multi-company-20260914\web
"C:\Program Files\nodejs\node.exe" node_modules\next\dist\bin\next start --hostname localhost --port 3101 >> C:\sites\ashiraai\logs\review-web.log 2>&1
