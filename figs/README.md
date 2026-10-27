# 示意图

`docs/07-实现原理与数据生成.md` 中的 7 张图由 `gen_figs.py` 生成：

| 文件 | 内容 |
|---|---|
| `fig-01-scene.{svg,png}` | 模拟场景：路网、RSU/gNB、覆盖、静态目标、车辆 |
| `fig-02-normal-flow.{svg,png}` | 正常态数据流与"制动回写"闭环 |
| `fig-03a-attack-a1.{svg,png}` | A1 感知篡改 → 通信拥塞 |
| `fig-03b-attack-a2.{svg,png}` | A2 物理欺骗 + 控制误导 |
| `fig-03c-attack-a3.{svg,png}` | A3 跨层隐蔽（回波渐偏） |
| `fig-03d-attack-a4.{svg,png}` | A4 双因素仿冒 |
| `fig-04-crosslayer.{svg,png}` | 攻击 × 观测域矩阵 + 六组跨源比较 |

## 重新生成

```bash
# SVG + PNG（PNG 需要 playwright chromium，见下）
.venv/bin/python figs/gen_figs.py

# 只生成 SVG
.venv/bin/python figs/gen_figs.py --svg-only
```

PNG 渲染需要中文字体。脚本会自动下载 `NotoSansSC.otf` 到 `figs/.cache/`
（约 16 MB，已加入 `.gitignore`，不进版本库），并用本机已安装的 Chromium 渲染 2× 图。

首次使用：

```bash
.venv/bin/pip install playwright
.venv/bin/playwright install chromium
```

## 约定

- 画布宽 1240 px；PNG 以 2× 渲染，便于文档缩放。
- 颜色：绿=感知、青=雷达、蓝=V2X、紫=认证、橙=控制、红=攻击、灰=真值。
- 红虚线框 = 注入点；灰底虚线框 = 真值（仅评估）。
- 文字为中文；SVG 里已声明常见 CJK 字体回退，PNG 内嵌渲染结果，任何查看器都能显示。

## 修改建议

- 节点/卡片用 `SVG.card()`；箭头用 `SVG.arrow()`；小标签用 `SVG.chip()`。
- 中文文本按"字符数 × 字号"估算宽度；卡片内文本建议 ≤ 卡宽 / 字号 × 0.95。
- 改完先 `--svg-only` 检查坐标，再生成 PNG；可用 `scripts/ui_smoke.py` 同样的 Playwright 环境。
