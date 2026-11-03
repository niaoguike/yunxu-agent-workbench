"""Browser regression at the HTTP seam; synthetic run history exists only in this test route."""

import os
import time

from playwright.sync_api import expect, sync_playwright


def main():
    base = os.getenv("WORKBENCH_BROWSER_URL", "http://127.0.0.1:8000")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=os.getenv("PLAYWRIGHT_CHANNEL", "chrome"), headless=True
        )
        context = browser.new_context(viewport={"width": 1400, "height": 1000})
        assert (
            context.request.post(
                base + "/api/auth/register",
                data={
                    "email": f"history-{time.time_ns()}@example.com",
                    "password": "history-regression-password",
                },
            ).status
            == 201
        )
        project = context.request.post(
            base + "/api/projects", data={"name": "History display regression"}
        ).json()
        task = context.request.post(
            base + f"/api/projects/{project['id']}/tasks",
            data={"title": "Delivery history"},
        ).json()
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        state = {"failed": False}
        old_error = "Old failure: artifacts/startup-error.txt missing"
        current_error = "Current failure: required report missing"

        def history(route):
            response = route.fetch()
            payload = response.json()
            payload.update(
                version=3,
                status="failed" if state["failed"] else "delivered",
                runs=[
                    {
                        "id": "old",
                        "version": 1,
                        "status": "failed",
                        "error": old_error,
                        "created_at": 100,
                    },
                    {
                        "id": "latest",
                        "version": 3,
                        "status": "failed" if state["failed"] else "completed",
                        "error": current_error if state["failed"] else None,
                        "created_at": 200,
                    },
                ],
                checks=[
                    {
                        "id": "old-check",
                        "run_id": "old",
                        "name": "Old browser check",
                        "status": "unverified",
                        "evidence": "Not executed",
                        "source": "engine_reported",
                    },
                    {
                        "id": "new-check",
                        "run_id": "latest",
                        "name": "Current file check",
                        "status": "failed" if state["failed"] else "passed",
                        "evidence": "File check evidence",
                        "source": "platform",
                    },
                ],
            )
            route.fulfill(response=response, json=payload)

        page.route(base + f"/api/tasks/{task['id']}", history)

        def open_task():
            page.goto(base)
            page.locator("aside").get_by_role(
                "button", name="History display regression"
            ).click()
            page.locator(".tasknav").get_by_role(
                "button", name="Delivery history", exact=False
            ).click()

        open_task()
        expect(page.locator(".taskmeta")).to_contain_text("已交付")
        expect(page.get_by_text(old_error, exact=True)).not_to_be_visible()
        expect(page.locator(".messages .error")).to_have_count(0)
        page.get_by_text("本次交付检查（1）", exact=True).click()
        expect(
            page.get_by_text("Current file check · passed", exact=True)
        ).to_be_visible()
        expect(
            page.get_by_text("Old browser check · unverified", exact=True)
        ).not_to_be_visible()
        page.get_by_text("历史执行记录（1 次）", exact=True).click()
        expect(page.get_by_text(old_error, exact=True)).to_be_visible()
        expect(
            page.get_by_text("Old browser check · unverified", exact=True)
        ).to_be_visible()
        state["failed"] = True
        open_task()
        expect(page.locator(".messages [role=alert]")).to_have_text(current_error)
        expect(page.get_by_text(old_error, exact=True)).not_to_be_visible()
        assert not errors, errors
        browser.close()
        print(
            "History regression passed: current success hides old errors, history remains accessible, current failure remains visible"
        )


if __name__ == "__main__":
    main()
