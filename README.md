# MovieClaw
全新一代，智能化的私人影音管理产品。

## 快速开始

三种方式，**按你的情况选一种**即可：

| 你是…… | 选哪种 | 需要会什么 |
| --- | --- | --- |
| 普通用户，想直接用起来（NAS / 家用服务器 / 云主机） | 方式一：官方镜像 | 会装 Docker 就行 |
| 想自己从源码打包镜像部署 | 方式二：源码构建镜像 | 基本的命令行操作 |
| 开发者，要改代码、调试 | 方式三：本地开发 | Python / Node 开发环境 |

### 方式一：官方镜像（推荐，最简单）

不用下载源码、不用编译。官方镜像
[movieclaw/movieclaw](https://hub.docker.com/r/movieclaw/movieclaw)
单容器跑全部（前端 + 后端 + NER 模型 + 内置 TMDB Key），支持 x86_64 与 ARM64。
镜像也内置 ffmpeg、Subtitle Edit seconv、Tesseract 和 11 种常用语言包，PGS
图片字幕转 SRT 无需再进入容器安装依赖。
唯一前提：机器上装好 Docker（群晖用自带的 Container Manager，其他 NAS 用
各自的 Docker 套件即可）。

#### 一条命令直接跑

会用命令行的话，这是最快的方式——改好两个路径，整段复制执行即可：

```bash
docker run -d \
  --name movieclaw \
  --init \
  -p 3000:3000 \
  --restart unless-stopped \
  -e TZ=Asia/Shanghai \
  -v "$(pwd)/data:/app/data" \
  -v /volume1/media:/media \
  -v /volume1/downloads:/downloads \
  movieclaw/movieclaw:latest
```

- 把 `/volume1/media`、`/volume1/downloads` 换成你机器上的真实目录。
  冒号**左边**是你机器上的目录，**右边**是 movieclaw 在容器里看到的路径——
  之后在网页设置里填路径时，填的都是右边这个
- **有多个媒体盘或多个下载目录？完全支持**：每个目录加一行 `-v` 即可，比如
  再加 `-v /volume2/movies:/movies \`、`-v /volume2/downloads2:/downloads2 \`，
  数量不限
- 跑起来后浏览器打开 `http://<主机IP>:3000`，按引导创建管理员账号
- **3000 被别的服务占了？** 改 `-p` 冒号左边即可，比如 `-p 8096:3000`，
  之后用 `http://<主机IP>:8096` 访问，容器里的端口不用动。
  只有用 `--network host` 的时候容器内端口就是宿主端口，这时才需要真正换
  监听端口：加 `-e MOVIECLAW_WEB_PORT=8096`，或装好后在
  「设置 → 应用设置 → 对外端口」里改（保存后应用自动重启生效）

#### 用 docker compose（推荐长期使用）

配置写在文件里，日后调整挂载、迁移机器都更省心，NAS 图形界面也走这条路。

**第 1 步**：新建一个文件夹（比如叫 `movieclaw`），在里面创建一个名为
`docker-compose.yml` 的文本文件，粘贴以下内容：

```yaml
services:
  movieclaw:
    image: movieclaw/movieclaw:latest
    container_name: movieclaw
    init: true
    ports:
      # 3000 被占了就改冒号左边，比如 "8096:3000"，右边不用动
      - "3000:3000"
    volumes:
      - ./data:/app/data              # 运行数据，备份这个文件夹就够了
      - /volume1/media:/media         # ← 改成你的媒体目录
      - /volume1/downloads:/downloads # ← 改成你下载器的保存目录
      # 有多个媒体盘/下载目录？每个目录加一行即可，数量不限，例如：
      # - /volume2/movies:/movies
      # - /volume2/downloads2:/downloads2
    environment:
      - TZ=Asia/Shanghai
    restart: unless-stopped
```

**第 2 步**：把 `volumes` 里的路径改成你机器上的真实路径。规则很简单：
冒号**左边**是你机器上的目录，冒号**右边**是 movieclaw 在容器里看到的路径——
之后在网页设置里填路径时，填的都是右边这个。下载器的保存目录一定要挂进来，
movieclaw 才能整理下载完成的文件。

**第 3 步**：在这个文件夹下启动（NAS 图形界面用户：在 Container Manager
选「项目 → 新增」，指向这个文件夹即可）：

```bash
docker compose up -d
```

**第 4 步**：浏览器打开 `http://<主机IP>:3000`，按引导创建管理员账号，
然后照下文「上手四步」完成配置。

### 方式二：源码构建镜像

适合想自己出镜像、或改了代码想打包部署的用户。需要先在
[themoviedb.org](https://www.themoviedb.org/settings/api) 免费申请一个
TMDB API Key（官方镜像已内置，自建才需要）。

```bash
# 1. 下载源码
git clone https://github.com/yipengfei329/movieclaw.git
cd movieclaw

# 2. 构建镜像（TMDB Key 会烧进镜像，运行时可用环境变量覆盖）
TMDB_API_KEY=你的key ./scripts/build-image.sh
#   国内网络加速： CN_MIRROR=1 TMDB_API_KEY=... ./scripts/build-image.sh
#   给 NAS 交叉构建：PLATFORM=linux/amd64 TMDB_API_KEY=... ./scripts/build-image.sh
#   构建完成会自动生成并从 MKV 抽取测试 PGS，再 OCR 回 SRT；失败即阻断

# 3. 把仓库根目录 docker-compose.yml 的 image 一行改成本地镜像名
#    movieclaw:latest，按注释改好媒体目录挂载，然后启动
docker compose up -d
```

挂载路径的含义与方式一相同，更多可选项（覆盖 TMDB Key、更新加速镜像、
Emby/Jellyfin 通知等）见 [docker-compose.yml](docker-compose.yml) 内的注释。
字幕镜像的架构、依赖与发布门禁见
[Docker 字幕运行时契约](docs/design/docker-subtitle-runtime.md)。

### 日常升级：应用内更新，无需重拉镜像

以上两种 Docker 部署，装完后的日常升级都**不需要**重新拉取或构建镜像：
在「设置 → 关于与更新」里一键检查并更新到最新版（前后端代码与 NER 模型），
下载的是 GitHub Release 上几 MB 的产物包（可配加速镜像），更新落在 data 卷上、
容器重建也不丢。只有当更新说明里明确提示「包含依赖变化，需升级 Docker 镜像」时，
才需要 `docker compose pull && docker compose up -d`（自建镜像则重新构建）——
这种情况很少发生。更新出问题可在同一页面一键回退，坏更新会被容器自动回落到
可用版本，数据不受影响。
（机制详见 [docs/design/in-app-update.md](docs/design/in-app-update.md)）

### 方式三：本地开发

```bash
./scripts/dev.sh          # 同时启动后端和前端
./scripts/dev.sh api      # 只启动后端
./scripts/dev.sh web      # 只启动前端
```

脚本会自动完成首次环境准备（创建虚拟环境、安装依赖、生成 `.env`、`pnpm install`），
日志带 `[api]` / `[web]` 彩色前缀区分来源，`Ctrl-C` 一键停止全部服务。

手动安装：

```bash
# 后端（Python 3.11+）
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn movieclaw_api.main:app --factory --reload

# 前端（Node.js 20+）
pnpm install && pnpm web:dev
```

- Web 控制台：`http://127.0.0.1:3000`
- API 文档（Swagger UI）：`http://127.0.0.1:8000/docs`

源码方式运行时，**种子名结构化抽取依赖的 NER 模型需手动放置**（Docker 镜像已内置）：
从 [Releases](https://github.com/yipengfei329/movieclaw/releases) 下载 `model.int8.onnx`、
`tokenizer.json`、`labels.json` 放进 `data/models/torrent-ner/`（可用 `MOVIECLAW_NER_DIR` 改路径）后重启。
不放模型服务照常启动，仅该功能不可用，日志中有明确提示。

## 上手四步

1. **建管理员账号** —— 首次访问自动进入初始化页
2. **接站点** —— 「设置 → 资源站点」填 Cookie / API Key，或装浏览器扩展自动同步
3. **接下载器** —— 「设置 → 下载器」接入 qBittorrent / Transmission；下载器与 movieclaw 看到的路径不一致时，在这里配好路径映射
4. **建媒体库** —— 「媒体库」新建库并指定根路径，建好即开始扫描；已有存量文件会被识别刮削，认不准的进「待识别」等你确认

可选：需要 AI 助手时在「设置 → AI 模型」填供应商密钥；想让任意来源的下载也自动入库，在「设置 → 监听导入」加一条「源目录 → 目标库」规则。

## 忘记管理员密码了怎么办

在跑着 movieclaw 的机器上执行一条命令即可重置，**不会动任何配置与数据**
（站点、下载器、媒体库、订阅全部原样保留，只换掉密码）：

```bash
# Docker 部署（容器名按你 compose 里的实际值改）
docker exec -it movieclaw python -m movieclaw_api.reset_password

# 源码部署：先 cd 到项目根目录（data/ 的上一级）
python -m movieclaw_api.reset_password
```

按提示输入两次新密码就好；连用户名也忘了，就加 `--show` 先看一眼：

```bash
docker exec -it movieclaw python -m movieclaw_api.reset_password --show
```

重置后立刻就能用新密码登录，不必重启服务；想让别处已登录的设备一并下线，
再 `docker restart movieclaw` 一次即可。

> 为什么是命令行、而不是网页上点「忘记密码」？自托管软件没有可信的第三方来
> 证明"你是账号主人"——没有强制绑定的邮箱/手机，真做邮件找回就得要求每位
> 部署者先配好 SMTP。所以这里把身份证明换成一件更硬的事：**能访问这台机器的
> `data/` 目录，就是主人**。这跟加密密钥文件的边界是同一条，Jellyfin、
> Vaultwarden、Gitea 也都这么做。

家人朋友的**成员**账号忘了密码不用这条命令：管理员在「设置 → 成员管理」里
点一下重置就行。

##产品技术文档

各模块的重大设计与取舍都记录在 [docs/design/](docs/design/) 目录，
一事一档（媒体库、元数据、订阅、应用内更新、CLI……），按文件名找感兴趣的主题即可。

## License

[MIT](LICENSE)
