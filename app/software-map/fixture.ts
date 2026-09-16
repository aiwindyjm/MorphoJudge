import type { MapEdge, MapNode, SoftwareMap } from './model'

const nodes: MapNode[] = [
  { id: 'page-diagnostics', label: '诊断页面', kind: '页面', x: 120, y: 150, source: { file: 'demo/app/diagnostics/page.tsx', line: 8, code: '<button onClick={collectDiagnostics}>生成诊断</button>' } },
  { id: 'feature-diagnostics', label: 'F-01 生成诊断', kind: '功能', x: 295, y: 100, source: { file: 'demo/feature-map.json', line: 2, code: '{ "F-01": { "page": "/diagnostics", "handler": "collectDiagnostics" } }' } },
  { id: 'collect', label: 'collectDiagnostics', kind: '方法', x: 380, y: 235, source: { file: 'demo/diagnostics.ts', line: 10, code: '/** @param sessionId 会话标识 */\nexport function collectDiagnostics(accountId: string) {\n  return sendTelemetry({ accountId });\n}' }, facts: { declaration: { params: ['sessionId'], required: ['ok'], effects: [] }, actual: { params: ['accountId'], required: ['ok'], effects: [] }, complete: true } },
  { id: 'send', label: 'sendTelemetry', kind: '方法', x: 550, y: 320, source: { file: 'demo/telemetry.ts', line: 12, code: '/** @sideEffects none */\nexport async function sendTelemetry(payload: DiagnosticPayload) {\n  await fetch(endpoint, { method: "POST", body: JSON.stringify(payload) });\n  return { ok: true };\n}' }, facts: { declaration: { params: ['payload'], required: ['ok'], effects: [] }, actual: { params: ['payload'], required: ['ok'], effects: ['network'] }, complete: true } },
  { id: 'diagnostic-contract', label: 'DiagnosticPayload', kind: '契约', x: 700, y: 185, source: { file: 'demo/contracts.ts', line: 3, code: 'type DiagnosticPayload = { accountId: string; sessionId?: string }' } },
  { id: 'telemetry-api', label: '诊断外部 API', kind: '数据', x: 790, y: 375, source: { file: 'demo/config.ts', line: 2, code: 'const endpoint = "https://telemetry.example.invalid/events";' } },
  { id: 'page-settings', label: '设置页面', kind: '页面', x: 110, y: 425, source: { file: 'demo/app/settings/page.tsx', line: 6, code: '<button onClick={saveSettings}>保存设置</button>' } },
  { id: 'feature-settings', label: 'F-02 保存设置', kind: '功能', x: 265, y: 500, source: { file: 'demo/feature-map.json', line: 3, code: '{ "F-02": { "page": "/settings", "handler": "saveSettings" } }' } },
  { id: 'save', label: 'saveSettings', kind: '方法', x: 405, y: 440, source: { file: 'demo/settings.ts', line: 9, code: '/** @returns {{ ok: boolean, revision: number }} */\nexport async function saveSettings(settings: Settings) {\n  await writeSettings(settings);\n  updatePreferences(settings);\n  await sendTelemetry({ accountId: settings.accountId });\n  return { ok: true };\n}' }, facts: { declaration: { params: ['settings'], required: ['ok', 'revision'], effects: [] }, actual: { params: ['settings'], required: ['ok'], effects: [] }, complete: true } },
  { id: 'settings-contract', label: 'Settings', kind: '契约', x: 610, y: 535, source: { file: 'demo/contracts.ts', line: 8, code: 'type Settings = { accountId: string; theme: "light" | "dark" }' } },
  { id: 'write', label: 'writeSettings', kind: '方法', x: 615, y: 435, source: { file: 'demo/storage.ts', line: 5, code: '/** @sideEffects file-write */\nexport function writeSettings(settings: Settings): void {\n  writeFileSync("settings.json", JSON.stringify(settings));\n}' }, facts: { declaration: { params: ['settings'], required: [], effects: ['file-write'] }, actual: { params: ['settings'], required: [], effects: ['file-write'] }, complete: true } },
  { id: 'settings-file', label: 'settings.json', kind: '数据', x: 800, y: 500, source: { file: 'demo/storage.ts', line: 7, code: 'writeFileSync("settings.json", JSON.stringify(settings));' } },
  { id: 'db-write', label: 'UPDATE preferences', kind: '方法', x: 485, y: 570, source: { file: 'demo/preferences.ts', line: 6, code: 'export function updatePreferences(settings: Settings) {\n  db.prepare("UPDATE preferences SET theme = ? WHERE account_id = ?")\n    .run(settings.theme, settings.accountId);\n}' }, facts: { actual: { params: ['settings'], required: [], effects: ['database-write'] }, complete: true } },
  { id: 'preferences-table', label: 'preferences 表', kind: '数据', x: 740, y: 570, source: { file: 'demo/schema.sql', line: 1, code: 'CREATE TABLE preferences (account_id TEXT PRIMARY KEY, theme TEXT NOT NULL);' } },
  { id: 'plugin', label: 'dynamicPlugin', kind: '方法', x: 580, y: 90, source: { file: 'demo/plugins.ts', line: 4, code: 'export function dynamicPlugin(payload: unknown) {\n  return handlers[config.name](payload);\n}' }, facts: { actual: { params: ['payload'], required: [], effects: [] }, complete: false } },
]

const nodeById = new Map(nodes.map((node) => [node.id, node]))
const edge = (from: string, to: string, relation: string, resolution: MapEdge['resolution'] = 'resolved'): MapEdge => ({ from, to, relation, resolution, source: nodeById.get(from)!.source })

export const fixture: SoftwareMap = {
  nodes,
  edges: [
    edge('page-diagnostics', 'feature-diagnostics', '触发'),
    edge('feature-diagnostics', 'collect', '实现映射'),
    edge('collect', 'send', '调用'),
    edge('send', 'diagnostic-contract', '接受'),
    edge('send', 'telemetry-api', '发送'),
    edge('page-settings', 'feature-settings', '触发'),
    edge('feature-settings', 'save', '实现映射'),
    edge('save', 'send', '调用'),
    edge('save', 'settings-contract', '接受'),
    edge('save', 'write', '调用'),
    edge('write', 'settings-contract', '接受'),
    edge('write', 'settings-file', '写入'),
    edge('save', 'db-write', '调用'),
    edge('db-write', 'settings-contract', '接受'),
    edge('db-write', 'preferences-table', '更新'),
    edge('collect', 'plugin', '候选调用', 'candidate'),
    edge('plugin', 'send', '候选调用', 'candidate'),
  ],
  limits: [
    '15 个节点为人工编写的示例快照；尚未加载或解析真实仓库。',
    '动态插件有 2 条候选边；运行时注入、反射及跨语言调用未解析。',
    '未计算跨方法副作用传播、完整类型兼容性或任意自然语言注释等价性。',
    '“全局”仅指当前图覆盖的范围；无路径不等于不受影响，静态路径不代表实际执行。',
  ],
}
