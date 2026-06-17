def render(title: str, sections: list[dict]) -> str:
    css = """
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               max-width: 860px; margin: 40px auto; padding: 0 24px;
               color: #1a1a1a; line-height: 1.6; background: #fff; }
        h1   { font-size: 1.75rem; font-weight: 700; margin-bottom: 0.25rem; }
        h2   { font-size: 1.15rem; font-weight: 600; margin-top: 2rem;
               border-bottom: 1px solid #e5e5e5; padding-bottom: 0.35rem; }
        p    { margin: 0.75rem 0; }
        hr   { border: none; border-top: 1px solid #e5e5e5; margin: 2rem 0; }
    """
    body_parts = [f"<h1>{title}</h1>"]
    for section in sections:
        heading = section.get("heading", "")
        body = section.get("body", "")
        if heading:
            body_parts.append(f"<h2>{heading}</h2>")
        if body:
            paragraphs = "".join(f"<p>{p.strip()}</p>" for p in body.split("\n\n") if p.strip())
            body_parts.append(paragraphs)
    return (
        "<!DOCTYPE html><html lang='en'><head>"
        "<meta charset='UTF-8'>"
        f"<title>{title}</title>"
        f"<style>{css}</style>"
        "</head><body>"
        + "\n".join(body_parts)
        + "</body></html>"
    )
