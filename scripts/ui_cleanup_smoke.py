"""清理弹窗冒烟检查（Playwright，无头浏览器）。

只做预览与交互检查，不执行删除。用法：
    .venv/bin/python scripts/ui_cleanup_smoke.py [url]
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright


def main(url: str = "http://127.0.0.1:8080") -> int:
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 950})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(url, wait_until="networkidle")
        page.click("button[data-tab='runs']")
        page.wait_for_function(
            "() => document.querySelector('#storageBar').textContent.includes('out 占用')",
            timeout=15000)
        bar = page.inner_text("#storageBar")
        print("[1] 占用栏：", bar)
        assert "out 占用" in bar

        page.click("#btnCleanup")
        page.wait_for_selector("#cleanupModal", state="visible")
        page.wait_for_function(
            "() => !document.querySelector('#planBadge').textContent.includes('计算中')")
        badge = page.inner_text("#planBadge")
        cards = page.inner_text("#storageCards").replace("\n", " ")
        print(f"[2] 预览：{badge} | {cards}")
        assert "删除" in badge or "没有符合" in badge

        rows = page.eval_on_selector_all("#planTable tbody tr", "els => els.length")
        print(f"[3] 预览列表：{rows} 行")
        assert rows > 0 or "没有符合" in badge

        page.fill("#keepInput", "999")
        page.wait_for_function(
            "() => document.querySelector('#planBadge').textContent.includes('没有符合')")
        page.check("#cleanupConfirm")
        assert page.is_disabled("#cleanupApply"), "无删除项时不应可执行"
        print("[4] 保留全部：", page.inner_text("#planBadge"))

        page.check("#scopeBatch")
        page.wait_for_timeout(700)
        page.uncheck("#scopeBatch")
        page.wait_for_timeout(400)

        page.screenshot(path="out/ui_cleanup.png", full_page=True)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        assert page.is_hidden("#cleanupModal"), "Esc 未关闭弹窗"
        print("[5] 关闭：OK")

        browser.close()

    if errors:
        print("控制台错误：", errors, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"))
