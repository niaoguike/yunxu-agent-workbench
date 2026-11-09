"""Real browser acceptance against a separately started local server."""
import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=os.getenv("PLAYWRIGHT_CHANNEL", "msedge"), headless=True)
        context = browser.new_context(viewport={"width": 1500, "height": 1100})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(os.getenv("WORKBENCH_BROWSER_URL", "http://127.0.0.1:8767"))
        page.get_by_role("button", name="创建账户", exact=True).click()
        page.get_by_label("邮箱").fill(f"chart-{time.time_ns()}@example.com")
        page.get_by_label("密码").fill("browser-check-password")
        page.locator(".dialog").get_by_role("button", name="保存", exact=True).click()
        page.get_by_role("button", name="创建第一个项目").click()
        page.get_by_label("名称", exact=True).fill("CSV 图表验收")
        page.locator(".dialog").get_by_role("button", name="保存", exact=True).click()
        page.get_by_role("button", name="新任务", exact=False).click()
        page.get_by_label("任务标题").fill("销售分析")
        page.locator(".dialog").get_by_role("button", name="保存", exact=True).click()
        page.get_by_label("上传 CSV 生成图表").set_input_files({"name": "sales.csv", "mimeType": "text/csv", "buffer": "月份,收入,季度\n一月,-10,Q1\n二月,30,Q1\n四月,50,Q2\n".encode()})
        card = page.get_by_role("article", name="CSV 图表")
        expect(card).to_be_visible()
        expect(card.get_by_role("img")).to_have_attribute("aria-label", "sales.csv，柱状图，3 行")
        card.get_by_label("图表标题").fill("第二季度收入")
        card.get_by_label("图表类型").select_option("line")
        card.get_by_label("筛选字段").select_option("季度")
        card.get_by_label("筛选值").select_option("Q2")
        expect(card.get_by_role("img")).to_have_attribute("aria-label", "第二季度收入，折线图，1 行")
        card.get_by_role("button", name="保存修改为新版本").click()
        expect(card.get_by_label("图表历史版本")).to_have_value("2")
        card.get_by_label("图表历史版本").select_option("1")
        expect(card.get_by_role("img")).to_have_attribute("aria-label", "sales.csv，柱状图，3 行")
        card.get_by_label("图表历史版本").select_option("2")
        # Close and reopen the page with the same authenticated browser context.
        page.close()
        page = context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(os.getenv("WORKBENCH_BROWSER_URL", "http://127.0.0.1:8767"))
        card = page.get_by_role("article", name="CSV 图表")
        expect(card.get_by_label("图表历史版本")).to_have_value("2")
        expect(card.get_by_label("筛选值")).to_have_value("Q2")
        expect(card.get_by_role("img")).to_have_attribute("aria-label", "第二季度收入，折线图，1 行")
        card.get_by_text("查看数据", exact=True).click()
        expect(card.get_by_role("cell", name="四月", exact=True)).to_be_visible()
        card.get_by_role("button", name="请 Agent 分析当前数据").click()
        expect(card.get_by_role("alert")).to_contain_text("Runner disabled")
        page.get_by_label("上传 CSV 生成图表").set_input_files({"name": "bad.csv", "mimeType": "text/csv", "buffer": b"x,y\na,NaN"})
        expect(page.get_by_role("region", name="交互图表成果").get_by_role("alert").filter(has_text="有限数值")).to_be_visible()
        Path("output").mkdir(exist_ok=True)
        page.screenshot(path="output/csv-chart-browser.png", full_page=True)
        assert not errors, errors
        browser.close()
        print("CSV browser flow passed: upload, filter, version, reopen, table, errors")


if __name__ == "__main__":
    main()
