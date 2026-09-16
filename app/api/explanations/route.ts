import { NextResponse } from 'next/server'

export async function POST(request: Request) {
  const body = await request.json().catch(() => null)
  const subject = body?.subject
  if (!subject?.id || !subject?.source?.file) return NextResponse.json({ error: '缺少可解释对象或源码证据' }, { status: 400 })
  const provider = body?.provider ?? 'fake'
  if (provider !== 'fake') return NextResponse.json({ error: '当前原型仅启用本地 Fake Provider；Ollama 接口待分析服务接入' }, { status: 503 })
  const evidenceId = `evidence-${subject.id}`
  return NextResponse.json({
    id: `explanation-${subject.id}`,
    provider: 'fake',
    model: body?.model ?? 'Qwen Coder · Ollama',
    status: 'completed',
    summary: `${subject.label} 位于 ${subject.source.file}，当前解释基于静态源码和关系证据生成。`,
    claims: [
      { text: `确定性证据显示该对象的源码入口位于第 ${subject.source.line} 行。`, evidenceIds: [evidenceId], certainty: 'fact_restatement' },
      { text: '模型推断其作用需要结合上游触发点和下游调用；这不是运行时行为证明。', evidenceIds: [evidenceId], certainty: 'model_inference' },
    ],
    uncertainty: ['未执行目标仓库代码，无法确认运行时是否实际到达该路径。'],
  })
}
