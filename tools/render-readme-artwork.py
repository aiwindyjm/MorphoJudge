"""Render original, dependency-free bilingual README diagrams.

Run with Python 3. Text is explicit and reviewable; no external images/fonts,
JavaScript, generated screenshots or model services are used.
"""
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "diagrams"


def text(x, y, value, size=20, fill="#d7def8", weight=400):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
            f'font-weight="{weight}">{escape(value)}</text>')


def box(x, y, w, h, title, subtitle="", accent="#9291ff"):
    body = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" '
            f'fill="#151e37" stroke="#334269"/>'
            f'<rect x="{x}" y="{y+19}" width="3" height="28" fill="{accent}"/>'
            + text(x+18, y+43, title, 21, "#f1f3ff", 600))
    if subtitle:
        body += text(x+18, y+75, subtitle, 15, "#aab6d8")
    return body


def arrow(x1, y1, x2, y2, dashed=False):
    return (f'<path d="M{x1} {y1} L{x2} {y2}" stroke="#8d9bcc" '
            f'stroke-width="2" fill="none" marker-end="url(#arrow)" '
            + ('stroke-dasharray="6 7"' if dashed else '') + '/>')


def svg(name, language, height, title, description, body):
    markup = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)}</title><desc id="desc">{escape(description)}</desc>
<defs>
  <linearGradient id="bg" x2="1" y2="1"><stop stop-color="#10192f"/><stop offset="1" stop-color="#211b41"/></linearGradient>
  <linearGradient id="glow"><stop stop-color="#59c9d0"/><stop offset="1" stop-color="#b89bff"/></linearGradient>
  <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 1 L9 5 L0 9" fill="none" stroke="#8d9bcc" stroke-width="1.5"/></marker>
</defs>
<rect width="1200" height="{height}" rx="24" fill="url(#bg)"/>
<g font-family="Inter,Segoe UI,Microsoft YaHei,Noto Sans CJK SC,Arial,sans-serif">
{body}
</g></svg>
'''
    (OUT / f"{name}.{language}.svg").write_text(markup, encoding="utf-8")


def render(language):
    zh = language == "zh-CN"
    pick = lambda en, cn: cn if zh else en
    title = pick("Software understanding, with evidence.", "理解软件，从证据开始。")
    body = text(52, 64, "MORPHOJUDGE  /  闪蝶判官", 20, "#a8b6e8", 600)
    body += text(52, 146, title, 42, "#f2f4ff", 700)
    body += text(52, 195, pick("An open-source trust layer for AI-generated software.",
                              "AI 时代的软件可信判断层。"), 24)
    body += text(52, 253, pick("LOCAL FIRST     •     TRACEABLE EVIDENCE     •     HUMAN REVIEW",
                              "本地优先     ·     证据可追溯     ·     人工复核"), 17, "#66d7d1")
    for x, y, r in [(911,92,18),(1065,79,11),(1034,166,20),(929,248,13),(1130,258,17)]:
        body += f'<path d="M{x} {y} L1000 190" stroke="#737bc1" opacity=".5"/>'
        body += f'<circle cx="{x}" cy="{y}" r="{r}" fill="#151e37" stroke="url(#glow)" stroke-width="2"/>'
    body += '<path d="M1000 150 L1026 190 L1000 230 L974 190 Z" fill="url(#glow)"/>'
    svg("hero", language, 320, title, title, body)

    title = pick("The workflow changed. The need to understand did not.",
                 "开发方式变了，理解的责任仍在。")
    body = text(42, 55, title, 29, "#f2f4ff", 650)
    body += text(42, 103, pick("THEN / human-led", "过去 / 人主导"), 17, "#a8b6e8")
    then = [("Intent","需求"),("Design","设计"),("Write code","编码"),("Test & review","测试与审核")]
    now = [("Intent","需求"),("Agent builds","Agent 生成"),("Many-file changes","多文件变更"),("You verify","由人验证")]
    for row, items in enumerate([then, now]):
        y = 126 + row * 162
        if row:
            body += text(42, 266, pick("NOW / agent-assisted", "现在 / Agent 协作"), 17, "#66d7d1")
        for i, pair in enumerate(items):
            x = 42 + i * 287
            body += box(x,y,252,91,pick(*pair),accent="#66d7d1" if row else "#9291ff")
            if i < 3: body += arrow(x+258,y+44,x+278,y+44)
    body += text(42, 430, pick("What changed? Why? What could it affect? Where is the evidence?",
                              "改了什么？为什么？会影响哪里？证据在哪里？"), 22, "#e2d7ff")
    svg("workflow",language,470,title,pick("Two development workflows, not a productivity benchmark.","开发流程示意，不是效率统计。"),body)

    title = pick("Follow the relationship. Open the source.", "沿关联下钻，回到源码。")
    body = text(42,55,title,30,"#f2f4ff",650)
    items = [("Page","页面","/settings"),("Feature","功能",pick("human mapping","人工映射")),
             ("Method","方法","saveSettings"),("Contract","契约","Settings"),
             ("Data operation","数据操作","UPDATE preferences")]
    for i,(en,cn,sub) in enumerate(items):
        x=42+i*230
        body += box(x,107,203,105,pick(en,cn),sub)
        if i < 4: body += arrow(x+207,156,x+222,156)
    body += text(42,253,pick("Conceptual view — each relation has its own meaning, not a runtime sequence.",
                             "关联示意：每条边有独立语义，并不代表运行时执行顺序。"),17,"#aab6d8")
    body += box(42,289,548,116,pick("Source anchor","来源定位"),"snapshot · commit · file · line · rule", "#66d7d1")
    body += box(610,289,548,116,pick("Keep uncertainty visible","保留不确定性"),
                "resolved  /  candidate  /  unresolved", "#bd9cff")
    body += text(42,446,pick("A candidate path suggests possible impact. It does not prove execution.",
                             "候选路径表示可能影响，不能证明行为已经执行。"),20)
    svg("evidence",language,486,title,title,body)

    title = pick("Local facts first. Optional explanations second.", "确定性分析在先，模型解释按需。")
    body = text(42,55,title,30,"#f2f4ff",650)
    body += '<rect x="30" y="91" width="1140" height="341" rx="18" fill="none" stroke="#66d7d1" stroke-dasharray="7 6"/>'
    body += text(50,122,pick("YOUR LOCAL ENVIRONMENT · target architecture","本地环境 · 目标架构"),17,"#66d7d1")
    items=[("Git snapshot","Git 快照"),("Parser + rules","解析与规则"),("Evidence + limits","证据与限制")]
    for i,pair in enumerate(items):
        x=50+i*280
        body += box(x,150,252,95,pick(*pair))
        if i < 2: body += arrow(x+257,195,x+275,195)
    body += arrow(876,195,916,195)
    body += box(924,150,224,95,pick("Human review","人工复核"))
    body += arrow(744,250,744,288,True)
    body += box(610,300,538,104,pick("Local model · planned integration","本地模型 · 待接入"),
                pick("Read selected evidence; never rewrite facts.","只解释已选证据，不改写事实。"),"#bd9cff")
    body += text(50,334,pick("No target code execution.","不执行被分析代码。"),20)
    body += text(50,371,pick("No code upload by default.","默认不上传代码。"),20)
    body += text(42,474,pick("Future remote providers require explicit, per-analysis data authorization.",
                             "未来远程 Provider 必须获得每次分析的数据范围授权。"),20,"#aab6d8")
    svg("local-first",language,516,title,title,body)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for language in ("en", "zh-CN"):
        render(language)
