# 后端镜像：以仓库根为 build context（需访问 data/medical.json、dict/、build_medicalgraph.py）
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 先装依赖，利用层缓存
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# 复制全部源码（build_medicalgraph.py 在根、backend/ 应用、data/medical.json 语料、dict/ 词典）
COPY . /app

RUN chmod +x /app/backend/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["sh", "/app/backend/entrypoint.sh"]
