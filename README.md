# 总控台

总控台是一个为 macOS 设计的本地服务监控与快速启动工具。它用 Python 3 标准库提供只绑定回环地址的后端，前端是无构建、无 CDN 的原生 HTML/CSS/JavaScript。

> 当前是个人/项目内部软件。根目录 `LICENSE` 不授予公开再分发权。准备对外发布时，作者需先选择正式代码许可证，并完成第三方素材核验。

## 功能

- 每 2 秒查看当前用户的本地监听服务、CPU、内存和运行时长。
- 保存常用服务或批处理任务，集中启动、停止、重启、查日志和诊断。
- 从项目文件夹识别常用启动命令，但不安装依赖、不执行项目代码。
- 通过运行 token、进程组和当前 UID 联合识别受控进程，不会因端口相同就杀死外部进程。
- Apollo 与 Candy 双 UI 主题，同时支持浅色、深色和跟随系统。

## 系统要求

- macOS 12 或更高版本。
- Python 3.12。运行时仅使用 Python 标准库。
- macOS 自带的 `ps`、`lsof`、`osascript` 等系统工具。
- Safari、Chrome 或其他支持 ES Modules 的现代浏览器。

`VERSION` 是项目版本的唯一权威来源。`Info.plist`、发行包名和发行说明应与它保持一致。

## 安装与启动

总控台当前以“完整项目目录”运行，`总控台.app` 是项目内启动器，不是可以单独复制的自包含应用。

1. 将整个项目目录放在一个你有读写权限的位置。
2. 确认 Python 版本：

   ```bash
   python3 --version
   ```

3. 双击 `总控台.app`。它会以后台应用方式运行，不显示 Terminal 和 Dock 图标。

需要看调试输出时，双击 `start.command`，或在终端运行：

```bash
python3 server.py
```

服务只绑定 `127.0.0.1`，从 9600 端口开始尝试；端口被占用时会递增，最多尝试 10 个端口。

## 使用要点

- 在“启动台”添加工作区，选择识别到的命令，或手动填写。
- `service` 用于长期运行的服务；`task` 用于有明确结束时间的批处理命令。
- 红色按钮会结束进程或删除应用，需要二次确认。
- 停止“总控台”本身不会自动停止已启动的独立服务。

## 数据、隐私与备份

运行数据与程序目录分离，默认放在 macOS 用户资料库：

| 路径 | 内容 | 备份建议 |
| --- | --- | --- |
| `~/Library/Application Support/总控台/config.json` | 应用命令、本地路径、端口、标记和运行识别信息 | 必须 |
| `~/Library/Application Support/总控台/config.json.bak` | 上一份已知良好的配置 | 必须 |
| `~/Library/Application Support/总控台/icons/` | 用户上传的图标和站点图标 | 按需 |
| `~/Library/Logs/总控台/` | 应用与总控台运行日志 | 通常不需 |

目录权限会收紧为 `0700`，配置、图标和日志文件为 `0600`。这些文件仍可能含个人路径、完整 shell 命令和日志内容；不应进入 Git，也不应随发行包或故障报告对外传播。

### 旧版数据首次迁移

如果新目标目录尚不存在，首次启动会将项目内旧 `data/config.json{,.bak}` 和 `data/icons/` 安全复制到 Application Support，将 `data/logs/` 复制到 Library Logs。迁移使用临时目录后原子落位，并且：

- 旧 `data/` 始终保留，不会自动删除。
- 目标已存在时绝不覆盖或合并，避免把更新的用户数据换回旧版。
- 符号链接和非普通文件不会被复制。
- 显式设置 `CONSOLE_DATA_DIR` 或 `CONSOLE_LOG_DIR` 时，对应目录不执行旧数据自动迁移。

需要自定义路径时：

```bash
CONSOLE_DATA_DIR="/private/path/console-data" \
CONSOLE_LOG_DIR="/private/path/console-logs" \
python3 server.py
```

自定义值必须是非空的绝对路径，并指向总控台专用的非符号链接子目录；不要直接填 `/`、用户主目录或项目根目录。

### 备份

1. 不再执行新的启动、停止或编辑操作。
2. 停止总控台。
3. 将 `~/Library/Application Support/总控台/` 复制到受保护的备份目录。
4. 记录当前 `VERSION`，以便恢复时匹配配置格式。

### 恢复

1. 确保总控台已停止，并另存当前 `~/Library/Application Support/总控台/`。
2. 将备份中的 `config.json` 和 `icons/` 复制回对应位置，权限分别设为 `0600` 和 `0700`。
3. 重新启动，逐项确认命令、工作目录和端口。

如果主配置损坏，程序会验证 `config.json.bak` 并恢复主文件。如果两份都不可用，服务进入只读保护状态，不会用空配置覆盖它们。`config.json.bak` 保留的是每次修改之前的上一份良好配置，而不是主文件的同内容副本。

## 升级

1. 阅读 `CHANGELOG.md`，确认是否有配置或平台变更。
2. 停止总控台并完整备份 `~/Library/Application Support/总控台/`。
3. 用新版本替换程序文件；用户数据保持在 Library 目录中。
4. 运行 `make check`。
5. 启动后检查应用数量、主题、关注关键字和一个可控服务的完整启停。

配置包含 `schemaVersion`，启动时逐版执行显式、幂等迁移。新程序不会静默降级它不认识的更高 schema；回退程序时仍应同时恢复与该版本匹配的数据备份。

## 卸载

1. 如果不希望已启动的服务继续运行，先在启动台逐个停止它们。
2. 停止总控台。
3. 按需导出 `~/Library/Application Support/总控台/` 备份。
4. 将整个项目目录移到废纸篓。
5. 确认不再需要数据后，手动删除 `~/Library/Application Support/总控台/` 和 `~/Library/Logs/总控台/`。

程序不会安装系统启动项，卸载时也不会自动删除用户数据。

## 安全边界

总控台不是多用户服务器或远程管理面板。它能以当前 macOS 用户的权限执行你保存的 shell 命令，因此：

- 只添加你已检查且信任的命令和工作目录。
- 不要将服务绑定到 `0.0.0.0`，不要通过反向代理、SSH 隧道或端口映射对外暴露。
- 不要在共享或不受信任的用户账户中运行。
- 不要把 Application Support 中的 `config.json`、Library Logs 日志或故障截图未经脱敏就上传。
- 本地回环绑定只是第一层边界，不能替代写接口的 Host/Origin/控制令牌防护。发布验收时必须执行 `RELEASE_CHECKLIST.md` 中的安全项。

## 故障排查

### 双击后没有界面

- 确认 `python3 --version` 可用且符合要求。
- 查看 `~/Library/Logs/总控台/console.log`。
- 用 `python3 server.py` 从终端启动，直接查看错误。
- 不要单独移动 `总控台.app`；它必须保持在项目根目录。

### 9600 打不开

程序可能已选择 9601–9609。查看终端输出或 `~/Library/Logs/总控台/console.log` 中的实际地址。服务可访问时，`GET /api/health` 会返回程序版本、配置 schema 和降级原因，且不会执行 `ps/lsof` 扫描。

### 应用启动失败

- 先打开该应用的日志和“启动诊断”。
- 确认工作目录仍然存在、命令可在普通 shell 中运行。
- 检查配置端口是否被外部进程占用，或被两张卡片重复配置。
- Finder 启动的应用不会读取你的 shell 配置；总控台会补入常用 Node/Homebrew 路径，但非标准安装仍可能需要显式绝对路径。

### 配置丢失或损坏

停止总控台，保留当前 `config.json`，然后按上文“恢复”流程使用已知良好的 `config.json.bak` 或离线备份。

## 开发

运行时无第三方 Python 依赖。只有重新生成纹理需要开发依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
```

主要目录：

```text
server.py                 Python 标准库后端
static/                   原生前端、主题、图标、字体和纹理
tests/test_server.py       后端单元测试
tools/gen_icons.py         由 vendored SVG 生成 icons.js
tools/gen_textures.py      重新生成纹理
tools/check_project.py     统一的只读项目检查
data/                      旧版运行数据（仅首次迁移源，不进 Git/发行包）
```

### 检查

提交前的权威命令是：

```bash
make check
```

它会检查 Python/JavaScript/Bash/plist/JSON 语法、版本一致性、主题和资源引用、生成的图标是否同步，并显式发现和运行测试。测试数量为 0 时会失败，不会出现“0 tests 也算通过”。

只运行后端测试：

```bash
make test
# 等价的显式命令：
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

正式发布前还应运行：

```bash
make release-check
```

它会额外检查 Git 状态和不应进入发行范围的文件；不会代替 `RELEASE_CHECKLIST.md` 中的人工验收。

### 重新生成资源

```bash
make generate-icons
make generate-textures
make check
```

`static/icons.js` 是生成文件，不应手工修改。重新生成图标或纹理后，只提交预期的差异。

## 发布

请按 `RELEASE_CHECKLIST.md` 逐项验收。一个可对外交付的版本至少需要：

- 清晰的代码许可选择与全部第三方素材授权凭证。
- 干净、可追溯的 Git commit 和带签名版本 Tag。
- 通过 `make release-check` 和人工 UI/安全/升级/回滚验收。
- 不含任何项目内旧 `data/`、用户 Library 数据、日志、绝对路径、token 或缓存的发行包。
- 针对目标 Mac 的签名、公证、完整性校验、全新安装和回退证据。

## 许可与第三方素材

项目当前保留所有权，详见 `LICENSE`。Lucide、Geist Mono、阿里巴巴普惠体等素材不受项目自有代码声明覆盖，详见 `THIRD_PARTY_NOTICES.md`。
