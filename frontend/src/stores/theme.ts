import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

/** 六套皮肤的编号。 */
export type ThemeKey = 'white' | 'black' | 'blue' | 'red' | 'pink' | 'orange'

export interface ThemeOption {
  key: ThemeKey
  label: string
  /** 切换器里的预览色：底色 / 描边 / 主色。 */
  swatch: { bg: string; border: string; primary: string }
  /** 深色皮肤：文字需要反色。 */
  dark: boolean
}

/** 皮肤清单，顺序与切换器展示顺序一致。 */
export const THEMES: ThemeOption[] = [
  { key: 'white', label: '白色', swatch: { bg: '#ffffff', border: '#e4e7ed', primary: '#2f6fed' }, dark: false },
  { key: 'black', label: '黑色', swatch: { bg: '#14161a', border: '#32363f', primary: '#4d8bff' }, dark: true },
  { key: 'blue', label: '蓝色', swatch: { bg: '#eef3fd', border: '#d5dff5', primary: '#1a56db' }, dark: false },
  { key: 'red', label: '红色', swatch: { bg: '#fdf1f1', border: '#f3d5d6', primary: '#d9363e' }, dark: false },
  { key: 'pink', label: '粉色', swatch: { bg: '#fdf0f6', border: '#f4d4e5', primary: '#d63384' }, dark: false },
  { key: 'orange', label: '橙色', swatch: { bg: '#fff6ec', border: '#f5ddc2', primary: '#e0761b' }, dark: false },
]

const STORAGE_KEY = 'aitp.theme'

function isThemeKey(value: unknown): value is ThemeKey {
  return typeof value === 'string' && THEMES.some((item) => item.key === value)
}

/** 皮肤状态：写入 <html data-theme>，Element Plus 的主题色随之整体切换。 */
export const useThemeStore = defineStore('theme', () => {
  const theme = ref<ThemeKey>('white')

  const current = computed(
    () => THEMES.find((item) => item.key === theme.value) ?? THEMES[0],
  )

  /** 切换皮肤：先改 DOM 属性，再更新状态，保证依赖颜色的组件读到的是新值。 */
  function apply(key: ThemeKey): void {
    if (!isThemeKey(key)) {
      return
    }
    document.documentElement.setAttribute('data-theme', key)
    theme.value = key
    try {
      localStorage.setItem(STORAGE_KEY, key)
    } catch {
      // 隐私模式下写不进去也不影响本次使用
    }
  }

  /** 启动时读取上次选择；读不到就用白色。 */
  function init(): void {
    let saved: string | null = null
    try {
      saved = localStorage.getItem(STORAGE_KEY)
    } catch {
      saved = null
    }
    apply(isThemeKey(saved) ? saved : 'white')
  }

  return { theme, themes: THEMES, current, apply, init }
})
