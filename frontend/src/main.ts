import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'
import { installGlobalErrorGuard } from './composables/useGlobalError'
import { useThemeStore } from './stores/theme'
import './styles/main.css'
import './styles/theme.css'

const app = createApp(App)

Object.entries(ElementPlusIconsVue).forEach(([name, component]) => {
  app.component(name, component)
})

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })

// 皮肤要在挂载前落到 <html data-theme> 上，避免首屏闪一下默认配色
useThemeStore().init()

// 兜底页要在挂载前登记：首屏渲染自己抛出的异常也要能被捕获到
installGlobalErrorGuard(app)

app.mount('#app')
