from app.presentation.http.admin_home import build_admin_home_html


def test_admin_home_page_contains_expected_links() -> None:
    html = build_admin_home_html(
        current_origin="https://api.xrinvest.uz",
        public_origin="https://api.xrinvest.uz",
        project_name="XR Invest Backend",
        api_prefix="/api/v1",
        show_api_docs=True,
    )

    assert "Admin Home" in html
    assert 'href="https://api.xrinvest.uz/admin-panel/"' in html
    assert 'href="https://api.xrinvest.uz/admin/learning"' in html
    assert 'href="https://api.xrinvest.uz/health"' in html
    assert 'href="https://api.xrinvest.uz/docs"' in html
    assert "https://api.xrinvest.uz" in html
    assert "/api/v1" in html


def test_admin_home_page_hides_docs_when_disabled() -> None:
    html = build_admin_home_html(
        current_origin="https://api.xrinvest.uz",
        public_origin="https://api.xrinvest.uz",
        project_name="XR Invest Backend",
        api_prefix="/api/v1",
        show_api_docs=False,
    )

    assert 'href="https://api.xrinvest.uz/admin-panel/"' in html
    assert 'href="https://api.xrinvest.uz/docs"' not in html
    assert "API Docs" not in html
