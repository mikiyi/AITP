import { onBeforeUnmount, onMounted, shallowRef, watch, type Ref } from 'vue'
import * as echarts from 'echarts'

/**
 * ECharts 容器封装。
 *
 * 铁律一：容器必须给固定 min-height（不能为 0），否则初始化时高度为 0，
 * 图表会空白；setOption 之后强制 resize，窗口缩放/路由切换才不会掉画布。
 *
 * 铁律二：chart 实例必须用 shallowRef 保存，不能用 ref。
 * ref 会把实例包成 Vue 响应式 Proxy，ECharts 内部靠 === 比较组件模型身份，
 * 被 Proxy 包装后同一模型会变成两个对象，坐标系的轴引用随之丢失，
 * resize 时抛 "Cannot read properties of undefined (reading 'type')"。
 */
export function useEchart(
  container: Ref<HTMLElement | null>,
  option: Ref<echarts.EChartsOption | null>,
) {
  const chart = shallowRef<echarts.ECharts | null>(null)

  function resize(): void {
    chart.value?.resize()
  }

  function render(): void {
    if (!container.value || !option.value) {
      return
    }
    if (!chart.value) {
      chart.value = echarts.init(container.value)
    }
    chart.value.setOption(option.value, true)
    resize()
  }

  onMounted(() => {
    // DOMContentLoaded 之后再取一次尺寸，避免首帧容器尚未布局
    window.setTimeout(render, 200)
  })

  onBeforeUnmount(() => {
    chart.value?.dispose()
    chart.value = null
  })

  watch(option, () => {
    render()
  }, { deep: true })

  window.addEventListener('resize', resize)

  return { chart, render, resize }
}
