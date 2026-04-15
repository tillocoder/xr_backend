from __future__ import annotations

from html import escape


def build_admin_home_html(
    *,
    current_origin: str,
    public_origin: str,
    project_name: str,
    api_prefix: str,
    show_api_docs: bool = True,
) -> str:
    safe_current_origin = escape(current_origin.rstrip("/"))
    safe_public_origin = escape((public_origin or current_origin).rstrip("/"))
    safe_project_name = escape(project_name)
    safe_api_prefix = escape(api_prefix)
    canonical_origin = (public_origin or current_origin).rstrip("/")

    cards = [
        {
            "eyebrow": "Main Entry",
            "title": "Admin Panel",
            "body": "Users, posts, chats, notifications, news, and provider keys in one place.",
            "href": f"{canonical_origin}/admin-panel/",
            "action": "Open Panel",
        },
        {
            "eyebrow": "Content",
            "title": "Learning Admin",
            "body": "Manage lessons, uploads, publishing, and featured learning videos.",
            "href": f"{canonical_origin}/admin/learning",
            "action": "Open Learning",
        },
        {
            "eyebrow": "Diagnostics",
            "title": "Health Check",
            "body": "Quickly confirm that the backend is alive before testing the mobile app.",
            "href": f"{canonical_origin}/health",
            "action": "Check Health",
        },
    ]
    if show_api_docs:
        cards.append(
            {
                "eyebrow": "Reference",
                "title": "API Docs",
                "body": "Open Swagger docs to inspect endpoints, schemas, and admin API routes.",
                "href": f"{canonical_origin}/docs",
                "action": "Open Docs",
            }
        )

    cards_html = "".join(
        f"""
        <article class="card">
          <div class="eyebrow">{escape(card["eyebrow"])}</div>
          <h3>{escape(card["title"])}</h3>
          <p>{escape(card["body"])}</p>
          <a class="card-link" href="{escape(card["href"])}">{escape(card["action"])}</a>
        </article>
        """
        for card in cards
    )

    docs_button_html = ""
    if show_api_docs:
        docs_button_html = (
            f'\n        <a class="button button-secondary" href="{safe_public_origin}/docs">Open API Docs</a>'
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_project_name} Admin</title>
  <style>
    :root {{
      color-scheme: light;
      --bg-1: #f4f8fb;
      --bg-2: #dff6f8;
      --ink: #12233f;
      --muted: #54657f;
      --line: rgba(24, 58, 102, 0.12);
      --panel: rgba(255, 255, 255, 0.86);
      --panel-strong: rgba(255, 255, 255, 0.96);
      --teal: #0cc6b8;
      --teal-deep: #0f8f96;
      --blue: #1759a6;
      --shadow: 0 30px 80px rgba(20, 54, 96, 0.14);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Trebuchet MS", "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(12, 198, 184, 0.22), transparent 28%),
        radial-gradient(circle at top right, rgba(23, 89, 166, 0.16), transparent 24%),
        linear-gradient(180deg, var(--bg-1), #eef5fb 40%, var(--bg-2) 100%);
    }}
    .shell {{
      width: min(1180px, calc(100% - 32px));
      margin: 28px auto 48px;
    }}
    .hero {{
      position: relative;
      overflow: hidden;
      padding: 28px;
      border-radius: 30px;
      border: 1px solid var(--line);
      background: linear-gradient(145deg, rgba(255,255,255,.96), rgba(232,248,250,.92));
      box-shadow: var(--shadow);
    }}
    .hero::after {{
      content: "";
      position: absolute;
      inset: auto -60px -80px auto;
      width: 240px;
      height: 240px;
      border-radius: 999px;
      background: radial-gradient(circle, rgba(12,198,184,.2), rgba(12,198,184,0));
      pointer-events: none;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 9px 14px;
      border-radius: 999px;
      background: rgba(12, 198, 184, 0.14);
      color: var(--teal-deep);
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 16px 0 12px;
      font-size: clamp(34px, 5vw, 58px);
      line-height: 0.98;
      letter-spacing: -0.04em;
    }}
    .lead {{
      max-width: 700px;
      margin: 0;
      color: var(--muted);
      font-size: 18px;
      line-height: 1.6;
    }}
    .hero-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 22px;
    }}
    .button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 48px;
      padding: 0 18px;
      border-radius: 999px;
      text-decoration: none;
      font-weight: 700;
      transition: transform .15s ease, box-shadow .15s ease;
    }}
    .button:hover {{
      transform: translateY(-1px);
    }}
    .button-primary {{
      background: linear-gradient(135deg, var(--teal), #3bd9d1);
      color: #06262a;
      box-shadow: 0 12px 30px rgba(12, 198, 184, 0.24);
    }}
    .button-secondary {{
      background: rgba(23, 89, 166, 0.08);
      color: var(--blue);
      border: 1px solid rgba(23, 89, 166, 0.12);
    }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      margin-top: 24px;
    }}
    .meta-card {{
      padding: 16px 18px;
      border-radius: 22px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.82);
    }}
    .meta-label {{
      display: block;
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }}
    .meta-value {{
      margin: 0;
      font-size: 16px;
      font-weight: 700;
      word-break: break-word;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
      margin-top: 18px;
    }}
    .card {{
      padding: 22px;
      border-radius: 24px;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: 0 18px 45px rgba(17, 37, 74, 0.08);
    }}
    .eyebrow {{
      margin-bottom: 10px;
      color: var(--teal-deep);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }}
    h3 {{
      margin: 0 0 10px;
      font-size: 24px;
      letter-spacing: -0.03em;
    }}
    .card p {{
      margin: 0 0 18px;
      color: var(--muted);
      line-height: 1.6;
    }}
    .card-link {{
      color: var(--blue);
      font-weight: 700;
      text-decoration: none;
    }}
    .tips {{
      margin-top: 18px;
      padding: 20px 22px;
      border-radius: 24px;
      border: 1px solid var(--line);
      background: var(--panel-strong);
    }}
    .tips h2 {{
      margin: 0 0 12px;
      font-size: 22px;
      letter-spacing: -0.03em;
    }}
    .tips ul {{
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
      line-height: 1.7;
    }}
    code {{
      padding: 3px 7px;
      border-radius: 999px;
      background: rgba(23, 89, 166, 0.08);
      color: var(--blue);
      font-family: Consolas, "Courier New", monospace;
      font-size: 0.95em;
    }}
    @media (max-width: 860px) {{
      .meta-grid,
      .cards {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
      <div class="badge">XR Invest Control Room</div>
      <h1>Admin Home</h1>
      <p class="lead">
        This is the public-safe entry page for the admin side of {safe_project_name}.
        Use it when you want a clean landing page instead of going straight into the database panel.
      </p>
      <div class="hero-actions">
        <a class="button button-primary" href="{safe_public_origin}/admin-panel/">Enter Admin Panel</a>
{docs_button_html}
      </div>
      <div class="meta-grid">
        <article class="meta-card">
          <span class="meta-label">Admin Entry</span>
          <p class="meta-value">{safe_public_origin}/admin</p>
        </article>
        <article class="meta-card">
          <span class="meta-label">Public API Base</span>
          <p class="meta-value">{safe_public_origin}</p>
        </article>
        <article class="meta-card">
          <span class="meta-label">API Prefix</span>
          <p class="meta-value"><code>{safe_api_prefix}</code></p>
        </article>
      </div>
    </section>

    <section class="cards">
      {cards_html}
    </section>

    <section class="tips">
      <h2>Quick Notes</h2>
      <ul>
        <li>The SQL admin panel uses the same admin username and password from your backend <code>.env</code>.</li>
        <li>This page now prefers your public admin URL so the same links work outside your local machine too.</li>
        <li>Current local host for this request was <code>{safe_current_origin}</code>.</li>
        <li>For mobile testing, the backend should stay online at <code>{safe_public_origin}</code> while Cloudflare Tunnel is running.</li>
      </ul>
    </section>
  </main>
</body>
</html>
"""
