# Candy 本地指挥中心卡片 — Design QA

## Evidence

- Source visual truth:
  - `/var/folders/j4/dl_3n4jj0m9g46853fxs65mh0000gn/T/codex-clipboard-1069de37-e66d-4704-8f1d-c980ab0717a1.png` — 原 Candy hero 卡，716 × 796 px；卡片有效区域约 642 × 731 px。
  - `/var/folders/j4/dl_3n4jj0m9g46853fxs65mh0000gn/T/codex-clipboard-0f7e0a3a-8470-406d-9906-85016132c939.png` — 用户提供的机器人，1536 × 1024 px、RGBA。
- Browser-rendered implementation:
  - `tmp/design-qa/candy-local-ops-660-light.png` — 660 × 800 CSS viewport，浏览器内容截图 650 × 788 px。
  - `tmp/design-qa/candy-local-ops-360-light.png` — 360 × 1100 CSS viewport，浏览器内容截图 350 × 1069 px。
- Combined focused comparison:
  - `tmp/design-qa/candy-card-comparison.png` — 原卡片、原始机器人素材与手机实现卡片并排比较，1920 × 800 px。
- Density normalization: browser capture and source images were compared at device scale factor 1. The original-card crop and implementation-card crop were proportionally scaled to the same comparison-panel height; no judgment was based on browser chrome or density-only differences.
- State: 启动台视图、Candy 浅色主题、服务列表有内容、hero 可见。Candy 暗色与 Apollo 隐藏状态另行回归。

## Findings

- No actionable P0, P1, or P2 findings remain.
- Fonts and typography: 中文标题继续使用项目内 AlibabaPuHuiTi Bold，英文与脚注沿用既有层级和字距；“本地指挥中心 / LOCAL OPS”在 360、520、660、900、1200 px 均未异常换行或截断。
- Spacing and layout rhythm: 原卡片的圆角、内边距、按钮和底部微文案节奏得到保留；机器人填补了原先的大面积空白。521–660 px 使用左文右图，520 px 以下改为上下布局。
- Colors and tokens: 黑底、青柠强调色和米白标题保持 Candy 视觉语言；暗色模式下标题固定为可读的米白色。
- Image quality and asset fidelity: 使用用户提供的原图，不以 CSS、SVG 或占位图替代；透明边缘已清理并编码为 1000 × 722 WebP，浏览器返回 200、`image/webp`，文件 135872 bytes。
- Copy and content: 移除了与页面主标题重复的“启动台 / LAUNCHPAD”，替换为功能定位更清楚的“本地指挥中心 / LOCAL OPS”；“命令面板 ⌘K”保留并可正常打开命令面板。
- Accessibility and behavior: 装饰图使用空 `alt`、`aria-hidden="true"`、不可拖拽并延迟加载；按钮键盘语义未改变。命令面板可由卡片按钮打开并用 Escape 关闭。

## Responsive And Regression Evidence

- 1200 × 800: 三列服务网格，hero 为 363 × 365 px，无横向溢出。
- 900 × 800: 两列服务网格，hero 为 420 × 365 px，顶栏断点正常。
- 660 × 800: 服务网格单列，hero 内部左文右图，视觉密度均衡。
- 520 × 800 and 360 × 1100: hero 上下布局，卡片和机器人完整落在内容宽度内。
- Candy dark: 标题、按钮、机器人和边框均清晰可读。
- Apollo: `.app-hero` 仍完全隐藏，没有影响原主题布局。
- Primary interactions tested: 主题切换、明暗切换、hero 命令面板按钮、Escape 关闭命令面板。
- Browser console errors checked: none (`dev.logs()` returned `[]`).

## Comparison History

1. P2 — 360 px 下网格轨道仍按内容最小宽度扩到约 615 px，产生约 273 px 横向溢出。
   - Fix: 单列断点改用 `minmax(0, 1fr)`。
   - Post-fix evidence: 360 × 1100 截图中 hero 和应用卡均落在 350 px 浏览器内容宽度内，没有可见横向溢出。
2. P2 — 521–660 px 的 hero 已声明分栏轨道，但容器仍是 flex，机器人没有进入右侧视觉区。
   - Fix: 该断点显式切换为 `display: grid`，并固定标题、按钮、图片、脚注的网格区域。
   - Post-fix evidence: 660 × 800 截图显示文案位于左侧、机器人完整占据右侧，脚注回到左下，卡片高度降至紧凑的约 284 px。

## Implementation Checklist

- [x] 去除重复的启动台文案。
- [x] 使用用户提供的机器人素材并优化体积。
- [x] 保留命令面板交互。
- [x] 覆盖浅色、暗色和 Apollo 回归。
- [x] 覆盖桌面、中间断点和手机宽度。
- [x] 检查静态资源响应、JavaScript 语法、项目检查和测试。

final result: passed
