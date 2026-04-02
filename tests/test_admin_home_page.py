from app.presentation.http.admin_home import build_admin_home_html


def test_admin_home_page_contains_expected_links() -> None:
    html = build_admin_home_html(
        current_origin="https://api.xrinvest.uz",
        public_origin="https://api.xrinvest.uz",
        project_name="XR Invest Backend",
        api_prefix="/api/v1",
    )

    assert "Admin Home" in html
    assert 'href="https://api.xrinvest.uz/admin-panel/"' in html
    assert 'href="https://api.xrinvest.uz/admin/learning"' in html
    assert 'href="https://api.xrinvest.uz/health"' in html
    assert 'href="https://api.xrinvest.uz/docs"' in html
    assert "https://api.xrinvest.uz" in html
    assert "/api/v1" in html
