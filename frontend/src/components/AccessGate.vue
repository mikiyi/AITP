<template>
  <div v-if="!ready || !authState.online || needLogin" class="access-gate">
    <div class="access-gate__card">
      <div class="access-gate__brand">
        <img class="access-gate__logo" src="/brand-logo.svg" alt="" aria-hidden="true" />
        <BrandName />
      </div>

      <template v-if="!ready">
        <el-icon class="is-loading access-gate__spin"><Loading /></el-icon>
        <p class="access-gate__text">正在检查访问口令…</p>
      </template>

      <template v-else-if="!authState.online">
        <p class="access-gate__text">无法连接后端服务，请确认 AITP 已启动。</p>
        <el-button type="primary" :loading="checking" @click="check">重新连接</el-button>
      </template>

      <template v-else>
        <p class="access-gate__title">本机访问口令</p>
        <p class="access-gate__text">AITP 已启用访问口令，输入后进入工作台。</p>
        <el-input
          ref="pwdInput"
          v-model="password"
          type="password"
          size="large"
          show-password
          placeholder="请输入访问口令"
          autocomplete="current-password"
          @keyup.enter="submit"
        />
        <p v-if="error" class="access-gate__error">{{ error }}</p>
        <el-button
          type="primary"
          size="large"
          class="access-gate__submit"
          :loading="submitting"
          :disabled="!password"
          @click="submit"
        >
          进入工作台
        </el-button>
        <p class="access-gate__tip">
          口令只在本机校验。忘记口令时，关闭 AITP 后删除
          <code>data/access_password.json</code> 再启动即可重置。
        </p>
      </template>
    </div>
  </div>
  <slot v-else />
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import BrandName from './BrandName.vue'
import { AuthError, authState, fetchAuthStatus, login } from '@/api/auth'

const ready = ref(false)
const checking = ref(false)
const submitting = ref(false)
const password = ref('')
const error = ref('')
const pwdInput = ref<{ focus: () => void } | null>(null)

const needLogin = computed(() => authState.enabled && !authState.authenticated)

function focusPassword(): void {
  void nextTick(() => pwdInput.value?.focus())
}

async function check(): Promise<void> {
  checking.value = true
  error.value = ''
  try {
    await fetchAuthStatus()
  } catch {
    // 连不上时保留上次的在线状态判断，由卡片给出重试入口
  } finally {
    checking.value = false
    ready.value = true
  }
}

async function submit(): Promise<void> {
  if (!password.value || submitting.value) {
    return
  }
  submitting.value = true
  error.value = ''
  try {
    await login(password.value)
    password.value = ''
  } catch (err) {
    error.value = err instanceof AuthError ? err.message : '登录失败，请重试'
    password.value = ''
    focusPassword()
  } finally {
    submitting.value = false
  }
}

watch(needLogin, (locked) => {
  if (locked) {
    focusPassword()
  }
})

onMounted(async () => {
  await check()
  if (needLogin.value) {
    focusPassword()
  }
})
</script>

<style scoped>
.access-gate {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100vw;
  height: 100vh;
  padding: 24px;
  background: var(--aitp-bg);
  color: var(--aitp-text);
}

.access-gate__card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
  max-width: 380px;
  padding: 28px 26px 22px;
  background: var(--aitp-panel);
  border: 1px solid var(--aitp-border);
  border-radius: var(--aitp-radius);
  box-shadow: var(--aitp-shadow);
}

.access-gate__brand {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  margin-bottom: 4px;
  /* 与侧栏品牌区同一字号，口令页不再出现两种大小的产品名 */
  font-weight: 700;
  font-size: 20px;
}

.access-gate__logo {
  width: 32px;
  height: 32px;
}

.access-gate__spin {
  align-self: center;
  font-size: 22px;
  color: var(--aitp-primary);
}

.access-gate__title {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  text-align: center;
}

.access-gate__text {
  margin: 0;
  color: var(--aitp-text-weak);
  font-size: 13px;
  line-height: 1.6;
  text-align: center;
}

.access-gate__error {
  margin: 0;
  color: var(--el-color-danger);
  font-size: 13px;
  text-align: center;
}

.access-gate__submit {
  width: 100%;
}

.access-gate__tip {
  margin: 4px 0 0;
  color: var(--aitp-text-weak);
  font-size: 12px;
  line-height: 1.6;
}

.access-gate__tip code {
  padding: 0 3px;
  background: var(--aitp-subtle);
  border-radius: 3px;
  font-family: Consolas, monospace;
}
</style>
