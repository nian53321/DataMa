# ---------- 构建阶段 ----------
FROM node:20-alpine AS builder
WORKDIR /app

# 先拷依赖清单，利用 Docker 层缓存
COPY package.json package-lock.json* ./
RUN npm install --registry=https://registry.npmmirror.com

# 拷源码并构建
COPY . .
RUN npm run build

# ---------- 运行阶段 ----------
FROM nginx:1.27-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
