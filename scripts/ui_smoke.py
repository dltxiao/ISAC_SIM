"""Web 控制台冒烟检查（Playwright，无头浏览器）。

用法：先启动 `isacsim web --port 8080`，再运行
    .venv/bin/python scripts/ui_smoke.py [url]
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main(url: str = "http://127.0.0.1:8080") -> int:
    errors: list[str] = []
    console: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.on("console", lambda m: (console.append(m.text),
                                      errors.append(m.text) if m.type == "error" else None))
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(url, wait_until="networkidle")
        print("[1] 页面加载：", page.title())

        page.fill("#durationInput", "30")
        page.click("#btnStart")
        page.wait_for_timeout(4000)
        vehicles = page.inner_text("#vehCount")
        sim_time = page.inner_text("#simTime")
        canvas_len = page.evaluate("document.getElementById('map').toDataURL().length")
        print(f"[2] 运行中：车辆={vehicles} 仿真时间={sim_time} 画布数据长度={canvas_len}")
        assert vehicles not in ("0", ""), "画布没有车辆"
        assert canvas_len > 20000, "画布为空"

        page.click("button[data-tab='attack']")
        page.select_option("#attackType", "A1")
        page.fill("#attackCount", "2")
        page.click("#btnInject")
        page.wait_for_timeout(4000)
        camp = page.inner_text("#statCamp")
        alerts = page.inner_text("#statAlerts")
        truth = page.inner_text("#statTruth")
        print(f"[3] 注入 A1×2：跨层攻击={camp} 告警={alerts} 真值事件={truth}")
        assert truth not in ("0", ""), "真值事件计数未显示"
        assert camp not in ("0", ""), "攻击计数未增加"

        page.click("button[data-tab='stats']")
        text = page.inner_text("#tab-stats")
        assert "A1" in text

        page.click("button[data-tab='infra']")
        infra = page.inner_text("#tab-infra")
        assert "语义核心网" in infra and "V2X 平台" in infra
        print("[4] 网络/平台视图：", "OK")

        page.click("#btnPause")
        page.wait_for_timeout(800)
        badge = page.inner_text("#stateBadge")
        print(f"[4] 暂停：状态={badge}")
        assert badge == "已暂停"

        page.screenshot(path="out/ui_smoke.png", full_page=True)
        page.click("#btnStop")
        page.wait_for_timeout(1500)
        print("[5] 停止：", page.inner_text("#stateBadge"))

        browser.close()

    if errors:
        print("控制台错误：")
        for e in errors[:20]:
            print("  -", e)
        return 2
    print("UI 冒烟通过，截图：out/ui_smoke.png")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"))
