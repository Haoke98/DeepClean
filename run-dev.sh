#!/bin/bash

# 保存当前目录
original_dir=$(pwd)

# 结束后端函数
cleanup() {
    echo "正在结束后端进程..."
    kill $backend_pid 2>/dev/null
    cd "$original_dir" || exit
}

# 捕获退出信号
trap cleanup EXIT

# 启动后端
echo "启动后端服务..."
/Users/shadikesadamu/anaconda3/envs/desktop-app/bin/python /Users/shadikesadamu/Projects/Cleaner/backend/main.py start-ui --port 5173 &
backend_pid=$!

# 等待端口就绪（最多10秒）
echo "等待后端端口 5173 就绪..."
timeout=10
while ! nc -z localhost 5173; do
    sleep 1
    timeout=$((timeout-1))
    if [ $timeout -eq 0 ]; then
        echo "错误：后端服务启动超时"
        exit 1
    fi
done

# 启动前端
echo "启动前端开发服务器..."
cd /Users/shadikesadamu/Projects/Cleaner/frontend || exit 1 && npm run dev

# 自动执行清理（当npm run dev退出时）
cleanup