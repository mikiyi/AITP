<template>
  <div class="page">
    <div class="page-header">
      <h2 class="page-title">访问口令</h2>
      <div class="toolbar" style="margin: 0">
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-button v-if="authState.enabled" :icon="Lock" @click="lockNow">立即锁定</el-button>
      </div>
    </div>

    <div class="panel panel-pad">
      <div class="block-head">
        <h3>本地访问口令</h3>
        <el-tag :type="authState.enabled ? 'success' : 'info'" effect="plain" size="small">
          {{ authState.enabled ? '已启用' : '未启用' }}
        </el-tag>
        <span v-if="authState.passwordUpdatedAt" class="muted">
          最近修改：{{ formatTime(authState.passwordUpdatedAt) }}
        </span>
      </div>
      <p class="muted intro">
        启用后，浏览器打开 AITP 要先输入口令才能访问数据；服务重启后需重新输入，单次登录
        {{ ttlHours }} 小时有效。口令只校验浏览器访问，挡不住能直接读写本机数据文件的人。
      </p>

      <el-form v-if="!authState.enabled" label-width="120px" class="access-form" @submit.prevent>
        <el-form-item label="新口令">
          <el-input
            v-model="createForm.password"
            type="password"
            show-password
            autocomplete="new-password"
            placeholder="至少 6 位"
          />
        </el-form-item>
        <el-form-item label="确认新口令">
          <el-input
            v-model="createForm.confirm"
            type="password"
            show-password
            autocomplete="new-password"
            placeholder="再输入一次"
            @keyup.enter="enablePassword"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="saving" @click="enablePassword">启用访问口令</el-button>
        </el-form-item>
      </el-form>

      <template v-else>
        <el-form label-width="120px" class="access-form" @submit.prevent>
          <el-form-item label="当前口令">
            <el-input v-model="changeForm.current" type="password" show-password autocomplete="current-password" />
          </el-form-item>
          <el-form-item label="新口令">
            <el-input
              v-model="changeForm.password"
              type="password"
              show-password
              autocomplete="new-password"
              placeholder="至少 6 位"
            />
          </el-form-item>
          <el-form-item label="确认新口令">
            <el-input
              v-model="changeForm.confirm"
              type="password"
              show-password
              autocomplete="new-password"
              placeholder="再输入一次"
              @keyup.enter="changePassword"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="saving" @click="changePassword">保存新口令</el-button>
            <span class="muted form-note">修改后其他已登录窗口会立即失效</span>
          </el-form-item>
        </el-form>

        <el-divider content-position="left">关闭访问口令</el-divider>
        <el-form label-width="120px" class="access-form" @submit.prevent>
          <el-form-item label="当前口令">
            <el-input
              v-model="disableForm.password"
              type="password"
              show-password
              autocomplete="current-password"
              placeholder="输入当前口令以确认关闭"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="danger" plain :loading="disabling" @click="disablePassword">关闭访问口令</el-button>
            <span class="muted form-note">关闭后登录态的 Cookie 一并失效</span>
          </el-form-item>
        </el-form>
      </template>
    </div>

    <div class="panel panel-pad notes">
      <h3>使用说明</h3>
      <ul>
        <li>
          口令只保存 PBKDF2 加盐哈希（<code>data/access_password.json</code>），不存明文；该文件不进数据库备份。
        </li>
        <li>登录态是 HttpOnly Cookie，导出与下载链接都能直接用，不需要额外拼接令牌。</li>
        <li>同一客户端连续输错 5 次会锁定 60 秒；服务重启后所有会话失效。</li>
        <li>
          忘记口令：关闭 AITP，删除 <code>data/access_password.json</code> 后重启，即恢复为未启用状态。
        </li>
        <li>
          命令行调用先用 <code>POST /api/v1/auth/login</code> 换取 Cookie，再用该 Cookie 调业务接口；
          不提供长期有效的令牌。
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Lock, Refresh } from '@element-plus/icons-vue'
import {
  AuthError,
  authState,
  fetchAuthStatus,
  logout,
  removePassword,
  savePassword,
} from '@/api/auth'

const createForm = reactive({ password: '', confirm: '' })
const changeForm = reactive({ current: '', password: '', confirm: '' })
const disableForm = reactive({ password: '' })
const saving = ref(false)
const disabling = ref(false)

const ttlHours = computed(() => Math.max(1, Math.round(authState.sessionTtlSeconds / 3600)))

function messageOf(err: unknown, fallback: string): string {
  return err instanceof AuthError ? err.message : fallback
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

async function load(): Promise<void> {
  try {
    await fetchAuthStatus()
  } catch (err) {
    ElMessage.error(messageOf(err, '读取访问口令状态失败'))
  }
}

function validate(password: string, confirm: string): string {
  if (!password) {
    return '请输入新口令'
  }
  if (password.length < 6) {
    return '口令至少 6 位'
  }
  if (password !== confirm) {
    return '两次输入的口令不一致'
  }
  return ''
}

async function enablePassword(): Promise<void> {
  const problem = validate(createForm.password, createForm.confirm)
  if (problem) {
    ElMessage.warning(problem)
    return
  }
  saving.value = true
  try {
    await savePassword({ password: createForm.password, confirmPassword: createForm.confirm })
    createForm.password = ''
    createForm.confirm = ''
    ElMessage.success('访问口令已启用，当前浏览器保持登录')
  } catch (err) {
    ElMessage.error(messageOf(err, '启用访问口令失败'))
  } finally {
    saving.value = false
  }
}

async function changePassword(): Promise<void> {
  if (!changeForm.current) {
    ElMessage.warning('请输入当前口令')
    return
  }
  const problem = validate(changeForm.password, changeForm.confirm)
  if (problem) {
    ElMessage.warning(problem)
    return
  }
  saving.value = true
  try {
    await savePassword({
      currentPassword: changeForm.current,
      password: changeForm.password,
      confirmPassword: changeForm.confirm,
    })
    changeForm.current = ''
    changeForm.password = ''
    changeForm.confirm = ''
    ElMessage.success('口令已更新，其他窗口需要重新登录')
  } catch (err) {
    ElMessage.error(messageOf(err, '修改口令失败'))
  } finally {
    saving.value = false
  }
}

async function disablePassword(): Promise<void> {
  if (!disableForm.password) {
    ElMessage.warning('请输入当前口令')
    return
  }
  disabling.value = true
  try {
    await removePassword(disableForm.password)
    disableForm.password = ''
    ElMessage.success('访问口令已关闭')
  } catch (err) {
    ElMessage.error(messageOf(err, '关闭访问口令失败'))
  } finally {
    disabling.value = false
  }
}

async function lockNow(): Promise<void> {
  try {
    await logout()
    ElMessage.success('已锁定，重新进入需要输入口令')
  } catch (err) {
    ElMessage.error(messageOf(err, '退出登录失败'))
  }
}

onMounted(load)
onActivated(load)
</script>

<style scoped>
.block-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.block-head h3 {
  margin: 0;
  font-size: 15px;
}

.intro {
  margin: 0 0 16px;
  line-height: 1.7;
}

.access-form {
  max-width: 560px;
}

.form-note {
  margin-left: 12px;
  font-size: 12px;
}

.notes {
  margin-top: 16px;
}

.notes h3 {
  margin: 0 0 8px;
  font-size: 15px;
}

.notes ul {
  margin: 0;
  padding-left: 20px;
  color: var(--aitp-text-weak);
  line-height: 1.9;
  font-size: 13px;
}

.notes code {
  padding: 0 3px;
  background: var(--aitp-subtle);
  border-radius: 3px;
  font-family: Consolas, monospace;
}
</style>
