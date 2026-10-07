from pathlib import Path

from playwright.sync_api import Page, expect


def test_smoke(server: str, page: Page, artifacts: Path) -> None:
    page.goto(server)
    expect(page.get_by_role("heading", name="Rubricate")).to_be_visible()
    expect(page.get_by_text("Version 0.1.0")).to_be_visible()
    page.screenshot(path=artifacts / "smoke.png")

    response = page.goto(f"{server}/courses/cs101-f26")
    assert response is not None and response.status == 200
    expect(page).to_have_title("Rubricate")
