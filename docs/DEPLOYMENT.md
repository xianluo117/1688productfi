# 部署与管理员指南

本文面向安装和管理系统的人员。日常操作见[用户 README](../README.md)，源码构建、架构和接口说明见[开发者 README](../about-dev/README.md)。

## 1. 环境准备

安装 Docker Engine 和 Docker Compose 插件；Windows 可使用 Docker Desktop 的 Linux 容器模式。

在项目根目录执行以下命令检查 Compose：

```text
docker compose version
```

本指南使用项目提供的预构建镜像。直接更新源码不会更新容器内程序；需要运行本地源码时，请参考开发者文档构建镜像。

## 2. 配置环境

将 [`.env.example`](../.env.example) 复制为项目根目录的 [`.env`](../.env)，用文本编辑器填写配置。

| 配置 | 用途 |
|---|---|
| POSTGRES_DB / POSTGRES_USER | 数据库名称与账号，默认均为 product_manager |
| POSTGRES_PASSWORD | 数据库密码，必须替换示例值 |
| ADMIN_USERNAME | 首次初始化的管理员用户名，默认 admin |
| ADMIN_PASSWORD | 首次初始化的管理员密码，至少 12 个字符 |
| SESSION_SECRET | 会话及服务密钥加密使用的秘密值，建议至少 32 个随机字符，必须替换示例值并长期保持稳定 |
| COOKIE_SECURE | HTTP 访问用 false；反向代理启用 HTTPS 后改为 true |
| APP_BIND_ADDRESS | 默认 127.0.0.1，仅服务器本机可访问 |
| APP_PORT | 对外入口端口，默认 1788 |
| ALLOWED_ORIGINS | 允许的浏览器来源，多个来源以逗号分隔 |
| BACKEND_IMAGE / FRONTEND_IMAGE | 后端、前端镜像，默认使用示例中的仓库与标签 |

数据库密码会参与连接地址拼接，建议使用不含 URL 保留字符的随机密码。

- 不配置全局采集令牌。个人令牌在用户管理中创建或重置。
- AI 和图搜服务地址、API Key 在管理员控制台保存，不需要额外配置 AI_SECRET_KEY。
- 不随意更换 SESSION_SECRET，否则现有登录会话和已保存服务密钥的解密会受影响。
- 已存在的管理员账号应在用户管理中重置密码，不把修改初始化配置当作密码重置操作。

### 访问方式

- **本机使用**：保持默认配置，访问 http://127.0.0.1:1788。
- **直接远程访问**：将 APP_BIND_ADDRESS 改为 0.0.0.0，开放相应端口，再访问服务器地址的 1788 端口。
- **域名访问**：保持本机监听，由外层反向代理将域名请求转发到 http://127.0.0.1:1788，并配置 HTTPS。

反向代理需保留原请求路径，至少确保页面、API 和 /images/ 图片路径均可访问。反向代理位于其他容器时，127.0.0.1 指向该容器自身，需改用实际可达的宿主机或网络地址。

## 3. 启动

在项目根目录执行：

```text
docker compose pull
docker compose up -d
docker compose ps -a
```

首次启动会初始化数据库并执行迁移。迁移成功后启动后端和三个后台处理服务，再启动网页入口。一次性 migrate 服务成功退出属于正常状态。

打开配置的地址，使用初始化管理员账号登录。默认镜像为：

- 后端：qingmiaoai/1688productfitt:backend
- 前端：qingmiaoai/1688productfitt:frontend

检查服务状态：

```text
docker compose logs --tail 100 migrate backend
docker compose logs --tail 100 similarity-worker temu-image-worker temu-image-search-worker
```

浏览器访问 http://127.0.0.1:1788/api/health 可检查 API 是否响应；使用远程地址时相应替换域名和端口。

## 4. 创建用户和配置采集

1. 进入管理员控制台的“用户管理”。
2. 创建用户，立即保存本次显示的个人采集令牌。
3. 将系统地址、账号、初始密码和令牌交给用户。
4. 按[用户 README](../README.md)安装脚本，并配置后台地址和分组。

令牌遗失后只能重置；重置后需同步更新脚本。禁用账号后，该用户不能继续登录或采集。

从旧单用户版本升级时，已有分组、批次和历史归属原管理员；旧全局采集令牌不再有效，需要改用个人令牌。

## 5. 图片与外部服务

### Temu 图片下载

进入“Temu 图片下载”，选择用户和分组，提交补齐任务。未选择分组时处理该用户全部 Temu 商品。

在任务列表查看进度和错误信息；失败任务可重试。后台处理服务需要能够访问商品图片地址。

### 相似度检索

进入“相似度检索”：

1. 填写兼容图片 Embedding 的服务地址、API Key、模型名和输出维度。
2. 服务地址可以是 Base URL，也可以是完整的 /embeddings 地址。
3. 按服务要求配置请求模板和响应路径；默认响应路径为 data.0.embedding。
4. 设置检索数量、pHash 距离、审核和自动合并阈值。
5. 保存当前模型，按需选择处理范围，查看任务与审核结果。

模型输出维度不能超过 1536。切换模型配置会建立新版本，存量图片需要后台重新处理。

### 1688 以图找图

进入“1688 以图找图”：

1. 填写图搜服务完整根地址和对应 API Key，包含实际使用的协议和端口。
2. 填写缓存图片公开域名，必须为 HTTPS，并能从公网访问 /images/ 下的图片。
3. 点击“检测服务”，确认服务健康且 1688 Cookie 可用。
4. 开启“图片就绪后自动图搜”，保存配置。
5. 根据外部服务容量设置并发数，范围 1～20，默认 4。
6. 对已有商品，选择用户和 Temu 分组，执行“重新执行以图找图”。

队列支持运行、暂停和停止。暂停后可继续当前进度；停止会尝试取消远程任务，继续时重新开始上传。并发配置保存后无需重启服务。

图搜成功后，商品详情最多展示前三个结果及官方图搜链接。失败任务可单条或按用户批量重试。图搜候选不会自动成为已确认货源。

## 6. 升级与备份

### 备份内容

升级前同时备份：

- PostgreSQL 数据。
- 图片数据卷 image_data。
- 当前环境配置，尤其是 SESSION_SECRET。

Compose 会为数据卷名称加项目名前缀，实际名称以部署环境为准。恢复时应保持数据库、图片与配置来自对应备份。

以下命令使用默认数据库名称和账号，在数据库容器内生成备份并复制到当前目录，避免终端重定向改变备份编码：

```text
docker compose exec -T db pg_dump -U product_manager -d product_manager -Fc -f /tmp/product_manager.dump
docker compose cp db:/tmp/product_manager.dump ./product_manager.dump
```

自定义了数据库名或账号时，替换相应参数。图片卷使用部署环境的卷备份工具或平台快照单独备份。

### 更新镜像

```text
docker compose pull
docker compose up -d
docker compose ps -a
docker compose logs --tail 100 migrate backend
```

更新会自动执行数据库迁移。不要删除数据卷；也不要使用带删除数据卷选项的停止命令进行普通升级。

从普通 PostgreSQL 16 切换到 pgvector PostgreSQL 16 时，保留已有数据库卷并先完成备份。

### 恢复数据库

先准备同名账号和一个**空的目标数据库**，暂停后端和后台处理服务的写入，再执行：

```text
docker compose cp ./product_manager.dump db:/tmp/product_manager.dump
docker compose exec -T db pg_restore -U product_manager -d product_manager --no-owner --exit-on-error /tmp/product_manager.dump
```

恢复对应图片卷和环境配置后，再启动其余服务。上述命令不负责清空已有数据库，不应直接用于已有业务数据的目标库。

## 7. 常见问题

| 问题 | 检查项 |
|---|---|
| 服务器本机能打开，其他电脑不能打开 | 默认只监听本机；调整监听地址或配置反向代理，并检查端口开放情况 |
| HTTP 下登录后仍显示未登录 | 未启用 HTTPS 时，COOKIE_SECURE 应为 false |
| 修改初始化管理员密码后不能用新密码登录 | 已有账号不通过初始化配置重置，使用用户管理重置密码 |
| 页面未启动 | 查看 migrate 和 backend 日志，确认数据库和迁移成功 |
| 新源码功能没有出现 | Compose 默认运行预构建镜像，需要发布对应镜像或自行构建 |
| 图片或图搜任务未执行 | 查看对应后台服务、管理员配置、队列状态和任务错误 |
| 图搜服务无法访问图片 | 检查公开 HTTPS 域名和 /images/ 路径，不使用仅本机可访问的地址 |
| 升级后脚本上传失败 | 更新脚本并使用个人采集令牌，不再使用旧全局令牌 |
