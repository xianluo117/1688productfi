# 开发者 README

本文面向代码维护者和接口接入人员。安装和账号操作见[部署与管理员指南](../docs/DEPLOYMENT.md)，日常使用见[用户 README](../README.md)。

## 1. 技术与运行结构

| 层级 | 技术 |
|---|---|
| 采集端 | Tampermonkey 浏览器脚本 |
| 前端 | Vue 3、Vite，构建后由 Nginx 提供静态页面 |
| API | FastAPI、Pydantic、SQLAlchemy 2、psycopg 3 |
| 数据库 | PostgreSQL 16、pgvector，Alembic 管理迁移 |
| 图片处理 | Pillow、ImageHash、外部图片 Embedding 服务 |
| 外部图搜 | 独立图搜 API，后台任务创建并轮询远程任务 |
| 部署 | Docker Compose，API 与三个 Worker 独立进程运行 |

容器构建基于 Python 3.12 和 Node.js 22。具体依赖版本见 [`backend/requirements.txt`](../backend/requirements.txt) 和 [`frontend/package.json`](../frontend/package.json)。

### 服务职责

| Compose 服务 | 职责 |
|---|---|
| db | 商品、账号、历史、配置、向量和任务持久化 |
| migrate | 执行数据库升级，成功退出后允许 API 与 Worker 启动 |
| backend | HTTP API、认证、图片静态服务和管理员初始化 |
| similarity-worker | 图片向量化及相似度相关任务 |
| temu-image-worker | Temu 图片下载任务 |
| temu-image-search-worker | Temu 到 1688 的外部以图找图任务 |
| frontend | 网页入口，反向代理 /api/ 与 /images/ 到后端 |

API 启动生命周期也会执行迁移并初始化管理员。数据库使用 postgres_data 卷；后端、相似度 Worker、图片下载 Worker 共享 image_data 卷。图搜 Worker 未挂载图片卷，依赖图片 URL 和数据库任务信息。

## 2. 代码导航

| 位置 | 内容 |
|---|---|
| [`1688-listing-date-collector.user.js`](../1688-listing-date-collector.user.js) | 1688 采集面板、数据提取与上传 |
| [`temu-product-collector.user.js`](../temu-product-collector.user.js) | Temu 原生商品与云启数据关联、上传和 CSV 导出 |
| [`backend/app/main.py`](../backend/app/main.py) | 应用生命周期、路由注册、账号与 1688 接口 |
| [`backend/app/auth.py`](../backend/app/auth.py) | 密码、会话、个人采集令牌和角色检查 |
| [`backend/app/config.py`](../backend/app/config.py) | 环境配置及默认值 |
| [`backend/app/database.py`](../backend/app/database.py) | 原有模块同步数据库引擎和会话 |
| [`backend/app/models.py`](../backend/app/models.py) / [`schemas.py`](../backend/app/schemas.py) / [`services.py`](../backend/app/services.py) | 原有数据模型、请求响应与 1688 业务 |
| [`backend/app/temu_router.py`](../backend/app/temu_router.py) / [`temu_services.py`](../backend/app/temu_services.py) | Temu 接口与业务 |
| [`backend/app/temu_favorite_services.py`](../backend/app/temu_favorite_services.py) | 收藏和 XLSX 导出 |
| [`backend/app/similarity_services.py`](../backend/app/similarity_services.py) / [`similarity_worker.py`](../backend/app/similarity_worker.py) | 同款、向量与跨平台关系处理 |
| [`backend/app/temu_image_download_services.py`](../backend/app/temu_image_download_services.py) / [`temu_image_download_worker.py`](../backend/app/temu_image_download_worker.py) | 图片下载队列 |
| [`backend/app/temu_image_search_services.py`](../backend/app/temu_image_search_services.py) / [`temu_image_search_worker.py`](../backend/app/temu_image_search_worker.py) | 外部图搜队列和任务控制 |
| [`backend/app/temu_image_search_client.py`](../backend/app/temu_image_search_client.py) | 图搜服务客户端 |
| [`backend/app/owned_products/`](../backend/app/owned_products/) | 自有产品独立模块：异步会话、模型、Schema、服务、路由 |
| [`backend/migrations/`](../backend/migrations/) | Alembic 迁移 |
| [`backend/tests/`](../backend/tests/) | 现有后端测试 |
| [`frontend/src/App.vue`](../frontend/src/App.vue) | 登录、页面入口与模块切换 |
| [`frontend/src/components/`](../frontend/src/components/) | 商品、用户、相似度、图片及图搜管理页面 |
| [`frontend/src/api.js`](../frontend/src/api.js) | 前端 API 请求封装 |
| [`frontend/nginx.conf`](../frontend/nginx.conf) | 容器静态页面和 API、图片代理 |
| [`docker-compose.yml`](../docker-compose.yml) | 镜像、环境变量、启动顺序和持久卷 |

## 3. 数据与任务约定

### 采集业务

- 商品当前信息全局共享；分组、成员关系、采集批次和历史快照按用户归属查询。
- Temu 原生商品 ID 为全局键；云启辅助 ID 不能代替原生 ID。同一原生商品容器内的数据关联由脚本处理。
- 网页采用 Cookie 会话；采集接口使用个人 Bearer 令牌。令牌明文只在签发时返回，数据库保存哈希。
- 管理员可切换数据归属用户，普通用户只能查询自己的业务视角。

### 相似度

- 使用 pHash 和图片向量建立同款关系，并支持跨平台候选。
- 向量列固定为 1536 维，实际模型输出不超过该维度；归一化后补零存储。
- 模型、维度、接口或模板变化会产生新版本，旧向量保留，新版本渐进处理。
- 请求模板占位符支持 model、image_data_url、image_base64、image_url、product_id；默认响应路径为 data.0.embedding。
- 外部服务密钥默认使用 SESSION_SECRET 加密，配置变更时需考虑已保存密钥的解密。

### 图搜任务

- 图片就绪后按配置创建持久化任务，由独立 Worker 消费，不依赖 API 进程内后台任务。
- 调用分为上传任务与商品检索任务两个阶段，保存远程任务 ID 和进度。
- 暂停保留断点；停止尝试取消远程任务并清除本地断点，继续后重新上传。
- 并发数动态读取管理员配置；降低并发不强制取消正在运行的任务。
- 外部图搜候选与人工确认的跨平台货源关系分别保存。

## 4. 构建与开发

### 推荐：以容器运行当前源码

现有 Compose 只有镜像引用，没有源码构建声明。直接执行启动命令或更新本地源码，不会自动构建应用。

先按[部署指南](../docs/DEPLOYMENT.md)准备环境配置，再在项目根目录手动构建：

```text
docker build -t product-manager-backend:local ./backend
docker build -t product-manager-frontend:local ./frontend
```

将环境配置中的 BACKEND_IMAGE 改为 product-manager-backend:local，FRONTEND_IMAGE 改为 product-manager-frontend:local，然后启动：

```text
docker compose up -d
docker compose ps -a
```

源码更新后重新构建对应镜像，再更新容器。后端和三个 Worker 必须使用同版本镜像。此方式不需要向宿主机额外暴露数据库端口。

### 本地开发注意事项

- 后端原生启动时，工作目录应为 [`backend/`](../backend/)，便于找到 [`alembic.ini`](../backend/alembic.ini)。配置加载使用当前工作目录的环境文件；根目录的 Compose 配置不会自动成为后端进程环境。
- 原生后端需要自行设置可连接的 DATABASE_URL、ADMIN_PASSWORD、SESSION_SECRET 和可写的 IMAGE_STORAGE_PATH。Compose 默认没有发布数据库端口，容器内数据库主机名不能直接用于宿主机进程。
- Windows 原生 psycopg 异步连接需要兼容的 Selector 事件循环。应用没有额外提供 Windows 启动兼容层，包含自有产品接口的完整开发环境推荐使用容器。
- 前端依赖安装使用 npm ci。在 [`frontend/package.json`](../frontend/package.json:6) 中定义了开发、构建和测试命令，由开发者在前端目录手动运行。
- [`frontend/vite.config.js`](../frontend/vite.config.js) 当前没有配置 API 代理。仅启动 Vite 不等于完整联调环境，需要自行配置同源代理，将 /api/、/images/ 指向后端。

### 检查与迁移

[`backend/Dockerfile`](../backend/Dockerfile:15) 构建时进行 Python 编译检查和现有 pytest 测试；[`frontend/Dockerfile`](../frontend/Dockerfile:6) 构建时执行 Vite 构建及 Vitest。前端测试命令允许没有测试文件时通过，不能将其视为已有完整前端测试覆盖。

在已启动的容器环境中检查数据库迁移状态：

```text
docker compose exec backend alembic current
docker compose exec backend alembic heads
```

新增模型时同步登记迁移元数据，基于当前迁移头新增迁移。不要仅修改 ORM 模型而省略数据库升级。当前自有产品迁移为 [`20261030_01_owned_products.py`](../backend/migrations/versions/20261030_01_owned_products.py)。

## 5. API 文档入口

FastAPI 默认提供 /docs 和 /openapi.json，需直接访问后端 HTTP 入口。

**默认 Compose 不发布后端 8000 端口，前端 Nginx 也没有代理这两个路径。** 因此访问前端域名下的 /docs 不会自动打开 Swagger。开发环境需要显式开放后端入口或增加相应代理；不必为正常网页操作开放它们。

## 6. 自有产品接口

该模块只负责保存和查询外部业务结果，不执行上游工作流，没有前端、删除接口、自动统计或飞书同步。

**当前接口没有登录或令牌鉴权依赖，用户名只是外部业务字段，不继承采集业务的用户隔离。** 默认 /api/ 代理会转发这些接口，部署和接入时应明确这一实际行为。

接口前缀为 /api/owned-products，路由定义见 [`router.py`](../backend/app/owned_products/router.py:24)。

| 操作 | 方法与路径（相对前缀） |
|---|---|
| 新增、分页查询批次 | POST、GET /batches |
| 部分修改、查询批次 | PATCH、GET /batches/{batch_id} |
| 新增、分页查询货号 | POST、GET /batches/{batch_id}/items |
| 部分修改、查询货号 | PATCH、GET /items/{item_id} |
| 按新货号聚合查询 | GET /items/by-new-code?new_item_code=货号原文 |
| 新增、分页查询颜色 | POST、GET /items/{item_id}/colors |
| 部分修改、查询颜色 | PATCH、GET /colors/{color_id} |

### 接入顺序与语义

1. 创建批次并保存返回的数据库 ID。
2. 用批次 ID 创建货号并保存货号记录 ID。
3. 用货号记录 ID 创建颜色。
4. 后续使用对应记录 ID 部分更新或查询。

- 新增每次产生新记录，不按业务字段去重，不提供重复提交幂等性。
- 未提交字段保持不变；显式空值清空可空字段；对象和数组整体替换。
- 主键、父关联和系统时间不能修改；子记录新增不更新父记录状态。
- 普通列表默认第 1 页、每页 30 条，按 ID 倒序；详情只返回当前层。
- 新货号查询精确匹配，不改写大小写或空格，跨全部批次返回所有匹配记录、各自所属批次及全部颜色，不分页、不去重。
- 新增成功返回 201，更新和查询成功返回 200，不存在记录返回 404，非法请求返回 422。

字段与响应结构见 [`schemas.py`](../backend/app/owned_products/schemas.py)。详细设计、最终约定和历史验证记录见[自有产品数据聚合执行方案](自有产品数据聚合执行方案.md)。

### 并发和事务

自有产品模块独立使用异步 SQLAlchemy 引擎和每请求会话，复用 psycopg 驱动；原有模块继续使用同步连接，未整体切换为异步。

每次写入是独立短事务，提交成功后返回；不同记录可并发操作，单个请求失败不回滚其他已提交请求。调用方避免同时修改同一记录，不提供版本冲突检测、自动合并或自动重试。

应用关闭时释放异步连接池。连接预算需合计每个 API 进程的同步、异步池及各 Worker 的连接，不能仅按单个模块估算。

## 7. 维护文档

- 用户操作变化：更新[用户 README](../README.md)。
- 部署参数或管理员配置变化：更新[部署与管理员指南](../docs/DEPLOYMENT.md)。
- 模块结构、接口和运行机制变化：更新本目录中的开发者文档。
- 按执行方案开发时，同步维护原计划清单，不把历史验证记录当作当前版本的运行结果。
