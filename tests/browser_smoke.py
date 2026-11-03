"""Optional browser check: pip install playwright; run against a fresh dev server.

WORKBENCH_BROWSER_URL defaults to http://127.0.0.1:8765. Uses an installed
Chrome/Edge via PLAYWRIGHT_CHANNEL (default chrome), never a personal profile.
"""

import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, expect


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.getenv("PLAYWRIGHT_CHANNEL", "chrome"), headless=True
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(os.getenv("WORKBENCH_BROWSER_URL", "http://127.0.0.1:8765"))
        page.get_by_role("button", name="创建账户", exact=True).click()
        page.get_by_label("邮箱").fill(f"browser-{time.time_ns()}@example.com")
        page.get_by_label("密码").fill("browser-check-password")
        page.locator(".dialog").get_by_role("button", name="保存", exact=True).click()
        expect(page.get_by_text("今天，想完成什么？")).to_be_visible()
        page.get_by_role("button", name="创建第一个项目").click()
        page.get_by_label("名称", exact=True).fill("端到端验证项目")
        page.locator(".dialog").get_by_role("button", name="保存", exact=True).click()
        page.get_by_role("button", name="新任务", exact=False).click()
        page.get_by_label("任务标题").fill("研究与需求交付")
        page.get_by_label("工作模式").select_option("development")
        page.locator(".dialog").get_by_role("button", name="保存", exact=True).click()
        expect(page.locator(".stages")).to_contain_text("验收评审")
        page.get_by_label("任务消息").fill("生成一份研究报告")
        page.get_by_role("button", name="发送", exact=False).click()
        expect(page.get_by_role("alert")).to_contain_text("Runner disabled")
        page.get_by_role("button", name="关闭", exact=True).click()
        page.get_by_role("button", name="记忆", exact=True).click()
        page.get_by_role("button", name="新增", exact=False).click()
        page.get_by_label("分类", exact=True).fill("project")
        page.get_by_label("内容", exact=True).fill("所有时间统一使用 UTC")
        page.locator(".dialog").get_by_role("button", name="保存", exact=True).click()
        expect(page.locator(".inspector")).to_contain_text("所有时间统一使用 UTC")
        page.get_by_role("button", name="文件", exact=True).click()
        page.locator("input[type=file]").set_input_files(
            {
                "name": "report.html",
                "mimeType": "text/html",
                "buffer": "<h1>浏览器验证</h1>".encode(),
            }
        )
        page.get_by_role("button", name="report.html", exact=False).click()
        expect(page.get_by_label("文件内容")).to_have_value("<h1>浏览器验证</h1>")
        page.get_by_role("button", name="预览", exact=True).click()
        expect(
            page.frame_locator('iframe[title="文件预览"]').get_by_role(
                "heading", name="浏览器验证"
            )
        ).to_be_visible()
        page.get_by_role("button", name="任务设置", exact=True).click()
        page.get_by_label("工作模式").select_option("free")
        page.locator(".dialog").get_by_role("button", name="保存", exact=True).click()
        expect(page.locator(".taskmeta")).to_contain_text("自由模式")
        path = Path(os.getenv("WORKBENCH_SCREENSHOT", ".data/browser-smoke.png"))
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
        page.reload()
        expect(page.locator("aside")).to_contain_text("端到端验证项目")
        assert not errors, errors
        browser.close()
        print(
            "Browser smoke passed: register, project, task, unavailable engine, memory, upload, sandbox preview, mode switch, reload"
        )


if __name__ == "__main__":
    main()
