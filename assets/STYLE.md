# 图的样式约定（给画图的人）

所有图都是手写 SVG，不用 Mermaid，不用外部字体和图片，不用 foreignObject，不用 CSS media query。
每张图两个文件：`<name>-light.svg` 和 `<name>-dark.svg`，README 用 `<picture>` 按系统主题切换。

## 画布
- `viewBox="0 0 1200 H"`，`width="100%"`，H 按内容定，尽量不超过 700。
- 外边距 32。背景用一个铺满的 rect，不要透明。

## 字体
- `font-family="-apple-system, 'Segoe UI', 'PingFang SC', 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif"`
- 标题 20，正文 14，注释 12。不要小于 12。
- 估算宽度：中文每字约 1 em，英文和数字每字约 0.55 em。文字不能出框、不能重叠。

## 颜色
| 用途 | light | dark |
|---|---|---|
| 背景 | #ffffff | #0d1117 |
| 正文 | #1f2328 | #e6edf3 |
| 次要文字 | #656d76 | #8b949e |
| 线和边框 | #d0d7de | #30363d |
| 淡填充 | #f6f8fa | #161b22 |
| 蓝（主） | #0969da | #58a6ff |
| 绿 | #1a7f37 | #3fb950 |
| 橙 | #bc4c00 | #d29922 |
| 紫 | #8250df | #a371f7 |
| 蓝淡填充 | #ddf4ff | #122d4d |
| 绿淡填充 | #dafbe1 | #12361f |
| 橙淡填充 | #fff1e5 | #3d2306 |
| 紫淡填充 | #fbefff | #2d1b4e |

一张图最多用三个强调色，其余用灰阶。

## 形状
- 框 `rx="6"`，描边 1.5。
- 箭头用 `<marker>` 定义一次，线宽 1.5。
- 不加阴影、渐变、动画。

## 校验
用 Chrome 无头渲染后肉眼看：
```
google-chrome --headless=new --disable-gpu --hide-scrollbars --screenshot=/tmp/x.png --window-size=1200,<H> file:///绝对路径/x-light.svg
```
看 PNG 确认没有文字出框、重叠、箭头穿字。两个主题都要看。
