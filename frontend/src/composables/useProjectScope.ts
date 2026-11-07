import { nextTick, watch } from 'vue'
import { useAppStore } from '@/stores/app'

/**
 * 资产列表页的「换项目」联动。
 *
 * 首帧只加载不清筛选项：清 versionId / moduleId 这类跨项目会失配的筛选，
 * 只能发生在用户真的换了项目之后，否则地址栏深链带进来的筛选会在
 * 项目列表加载完成前被抹掉，刷新或分享链接都还原不出当前视图。
 */
export function useProjectScope(handlers: {
  /** 每次项目确定或变化都要重拉的下拉数据。 */
  load: () => void
  /** 真正切换项目后需要清掉的跨项目筛选项。 */
  resetScopedFilters: () => void
}): void {
  const appStore = useAppStore()
  let scopedProjectId: number | null = null
  watch(() => appStore.currentProjectId, async (pid) => {
    const projectId = pid ?? null
    const changed = scopedProjectId !== null && projectId !== null && projectId !== scopedProjectId
    scopedProjectId = projectId
    handlers.load()
    if (!changed) {
      return
    }
    // 必须等新的 extraQuery 传给表格再重拉，否则这一枪打出去的还是上一个项目。
    await nextTick()
    handlers.resetScopedFilters()
  }, { immediate: true })
}
