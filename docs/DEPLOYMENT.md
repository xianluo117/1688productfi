# 1688 商品管理系统

该项目包含：

- [`1688-listing-date-collector.user.js`](../1688-listing-date-collector.user.js)：Tampermonkey 采集端。
- [`backend`](../backend)：FastAPI、PostgreSQL 数据模型、管理员会话、Token 上传和图片缓存。
- [`frontend`](../frontend)：Vue 3 管理后台。
- [`docker-compose.yml`](../docker-compose.yml)：Linux Docker Compose 部署入口。

## 功能

- 商品 ID 全局唯一，同一商品可以属于多个已有分组。
- 每次上传更新商品最新数据，并保留不可变历史快照。
- 后台计算每日笔数：上架天数大于 360 时使用 220 天，否则使用实际上架天数。
- 后台缓存远程商品图片，前端直接显示图片预览。
- 前端默认显示图片、商品 ID、上架天数、每日笔数。
- 表头点击排序，并将每个分组的默认排序字段和方向保存到数据库。
- 油猴每页采集完成后立即上传；使用幂等键和指数退避重试；过滤后为空的页面也会记录空批次；最终仍下载 CSV。

## Linux 部署

### 1. 环境要求

安装 Docker Engine 和 Docker Compose 插件。确认服务器可执行：

```bash
docker compose version
```

### 2. 配置密钥

在项目根目录执行：

```bash
cp .env.example .env
```

编辑 [`.env`](../.env.example)，至少替换：

- `POSTGRES_PASSWORD`
- `ADMIN_PASSWORD`
- `SESSION_SECRET`
- `INGEST_API_TOKEN`

建议生成随机密钥：

```bash
openssl rand -hex 32
```

初次部署且尚未启用 HTTPS 时，`COOKIE_SECURE=false`。反向代理提供 HTTPS 后改为 `true` 并重启服务。

### 3. 启动

默认从 Docker Hub 拉取以下镜像：

- `qingmiaoai/1688productfitt:backend`
- `qingmiaoai/1688productfitt:frontend`

```bash
docker compose pull
docker compose up -d
```

如需使用其他标签或镜像仓库，在 [`.env`](../.env.example) 中修改 `BACKEND_IMAGE` 和 `FRONTEND_IMAGE`。

默认只监听 `127.0.0.1:1788`，避免与常见服务端口冲突，并防止绕过反向代理直接公开服务。

查看状态和日志：

```bash
docker compose ps
docker compose logs -f backend frontend db
```

### 4. 分组管理

访问 `http://服务器地址:1788`，使用 [`.env`](../.env.example) 中的管理员账号密码登录，可查看和管理分组。油猴脚本也支持输入分组名称；服务器中不存在该名称时会自动创建。

### 5. 配置油猴

用新版 [`1688-listing-date-collector.user.js`](../1688-listing-date-collector.user.js) 覆盖 Tampermonkey 中的原脚本。

在 1688 搜索页面右上角面板中填写：

- 后台地址：例如 `https://products.example.com`，不要以 `/` 结尾；直接访问端口时可填写 `http://服务器地址:1788`。
- API Token：与 `INGEST_API_TOKEN` 完全一致。该 Token 会保存在 Tampermonkey 脚本存储中；请仅在可信浏览器配置文件中使用，避免与不可信用户共享该浏览器配置。
- 点击“保存配置并加载分组”。
- 可从下拉框选择已有分组，或在“输入分组”中填写名称。输入名称优先；分组不存在时由后台自动创建。
- 输入结束页并开始采集。

## 反向代理

将域名根路径代理到 `127.0.0.1:1788`。必须传递：

- `Host`
- `X-Real-IP`
- `X-Forwarded-For`
- `X-Forwarded-Proto`

Nginx 示例：

```nginx
location / {
    proxy_pass http://127.0.0.1:1788;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

启用 HTTPS 后，将 `COOKIE_SECURE=true`，并确保 `ALLOWED_ORIGINS` 包含 `https://s.1688.com`。Tampermonkey 使用特权跨域请求，但后台仍保留来源配置用于浏览器兼容和未来扩展。

## 备份与恢复

### PostgreSQL 备份

```bash
docker compose exec -T db pg_dump -U product_manager product_manager > product_manager.sql
```

恢复到空数据库：

```bash
docker compose exec -T db psql -U product_manager product_manager < product_manager.sql
```

### 图片备份

图片存储于 Docker 卷 `image_data`。可通过临时容器打包：

```bash
docker run --rm -v 1688数据采集_image_data:/data -v "$PWD":/backup alpine tar czf /backup/images.tar.gz -C /data .
```

实际卷名前缀取决于部署目录名称，可先执行 `docker volume ls` 查看。

## 升级

备份数据库与图片后更新 [`docker-compose.yml`](../docker-compose.yml) 和 [`.env`](../.env.example)，然后执行：

```bash
docker compose pull
docker compose up -d
```

当前第一版使用 SQLAlchemy 启动时创建表。正式长期迭代时应在修改既有字段前引入 Alembic 版本化迁移，避免直接依赖自动建表改变数据库结构。

## 镜像构建与发布状态

- [x] 后端镜像构建完成，构建阶段 Python 编译与 6 项 pytest 测试通过。
- [x] 前端镜像构建完成，Vite 构建与 Vitest 检查通过。
- [x] [`docker-compose.yml`](../docker-compose.yml) 已改为直接使用 Docker Hub 镜像，默认对外端口为 `1788`。
- [x] 油猴脚本支持输入分组名称，并通过采集 Token 接口自动创建或复用分组。
- [x] `qingmiaoai/1688productfitt:backend` 已推送，摘要为 `sha256:b6aeb2511f873a705523d1c57050d9f4a10f7c8316e970a929c1d40a98c85e27`。
- [x] `qingmiaoai/1688productfitt:frontend` 已推送，摘要为 `sha256:2fa13515f9d40d51d1ade27fc63fab9ad8dd888f6a262504a7882ebf378c0018`。

部署机验证命令：

```bash
docker compose config
docker compose pull
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:1788/api/health
```

上述命令全部成功后，再执行下面的浏览器验收清单。

## 浏览器验收清单

1. 后台创建两个名称不同的分组，重复名称应被拒绝。
2. 油猴能加载已有分组；输入新分组名称后应自动创建并选中；同名不同大小写或多余空格应复用已有分组；错误 Token 应显示 401 错误。
3. 采集一页后，后台立即出现商品，而不是等待全部页完成。
4. 重复上传同一页幂等键，不应产生重复历史快照；过滤后没有商品的页面应成功接受为 0 条空批次。
5. 同一商品采集到另一个分组后，应只存在一条商品主体，但两个分组均能看到。
6. 图片下载成功后，列表直接显示缓存图片；失败时显示占位区域。
7. 点击上架天数或每日笔数表头后排序改变，重新进入分组仍使用保存的排序。
8. 商品详情显示总笔数、总件数、计算天数和历史记录。
9. 上架天数为 449、总笔数为 43 时，每日笔数应约为 `0.195455`。
10. 完成采集后仍自动下载 CSV，CSV 包含分组与每页上传状态。
