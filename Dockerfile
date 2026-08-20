# ==================== 前端构建阶段 ====================
# 使用 DaoCloud 加速地址拉取 docker.io 镜像(国内网络直连 docker.io 不稳定)
FROM docker.m.daocloud.io/library/node:20-alpine AS frontend-build

ARG NPM_REGISTRY=https://registry.npmmirror.com
WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm config set registry ${NPM_REGISTRY} && npm ci

COPY frontend/ ./
RUN npm run build

# ==================== 后端运行阶段 ====================
FROM docker.m.daocloud.io/library/python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install -i ${PIP_INDEX_URL} -r requirements.txt

COPY backend/ ./

# 前端构建产物:main.py 按 <根>/frontend/dist 路径挂载
COPY --from=frontend-build /build/frontend/dist /app/frontend/dist

# settings.py 要求 backend/.env 存在;容器内值全部走环境变量注入,
# 这里只创建空文件占位(backend/.env 已被 .dockerignore 排除,不会进镜像)
RUN touch /app/backend/.env

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
