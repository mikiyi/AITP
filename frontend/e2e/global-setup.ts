/**
 * E2E 全局准备：拉起隔离工装并播种，绝不碰正式 8760 / data/aitp.db。
 *  - mock 模型: 18790(openai)/18791(ollama)
 *  - 后端: 8762 + 临时库
 *  - vite: 5199 代理到 8762
 * 状态写入 e2e/.state.json 供 teardown 清理。
 */
import { spawn, type ChildProcess } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontendDir = path.resolve(here, '..')
const root = path.resolve(frontendDir, '..')
const python = path.join(root, '.venv', 'Scripts', 'python.exe')

const BACKEND = 'http://127.0.0.1:8762'
const VITE = 'http://127.0.0.1:5199'

interface State { tmp: string; pids: number[] }

async function waitHttp(url: string, tries = 60): Promise<void> {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url)
      if (r.status < 500) return
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error(`等待 ${url} 超时`)
}

function start(cmd: string, args: string[], cwd: string, env: NodeJS.ProcessEnv, log: string): ChildProcess {
  const out = fs.openSync(log, 'a')
  const child = spawn(cmd, args, { cwd, env: { ...process.env, ...env }, stdio: ['ignore', out, out], detached: false })
  return child
}

async function api(method: string, p: string, body?: unknown): Promise<any> {
  const r = await fetch(`${BACKEND}/api/v1${p}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return r.json()
}

export default async function globalSetup(): Promise<void> {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'aitp-e2e-'))
  const pids: number[] = []

  // 1. mock 模型
  const mock = start(python, ['-u', 'backend/scripts/mock_openai_server.py', '--port', '18790', '--ollama-port', '18791'], root, { PYTHONIOENCODING: 'utf-8' }, path.join(tmp, 'mock.log'))
  pids.push(mock.pid!)

  // 2. 隔离后端
  const backend = start(python, ['-u', '-X', 'utf8', 'backend/run.py'], root, {
    AITP_PORT: '8762',
    AITP_DB_PATH: path.join(tmp, 'e2e.db'),
    AITP_LOG_PATH: path.join(tmp, 'e2e.log'),
    AITP_DEPLOY_LOG_PATH: path.join(tmp, 'deploy.log'),
    AITP_SECRET_KEY_PATH: path.join(tmp, '.secret_key'),
    PYTHONIOENCODING: 'utf-8',
  }, path.join(tmp, 'backend.log'))
  pids.push(backend.pid!)
  await waitHttp(`${BACKEND}/api/v1/health`)

  // 3. 播种：示例项目 + 本地 mock AI 档案（默认，零额度）
  await api('POST', '/onboarding/demo')
  // 关掉首启引导，避免遮罩挡住 E2E 交互
  await api('POST', '/onboarding', { action: 'complete' })
  await api('POST', '/settings/ai/profiles', {
    name: 'E2E 本地 mock', provider: 'ollama', protocol: 'ollama_native',
    baseUrl: 'http://127.0.0.1:18791', model: 'mock-qwen3:1.7b',
    isLocal: true, isActive: true, isDefault: true, priority: 999,
  })

  // 4. vite dev
  const vite = start(process.execPath, [path.join(frontendDir, 'node_modules', 'vite', 'bin', 'vite.js'), '--port', '5199', '--host', '127.0.0.1', '--strictPort'], frontendDir, {
    AITP_DEV_PORT: '5199', AITP_PROXY_TARGET: BACKEND,
  }, path.join(tmp, 'vite.log'))
  pids.push(vite.pid!)
  await waitHttp(VITE)

  const state: State = { tmp, pids }
  fs.writeFileSync(path.join(here, '.state.json'), JSON.stringify(state))
}
