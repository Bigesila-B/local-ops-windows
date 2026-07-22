# 总控台 (Console)

本地服务监控与快速启动控制台。**零依赖**：Python 3 标准库后端（单文件）+ 无构建原生前端。推荐双击 `总控台.app` 后台运行（不显示 Terminal/Dock）；`start.command` 保留为终端调试入口。

## 结构

- `server.py` — 后端（单文件，仅标准库，Python 3.12）
- `static/index.html` / `static/app.js`（入口）/ `static/js/{core,launchpad,services,overlays}.js`（原生 ES Modules，无构建）/ `static/icons.js` — 前端（原生，禁框架/CDN/构建）；`core.js` 承载工具/API/浮层/状态/主题注册，`launchpad.js` 卡片+拖拽+诊断，`services.js` 表格，`overlays.js` 模态+抽屉；模块间用 `window.__poll` 共享轮询入口
- `static/themes/` — **主题注册表**：`{id}.css` 整包样式 + `{id}.json` 清单（`id/name/author/desc/colors[]`）。`GET /api/state` 返回 `themes`（全部清单）与 `uiTheme`（当前主题，存用户 Application Support 的 `config.json`）；`POST /api/ui/theme {theme}` 校验 id 后落盘。顶栏按钮/⌘K 打开主题选择面板（色点 + 名称 + 作者 + 描述），换主题 = `#themeCss` 换 href；新主题 = 往目录丢 css+json 即自动注册；`theme-template.css` 为作者模板（令牌契约 + 组件清单 + 铆钉/孔板/折边工艺片段，不参与注册）。内置双主题：apollo（任务控制）与 candy（彩色块，含黑色 hero 卡 `.app-hero`，其他主题须 `display:none`）
- `static/fonts/AlibabaPuHuiTi-{55,85}.otf` — 阿里巴巴普惠体 Regular/Bold（v3，TTF-Min 常用字精简版；当前主题标题/正文字体）；`GeistMono-Variable.woff2`（vendored，数据/代码字体）；`static/icons/*.svg` — Lucide 图标源文件（vendored）；`tools/gen_icons.py` — 由 svg 重新生成 `icons.js`（勿手改 icons.js）
- `static/assets/` — 纹理与品牌素材：`logo.jpg`（项目 logo，顶栏品牌图标）；`deck-dark.jpg` / `deck-light.jpg`（程序化生成的严格周期冲压孔板，无缝平铺，仅 Apollo 主题舱壁使用）、`metal-brush.jpg`（程序化频域各向异性噪声拉丝，中灰基底天然周期、平铺无断层，soft-light 下只出纹理不改明度）、`metal-brush-dark.jpg`（深色面板用预调暗版）；纹理均由 `tools/gen_textures.py` 重新生成（旧 AI 镜像版备份在 `tmp/`）
- `~/Library/Application Support/总控台/config.json` — 用户配置；`icons/` 为应用图标。目录/ 文件权限分别为 0700/0600
- `~/Library/Logs/总控台/{appId}.log` — 应用启动日志；`console.log` 为 `.app` 启动日志
- `data/` — 旧版项目内数据，仅在新目标不存在的首次启动中复制迁移；保留不删除
- `start.command` — macOS 双击启动脚本（chmod +x）
- `总控台.app` — macOS 无终端窗口启动器（`LSUIElement` 后台应用；内部直接启动 `server.py`，输出写入 `~/Library/Logs/总控台/console.log`）

## 运行

`python3 server.py` → 绑定 `127.0.0.1`，端口从 **9600** 起尝试，被占则 +1（最多 10 个）。启动后自动打开浏览器。`/favicon.ico` 返回 204。双击 `总控台.app` 会先识别同目录的现有总控台，可直接打开或安全重启，不需要用户输入命令，也不会出现 Terminal 窗口。

## API 契约（全部 JSON；icon 上传为原始字节）

### `GET /api/state` — 前端唯一轮询接口
```json
{
  "services": [{
    "key": "python3.12:8791",
    "pid": 54252, "name": "python3.12", "port": 8791,
    "cwd": "/Users/laogou/xx项目", "project": "xx项目", "cmd": "python3 app.py",
    "cpu": 0.3, "mem": 1.2, "uptimeSec": 7980,
    "group": "mine", "pinned": false, "hidden": false, "promoted": false,
    "appId": null, "appName": null
  }],
  "watched": [{"pid": 1, "name": "ffmpeg", "cmd": "...", "cpu": 0.0, "mem": 0.5, "uptimeSec": 60, "keyword": "ffmpeg"}],
  "apps": [{
    "id": "a1b2c3d4", "name": "我的博客", "command": "python3 -m http.server 8080",
    "cwd": "/path", "port": 8080, "emoji": "🚀", "glyph": "rocket", "icon": "/icons/a1b2c3d4.png",
    "kind": "service",
    "running": true, "pid": 1234, "uptimeSec": 120,
    "listening": true, "portOccupied": false, "portOccupiedPid": null,
    "portConflict": false, "portConflictApps": [],
    "lastExit": {"code": 0, "at": 1700000000, "startedAt": 1699999998750, "durationSec": 1.25},
    "favicon": "/icons/fav-a1b2c3d4.png",
    "ports": [8080],
    "listening": true, "portOccupied": false, "portOccupiedPid": null,
    "portOwner": null, "portConflict": false, "portConflictApps": []
  }],
  "watchedKeywords": ["ffmpeg"],
  "consolePort": 9600, "consolePid": 123, "consoleCwd": "/path/to/总控台",
  "version": "1.0.0", "schemaVersion": 1,
  "degraded": false, "degradedReasons": []
}
```
- `GET /api/health` — 不运行 `ps/lsof` 的轻量健康检查，返回 `status/version/schemaVersion/degraded/issues/config`
- `group`: `"mine"` | `"background"`；`icon`/`emoji`/`port`/`cwd`/`project`/`appId`/`appName`/`lastExit` 可为 `null`
- `lastExit`：最近一次自然退出的 `{code, at, startedAt, durationSec}`（旧数据可能只有 `code/at`）；手动 stop 不记录。批处理启动时保留上一次完成历史，自然退出后覆盖；卡片显示成功/失败、距今时间与耗时
- `kind`：`"service"`（长期服务，有端口语义）| `"task"`（批处理任务，强制 port=null，主按钮为「运行」）；旧数据缺省视为 `service`。启动台按 kind 分两个区渲染
- `running`：仅表示存在通过本次启动 token、进程组与当前用户三重校验的受控进程；不再以“配置端口有任意监听者”作为运行依据
- `listening`：受控进程是否正在监听配置端口；`portOccupied`：该端口被非受控进程占用；`portConflict`：多张应用卡配置了同一端口；`legacyManaged`：是否通过旧版 PID+端口+UID+cwd 兼容身份识别
- `project`：cwd 最后一段目录名（用于区分同名进程）；`appId`/`appName`：该端口命中启动台应用时的关联信息
- 排除控制台自身进程；只返回当前用户的进程

### 服务操作
- `POST /api/kill` `{pid, force?}` → `{ok}` / `{ok:false, error}`（force 用 SIGKILL；校验属当前用户）
- `POST /api/services/flag` `{key, flag: "hidden"|"pinned"|"promoted", value: bool}` → `{ok}`（promoted=false 即「移回后台」，前端对 `svc.promoted` 的行显示该按钮）
- `POST /api/watch` `{keyword, action: "add"|"remove"}` → `{ok, keywords}`

### 启动台应用
- `POST /api/apps` `{name, command, cwd?, port?, emoji?, glyph?, kind?}` → app 对象（`kind` 缺省 `service`；`task` 强制 port=null）
- `POST /api/pick` `{what: "dir"|"script"}` → `{ok, path}` / `{ok, canceled:true}`（osascript 弹 macOS 原生目录/文件选择框；取消不是错误）
- `POST /api/project/detect` `{cwd}` → `{ok, cwd, name, files, candidates:[{command,label,source,port,kind,detail}]}`（只读分析项目根目录，不执行项目代码；识别 package.json scripts 与包管理器锁文件、Hexo/Hugo/Jekyll、Django/FastAPI/Flask/Streamlit、Docker Compose、Go、Rust、常用启动脚本及纯静态站点。Hexo 无 scripts 时仍返回 `hexo s` 服务与 `hexo cl` 任务）
- `POST /api/apps/reorder` `{ids: [...]}` → `{ok}`（按 ids 重排 apps 数组；Python sort 稳定，未涉及的 id 相对顺序不变，服务/任务两区可独立拖拽排序互不干扰）
- `PUT /api/apps/{id}`（部分更新同字段，可带 `stopBeforeUpdate:true`）→ app 对象；运行中修改 command/cwd/port/kind 时，缺少该标记返回 `{ok:false, requiresStop:true}`，带标记则安全停止后原子保存
- `DELETE /api/apps/{id}` → `{ok}`（先停止再删，连同图标/日志）
- `POST /api/apps/{id}/start` → `{ok, pid}` / `{ok:false, error}`（已运行则报错；批处理启动后立即返回，由退出监视线程记录结果，快速成功任务不会被误判成启动失败）
- `POST /api/apps/{id}/stop` → `{ok}` / `{ok:false, error}`
- `POST /api/apps/{id}/restart` → `{ok, pid}` / `{ok:false, error}`（仅重启 token 校验通过的受管进程；等待旧进程退出后再启动，不自动 SIGKILL）
- `POST /api/apps/{id}/diagnose` → `{ok, issues:[{kind,title,detail,fix}], summary}`（本地规则诊断，不调外部 AI：覆盖依赖未装/模块缺失、运行时或脚本不存在、npm 脚本名错误并列出可用脚本、端口占用/配置重复、权限不足、pip 包缺失，以及退出码 126/127/0/负值的兜底判读；前端在启动失败卡片显示「启动诊断」按钮）
- `POST /api/apps/{id}/icon`（body 为 png/jpg/webp 原始字节）→ `{ok, icon}`
- `POST /api/apps/{id}/favicon` → `{ok, favicon}` / `{ok:false, error}`（按有效端口抓站点图标：解析首页 `<link rel*icon*>`，兜底 `/favicon.ico`，支持 png/jpg/webp/ico/svg，存入 Application Support 的 `icons/fav-{id}.{ext}` 并写入 `app.favicon`；图标优先级：上传 icon > glyph > favicon > 名称首字，前端在无 icon/glyph 且运行中时自动触发一次）
- `DELETE /api/apps/{id}/icon` → `{ok}`
- `GET /api/apps/{id}/logs?tail=300` → `{text}`

### 总控台自身
- `POST /api/console/restart` → `{ok, pid, helperPid, port}`（先返回响应，再由独立 helper 等待旧进程退出并优先复用原端口；启动台应用不随总控台停止）
- `POST /api/console/stop` → `{ok, pid, port}`（响应发出后关闭总控台 HTTP 服务；启动台中已经运行的独立进程组保持运行）
- `POST /api/ui/theme` `{theme}` → `{ok, theme}` / `{ok:false, error}`（校验主题 id 存在后写入 `config.json` 的 `uiTheme`；主题清单由 `/api/state` 的 `themes` 字段返回）

### 静态
`GET /` → `static/index.html`；`/app.js`、`/js/*`、`/themes/*`、`/assets/*`、`/fonts/*` 等映射 `static/`；`/icons/xxx` → Application Support 的 `icons/xxx`。防路径穿越。

## 后端实现要点

- **端口扫描**：`lsof -iTCP -sTCP:LISTEN -P -n`，按 `(pid, port)` 去重（IPv4/6 重复行）。lsof 的 COMMAND 列会截断，名称以 ps 的 comm 为准。
- **进程详情**：批量 `ps -o pid=,user=,comm=,args=,%cpu=,%mem=,etime= -p <逗号分隔pid>`；只保留 `user == 当前用户`。
- **cwd**：`lsof -a -p <逗号分隔pid> -d cwd -Fn`，解析 `n` 行。
- **etime 解析**：`[[dd-]hh:]mm:ss` → 秒。
- **分组逻辑**（按优先级）：用户 `promoted` → `mine`；进程名含开发关键词（python node ollama docker 等，见 `DEV_KEYWORDS`，只匹配 name 不匹配 args，避免 VS Code `--ms-enable-electron-run-as-node` 这类误伤）→ `mine`（覆盖下方规则，Ollama/Docker 这类在 .app 内的守护进程仍算服务）；可执行路径含 `.app/Contents/`（GUI 应用及其 helper）→ `background`；comm 以系统路径开头（`/usr/libexec/`、`/usr/sbin/`、`/sbin/`、`/System/`、`/usr/lib/`）→ `background`；comm 或 cwd 含 `/Library/Containers/`（沙盒应用）→ `background`；其余默认 `mine`。`hidden` 仅是标记，照常返回。
- **关注进程**：`ps -axo pid=,uid=,comm=,args=,etime=,%cpu=,%mem=`，args 小写包含关键字即命中，只保留当前用户并排除自身及 ps/lsof。
- **应用状态**：每次启动生成随机 `runToken`，常驻外层 shell 在 argv 中持有标记并等待内层命令及其后台作业。新版进程只有同时命中 `lastPgid` / 当前 UID / token 的进程组才算 running；升级前缺少 token 的旧进程，只有配置 `lastPid`、监听端口、当前 UID 与真实 cwd 全部一致时才兼容认领，任一条件不符仍按外部端口占用处理。`ports` 来自受控进程组成员实际监听的端口。
- **应用启停**：启动前拒绝重复配置端口和已被占用端口；停止时先校验 token，然后只对该受控进程组发 `SIGTERM`，**绝不按端口杀其他监听者**。启动后 daemon 线程记录自然退出的 `lastExit={code,at,startedAt,durationSec}`，手动 stop 不记录；批处理不做“长期服务存活探测”，避免把快速成功误判成失败。
- **运行中编辑**：编辑面板打开时立即显示“停止服务”。点击只调用 stop，面板保持打开且当前草稿不变；停止成功后用户继续编辑并普通保存。名称/图标仍可在运行中直接保存。`stopBeforeUpdate:true` 保留为 API 客户端的原子停止更新能力，但不是默认前端流程。
- **无终端 PATH**：Finder/`LSUIElement` 启动不会读取 shell 配置；子应用启动环境需显式补入 `~/.local/bin`、Volta/Bun/pnpm、NVM/fnm、Homebrew 与系统 bin 目录，保证 `node`/`npm`/`pnpm` 等可用。启动 API 短暂探测立即退出，并把日志末行作为明确错误返回。
- **日志**：单文件超过 10MB 时 copy-truncate，保留 3 份轮转备份；日志 API 从文件尾部分块读取，不将整个日志读入内存。
- **keep-alive 陷阱**：POST start/stop 前端会带 `{}` body，handler 必须 `discard_body()` 读掉——否则残留字节污染同一 keep-alive 连接的下一个请求（method 解析成 `{}GET` → 501，前端显示断连横幅）。新增不读 body 的 POST 路由时同样处理。
- **运行目录**：默认配置/图标位于 `~/Library/Application Support/总控台`，日志位于 `~/Library/Logs/总控台`；`CONSOLE_DATA_DIR` / `CONSOLE_LOG_DIR` 可显式覆盖，覆盖时对应目录不自动迁移旧 `data/`。
- **配置**：读写加线程锁；写入用临时文件 + `os.replace` 防损坏；`schemaVersion` 逐版显式迁移；`.bak` 保留上一份良好版本。主配置与备份均不可读时进入只读保护，不覆盖原文件。
- **项目识别**：仅读取项目根目录下不超过 2MB 的已知配置/入口文件，不安装依赖、不执行配置、不扫描整个目录；显式 CLI 端口优先于框架默认端口。
- **kill 安全**：只允许结束当前用户的进程。

## 配置 schema
```json
{
  "schemaVersion": 1,
  "apps": [{"id": "8位hex", "name": "", "command": "", "cwd": null, "port": null, "emoji": null, "icon": null, "favicon": null, "kind": "service", "lastPid": null, "lastPgid": null, "runToken": null, "lastExit": null, "createdAt": 0}],
  "hidden": ["name:port"], "pinned": ["name:port"], "promoted": ["name:port"],
  "watchedKeywords": [],
  "uiTheme": "apollo"
}
```

## 前端要求

- 中文 UI，单页两视图（侧边导航：启动台 / 服务监控），每 2s 轮询 `/api/state`
- 添加服务时选择工作区文件夹后自动调用项目识别并展示候选命令；用户点选候选后再填入命令/端口。原有“选择脚本”与手动填写入口必须保留
- 编辑运行中服务时，表单内立即显示“停止服务”；停止操作不得关闭编辑面板或清除已经填写的内容，停止后恢复普通“保存”
- 批处理运行中显示实时耗时和停止入口；自然结束后持续显示成功/失败、距今时间与耗时，并弹出完成提示。失败时突出日志入口；首次加载已有历史不重复提醒
- DOM 按 key 原地更新，禁整列表重绘闪烁；fetch 失败显示断连横幅
- 深浅色跟随系统 + 手动切换（localStorage `console-theme`）；**双 UI 主题**（localStorage `console-ui-theme`，`#themeCss` 换 href 整包切换）：**Apollo 任务控制**（apollo.css：金属面板 + 四角螺丝 + 下沉荧光屏 + LED + 立体按键，浅深色舱壁孔板纹理）与 **Candy 彩色块**（candy.css：奶油底/墨底 + 超大标题（描边英文随行）+ 彩色概览格 + 黑描边白卡 + 黑药丸按钮 + 青柠点缀）；字体 = 阿里巴巴普惠体（vendored Regular/Bold）+ Geist Mono（数据/代码）；顶栏品牌图标 = `static/assets/logo.jpg`；UI 零 emoji
- 动效：卡片入场 stagger（`--d`）、hover 浮起、LED steps() 闪烁、模态/抽屉缓动、按键下压回弹、`prefers-reduced-motion` 降级
- 危险操作（结束进程/删除应用）必须确认
