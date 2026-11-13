/** E2E 全局清理：树杀工装进程并删除临时目录。 */
import { execSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))

export default async function globalTeardown(): Promise<void> {
  const stateFile = path.join(here, '.state.json')
  if (!fs.existsSync(stateFile)) return
  const state = JSON.parse(fs.readFileSync(stateFile, 'utf8')) as { tmp: string; pids: number[] }
  for (const pid of state.pids) {
    try {
      if (process.platform === 'win32') execSync(`taskkill /PID ${pid} /T /F`, { stdio: 'ignore' })
      else process.kill(pid, 'SIGKILL')
    } catch { /* already gone */ }
  }
  try { fs.rmSync(state.tmp, { recursive: true, force: true }) } catch { /* ignore */ }
  fs.rmSync(stateFile, { force: true })
}
