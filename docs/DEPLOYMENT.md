# 1688 商品管理系统

## 1688 图片同款识别

系统使用 pHash 与图片 Embedding 建立跨用户共享的 1688 同款簇。商品列表按簇去重，并按总件数、每日笔数、最近采集时间选择全局代表商品。

部署要求：

- 数据库使用 `pgvector/pgvector:pg16`，迁移会启用 `vector` 扩展。
- `migrate` 服务先完成数据库迁移，随后后端与独立 `similarity-worker` 才会启动。
- 后端与独立 `similarity-worker` 共用数据库和图片卷。
- `.env` 不需要填写 `AI_SECRET_KEY`。管理员面板保存的第三方 API Key 默认由 `SESSION_SECRET` 加密，因此必须保持 `SESSION_SECRET` 稳定。
- 向量存储固定为 1536 维。实际图片模型可输出不超过 1536 维的向量，后端归一化后补零，允许后续切换模型维度而不重建列类型。

管理员在 1688 页面进入“AI 同款”后完成配置：

1. 填写 OpenAI 兼容服务的自定义 Base URL，例如 `https://newapi.momoaisite.com/v1`。也可直接填写完整的 `/embeddings` 地址。
2. 在管理员面板填写并保存第三方 API Key、模型名和实际输出维度，无需写入 `.env`。
3. 默认请求体为 `model + input`，默认响应路径为 `data.0.embedding`；第三方接口不一致时再修改请求 JSON 模板与响应路径。
4. 设置 TopK、pHash 自动距离、审核阈值、自动合并阈值和自动合并开关。
5. 保存为当前模型后，存量缓存图片会渐进排队处理。

填写 Base URL 时，后端自动追加 `/embeddings`。请求模板支持双大括号占位符：`model`、`image_data_url`、`image_base64`、`image_url`、`product_id`。

模型、维度、接口或模板变化时会创建新版本。旧向量继续保留，新版本后台渐进重算，避免直接破坏在线索引。

升级命令：

```text
docker compose pull
docker compose up -d
docker compose ps
```

从普通 PostgreSQL 16 镜像切换至 pgvector PostgreSQL 16 时保留原 `postgres_data` 卷，禁止删除数据卷。

## 平台入口

登录成功后首先进入平台选择首页，可进入 1688 或 Temu 产品系统。

Temu 数据规则：

- Temu 原生商品 ID 是全局唯一键，任一用户上传同 ID 都更新全局当前值。
- 云启 `gdid` 仅是辅助字段，允许与 Temu 原生商品 ID 不同；云启 Shadow DOM 位于对应原生商品卡片内时视为可靠容器关联。
- Temu 分组、成员关系、批次和历史快照按用户隔离。
- 管理员可按用户切换 Temu 数据视角。
- Temu 列表默认隐藏广告，筛选面板可开启显示。
- 半托管且包含 `Local` 标签显示为“半托Y1”，其他半托管显示为“半托Y2”。
- Temu 油猴脚本同时支持 CSV 导出和个人令牌上传，每次 See more 后等待 5 秒再上传可靠关联记录。

升级会自动执行 Alembic 迁移 `20260808_01` 并创建 Temu 独立数据表，升级前必须备份 PostgreSQL。

## 组成

- [`1688-listing-date-collector.user.js`](../1688-listing-date-collector.user.js)：1688 Tampermonkey 采集端。
- [`temu-product-collector.user.js`](../temu-product-collector.user.js)：Temu 与云启数据 Tampermonkey 采集端。
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

Docker Compose 先运行一次性 `migrate` 服务执行 Alembic 升级，成功后才启动后端和图片相似度 worker。首次升级多用户版本时：

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

分别安装或完整覆盖最新版：

- [`1688-listing-date-collector.user.js`](../1688-listing-date-collector.user.js)
- [`temu-product-collector.user.js`](../temu-product-collector.user.js)

Temu 脚本需要 Tampermonkey 授权 `GM_xmlhttpRequest` 和目标后台域名连接权限。安装或升级脚本时应确认授权提示；请求由 Tampermonkey 发出，不依赖 Temu 页面 CORS。

在采集面板填写：

- 后台地址：例如 `https://products.example.com`，不要以 `/` 结尾。
- 个人采集令牌：管理员创建用户或重置令牌时提供的 `zoo_...` 值。
- 已有分组或输入分组名称。分组不存在时在当前令牌用户下自动创建。

不要继续使用旧 [`.env`](../.env) 中的 `INGEST_API_TOKEN`。

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

## 本次发布

- Git 提交：`f15cfc9 feat: add multi-user data isolation and filters`
- 后端镜像：`qingmiaoai/1688productfitt:backend`
- 后端摘要：`sha256:f67d8a2c44a066e282a91bc0899910ec00e7ccddea699cdf603d2e7f26587cae`
- 前端镜像：`qingmiaoai/1688productfitt:frontend`
- 前端摘要：`sha256:c2a28777e9c10090898c593414df5befae8f1a509bab4b00192d5e46ebbdaee3`
- 后端镜像构建阶段通过 9 项测试。
- 前端镜像构建阶段通过 Vite 构建与 Vitest 检查。

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
11. Temu 脚本可从 `www.temu.com` 通过 Tampermonkey 跨域请求加载分组并上传批次。
12. 云启 `gdid` 与 Temu 原生商品 ID 不同时，同一原生商品卡片内的云启 Shadow DOM 仍可按可靠容器关联上传，并以原生 ID 更新商品。
13. Temu 列表默认不显示广告；开启“显示广告产品”后才显示广告记录。
