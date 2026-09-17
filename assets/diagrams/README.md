# README artwork / 文档素材

All eight SVGs are original project artwork, distributed under the repository's
MIT license. They use editable text and vector geometry; no external service,
remote font, stock image or image-generation prompt is required.

| Files (`en` / `zh-CN`) | Purpose |
| --- | --- |
| `hero.*.svg` | Project banner / 项目横幅 |
| `workflow.*.svg` | Human-led and agent-assisted development / 开发流程变化 |
| `evidence.*.svg` | Conceptual relationships and source anchors / 关系与证据 |
| `local-first.*.svg` | Target data boundary, including planned model integration / 目标架构 |

Regenerate with `tools/render-readme-artwork.py` using Python 3 (standard library
only). This is optional artwork maintenance, not a prerequisite to run the
project. Both READMEs embed the corresponding language version.

The butterfly logo was supplied by the project initiator for MorphoJudge:
source `assets/logo/logo-source-2048.png`, display copy `public/logo-96.png`.
It is not presented as independently designed by an AI agent.

`assets/screenshots/feature-trace.png` and `dependency-graph.png` are unmodified
1440×960 browser captures of the Chinese Web prototype on 2026-09-16.
They show hand-authored demonstration data, not connected-repository analysis.
The visible model controls are prototype UI backed by a Fake Provider.
The images do not certify backend functionality.

图解为概念说明，截图为真实原型；两者不混用。没有效率百分比、性能结论、
未实现界面或待生成的占位配图。
