# 1688 商品管理系统

## 组成

- [`1688-listing-date-collector.user.js`](../1688-listing-date-collector.user.js)：Tampermonkey 采集端。
- [`backend`](../backend)：FastAPI、PostgreSQL、Alembic、用户认证、采集授权和图片缓存。
- [`frontend`](../frontend)：Vue 3 管理后台。
- [`docker-compose.yml`](../docker-compose.yml)：Docker Compose 部署入口。

## 权限与数据规则

- 管理员创建、启用或禁用用户，并可重置密码和个人采集令牌。
- 普通用户只能查看和管理自己的分组、分组成员、采集批次和历史快照。
- 管理员可选择任意用户，查看该用户的分组、商品和历史。
- 商品基础记录及列表中的最新指标全局共享：任意用户的新上传会更新商品当前值。
- 商品详情中的分组和历史按当前查看用户过滤。
- 分组名称只要求在同一用户内唯一；不同用户可使用相同分组名称。
- 个人采集令牌只保存 SHA-256 哈希；明文仅在创建或重置时显示一次。
- 旧版全局 `INGEST_API_TOKEN` 升级后立即失效。

## 商品筛选

商品列表支持组合筛选：

- 上架日期起止。
- 上架天数最小值和最大值。
- 总笔数最小值和最大值。
- 总件数最小值和最大值。
- 每日笔数最小值和最大值。
- 最后采集时间起止。

筛选条件与分页、排序同时生效。

## 部署

### 1. 环境要求

安装 Docker Engine 和 Docker Compose 插件。

```bash
docker compose version
```

### 2. 备份旧数据

升级多用户版本前必须备份数据库和图片。

```bash
docker compose exec -T db pg_dump -U product_manager product_manager > product_manager.sql
```

图片位于 Docker 卷 `image_data`。

### 3. 配置环境

```bash
cp .env.example .env
```

至少替换：

- `POSTGRES_PASSWORD`
- `ADMIN_PASSWORD`
- `SESSION_SECRET`

个人采集令牌不写入 [`.env.example`](../.env.example)，由管理员登录后创建或重置。

初次部署且未启用 HTTPS 时使用 `COOKIE_SECURE=false`。启用 HTTPS 后改为 `true`。

### 4. 启动或升级

```bash
docker compose pull
docker compose up -d
```

后端启动时自动执行 Alembic 升级。首次升级多用户版本时：

1. 现有管理员账号标记为管理员角色。
2. 现有分组、成员关系、批次和快照归属该管理员。
3. 旧全局采集令牌不迁移，立即失效。

默认镜像：

- `qingmiaoai/1688productfitt:backend`
- `qingmiaoai/1688productfitt:frontend`

默认监听 `127.0.0.1:1788`。

```bash
docker compose ps
docker compose logs -f backend frontend db
curl -fsS http://127.0.0.1:1788/api/health
```

## 用户管理

1. 访问 `http://服务器地址:1788`。
2. 使用 [`.env.example`](../.env.example) 对应的管理员账号登录。
3. 打开“用户”页面。
4. 创建用户并立即复制本次显示的个人采集令牌。
5. 将账号、初始密码和个人采集令牌分别交付用户。
6. 用户离职或停止使用时禁用账号；禁用后网站登录和采集令牌同时失效。
7. 重置令牌后旧令牌立即失效。

## 配置油猴

使用最新版 [`1688-listing-date-collector.user.js`](../1688-listing-date-collector.user.js) 完整覆盖旧脚本。

在采集面板填写：

- 后台地址：例如 `https://products.example.com`，不要以 `/` 结尾。
- 个人采集令牌：管理员创建用户或重置令牌时提供的 `zoo_...` 值。
- 已有分组或输入分组名称。分组不存在时在当前令牌用户下自动创建。

不要继续使用旧 `.env` 中的 `INGEST_API_TOKEN`。

## 反向代理

```nginx
location / {
    proxy_pass http://127.0.0.1:1788;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

## 恢复

恢复到空数据库：

```bash
docker compose exec -T db psql -U product_manager product_manager < product_manager.sql
```

## 验收清单

1. 管理员可以创建普通用户，并且明文采集令牌只在创建响应中显示。
2. 普通用户只能看到自己的分组；不同用户可创建同名分组。
3. 禁用用户后，该用户网站会话和个人采集令牌均不可继续使用。
4. 油猴使用个人令牌加载、创建分组并上传，数据归属令牌用户。
5. 用户无法通过修改分组 ID 上传到其他用户的分组。
6. 管理员可以切换用户查看分组、商品和该用户历史。
7. 普通用户商品详情不返回其他用户的分组和历史。
8. 任意用户上传同一商品后，全局商品当前指标更新。
9. 上架日期/天数、总笔数、总件数、每日笔数和采集时间筛选可组合使用。
10. 升级前已有数据全部出现在管理员用户视角中。
