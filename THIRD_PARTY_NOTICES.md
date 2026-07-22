# 第三方软件与素材声明

项目自身代码使用根目录 `LICENSE` 中的保留所有权声明。该声明不覆盖下列第三方素材；对外分发前必须核对来源、版本和完整许可，并由发布负责人确认。

## Lucide Icons

- 位置：`static/icons/*.svg` 与由它们生成的 `static/icons.js`
- 版本：`lucide-static` 0.544.0（依据 SVG 文件头）
- 项目：<https://github.com/lucide-icons/lucide>
- 许可：ISC
- 随包许可原文：`licenses/Lucide-LICENSE.txt`（含部分 Feather 图标适用的 MIT 条款）

Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part
of Feather (MIT). All other copyright (c) 2022, Lucide Contributors.

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.

## Geist Mono

- 位置：`static/fonts/GeistMono-Variable.woff2`
- 项目：<https://github.com/vercel/geist-font>
- 许可：SIL Open Font License 1.1
- 版权：Copyright (c) 2023 Vercel, in collaboration with basement.studio
- 随包许可原文：`licenses/Geist-OFL-1.1.txt`

OFL 1.1 允许字体与软件一同捆绑和再分发，前提是每份副本包含版权声明与 OFL 许可文本，且不将字体文件单独出售。上游原文见：<https://github.com/vercel/geist-font/blob/main/LICENSE.txt>。

## 阿里巴巴普惠体 3.0

- 位置：`static/fonts/AlibabaPuHuiTi-55.otf` 和 `static/fonts/AlibabaPuHuiTi-85.otf`
- 版权方：阿里巴巴（中国）有限公司
- 官方下载入口：<https://www.alibabafonts.com/>
- 许可：阿里巴巴普惠体 3.0 专有法律声明（不是 MIT/OFL 等开源许可）

### 外部分发前必须核验

当前文件是常用字精简版 OTF，但项目内没有随文保存来源记录、官方原始文件校验值、完整法律声明，也没有足以证明“精简字体可随本软件对外分发”的授权记录。这是发布材料不完整的事实，本文件不对具体法律条款作扩张解释。

正式发布前必须选择其一：

1. 从官方渠道取得原始文件、完整法律声明，并由发布负责人核验捆绑分发范围；
2. 就当前精简文件获得可留档的授权确认；
3. 替换为许可文本明确、来源可追溯且适合捆绑分发的字体。

完成后，将官方许可原文随发行包一同保留，并更新本节。

## 项目图像与程序化纹理

- `static/assets/deck-*.jpg` 与 `metal-brush*.jpg` 由本项目的 `tools/gen_textures.py` 生成。
- `static/assets/logo.jpg` 在发布前需由发布负责人确认为自有作品或已获得允许再分发的授权，并将原始素材/授权凭证归档。

## 开发期工具

`tools/gen_textures.py` 使用 NumPy 和 Pillow。它们只用于重新生成已入库的纹理，不随总控台运行，也不是运行时依赖。各自许可见其上游发行包。
