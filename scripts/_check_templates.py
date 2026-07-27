from pathlib import Path
for f in ["templates/landing.html", "templates/register.html", "templates/index.html"]:
    t = Path(f).read_text(encoding="utf-8")
    print(
        f,
        "len",
        len(t),
        "carousel",
        "hero-carousel" in t,
        "view-bot",
        'id="view-bot"' in t,
        "report-novos",
        "report-novos" in t,
        "bot-meta-bar",
        "bot-meta-bar" in t,
    )
idx = Path("templates/index.html").read_text(encoding="utf-8")
print("main", idx.count("<main"), idx.count("</main>"))
print("leads", idx.count('id="view-leads"'))
print("users", idx.count('id="view-users"'))
