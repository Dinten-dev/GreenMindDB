#!/bin/bash
scp -i dev-tools/greenmind_deploy_key -o StrictHostKeyChecking=no get_token.py traver@188.245.247.156:/tmp/get_token.py
ssh -i dev-tools/greenmind_deploy_key -o StrictHostKeyChecking=no traver@188.245.247.156 "docker cp /tmp/get_token.py greenminddb-backend-1:/app/get_token.py && docker exec greenminddb-backend-1 python /app/get_token.py"
