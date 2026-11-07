"""Render actual discoveries in a scrollable live view; no simulated browser activity."""
from html import escape
from models import valid_url

def live_feed_html(rows, follow=True):
    cards=[]
    for row in rows:
        url=row.get("Post link", "")
        if not valid_url(url): continue
        title=escape(str(row.get("Title") or "Post needs text"))
        score=row.get("Score")
        badge=f"{score}/100" if score is not None else escape(str(row.get("Status", "")))
        cards.append(f'''<article><div class="row"><strong>{title}</strong><span>{badge}</span></div>
<p>{escape(str(row.get('Company','')))} · {escape(str(row.get('Source','')))}</p>
<a target="_blank" rel="noopener noreferrer" href="{escape(url,quote=True)}">Open original post ↗</a>
<small>{escape(url)}</small></article>''')
    content="".join(cards) or '<div class="empty">Results will appear here when the search finds or reviews a post.</div>'
    return '''<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;font:14px/1.5 system-ui,sans-serif;color:#183247;background:transparent}
#feed{height:330px;overflow-y:auto;padding:6px;border:1px solid #dbe5ed;border-radius:12px;background:#f5f8fb}
article{background:white;border:1px solid #dbe5ed;border-left:4px solid #07828b;border-radius:10px;padding:16px;margin:0 0 10px}
.row{display:flex;gap:14px;justify-content:space-between}.row span{white-space:nowrap;color:#087b75}
p{margin:5px 0;color:#536778}a{color:#006f85;font-weight:600}small{display:block;overflow-wrap:anywhere;color:#657887;font-size:11px;margin-top:4px}
.empty{padding:60px 22px;text-align:center;color:#536778}
</style></head><body><div id="feed" role="log" aria-label="Live discovered post links">''' + content + '</div>' + ("<script>const f=document.getElementById('feed');f.scrollTop=f.scrollHeight;</script>" if follow else "") + '</body></html>'
