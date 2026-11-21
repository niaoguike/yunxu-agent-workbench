"""Real browser acceptance for the personal entry; uses a fresh local test server."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parents[1]
data = root / ".scratch/personal-agent/browser-check"
data.mkdir(parents=True, exist_ok=True)
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
url = f"http://127.0.0.1:{port}"
env = {
    **os.environ,
    "WORKBENCH_DATA": str(data),
    "WORKBENCH_ORIGIN": url,
    "WORKBENCH_RUNNER": "disabled",
    "PYTHONPATH": str(root / "backend"),
}
process = subprocess.Popen(
    [
        sys.executable,
        "-m",
        "uvicorn",
        "workbench.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ],
    cwd=root,
    env=env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
)
try:
    import urllib.request

    for _ in range(100):
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    else:
        raise AssertionError("Server did not start")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.get_by_role("button", name="创建账户", exact=True).click()
        page.get_by_label("邮箱").fill(f"personal-{time.time_ns()}@example.com")
        page.get_by_label("密码").fill("browser-check-password")
        page.locator(".dialog").get_by_role("button", name="保存", exact=True).click()
        expect(page.get_by_role("heading", name="你的个人助手 · 云序")).to_be_visible()
        page.get_by_label("目标名称").fill("准备演示")
        page.get_by_label("预期结果").fill("完成产品演示")
        page.get_by_label("成功标准（每行一项）").fill("页面可运行")
        page.get_by_role("button", name="创建目标", exact=True).click()
        expect(page.get_by_role("heading", name="准备演示", exact=True)).to_be_visible()
        page.reload()
        expect(page.get_by_role("heading", name="准备演示", exact=True)).to_be_visible()
        page.get_by_role("button", name="连接 gmail", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("尚未配置")
        page.screenshot(path=str(data / "personal-home.png"), full_page=True)
        page.get_by_role("button", name="查看执行、授权与协作者", exact=True).click()
        expect(page.locator(".taskmeta")).to_be_visible()
        assert not errors, errors
        browser.close()
    print(
        "PERSONAL_BROWSER_OK: account entry, goal creation, reload persistence, missing connector config, no page errors"
    )
finally:
    process.terminate()
    process.wait(timeout=10)
