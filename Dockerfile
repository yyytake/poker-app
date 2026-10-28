# 德州扑克联机服务
# 项目只用 Python 标准库，没有任何第三方依赖，
# 所以镜像非常小、构建也很快。

FROM python:3.12-slim

WORKDIR /app

# 复制项目源码（.dockerignore 已排除测试/备份等无关文件）
COPY . .

ENV PYTHONUNBUFFERED=1

# 长期账本 data/ledger.json 需要一个可写目录
RUN mkdir -p /app/data

# 云平台一般用 PORT 环境变量指定端口，server.py 会自动识别
EXPOSE 8000

CMD ["python3", "web/server.py"]
