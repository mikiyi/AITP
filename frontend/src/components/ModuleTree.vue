<template>
  <div class="module-tree">
    <el-tree
      ref="treeRef"
      :data="nodes"
      :props="treeProps"
      node-key="id"
      :expand-on-click-node="false"
      :default-expanded-keys="expandedKeys"
      highlight-current
      @node-click="onSelect"
    >
      <template #default="{ data }">
        <div class="module-tree__node">
          <el-tag :type="typeTag(data.nodeType)" size="small" effect="plain">
            {{ typeLabel(data.nodeType) }}
          </el-tag>
          <span class="module-tree__name">{{ data.name }}</span>
          <span v-if="data.references" class="module-tree__count">
            用例 {{ data.references.cases ?? 0 }} · 缺陷 {{ data.references.bugs ?? 0 }}
          </span>
        </div>
      </template>
    </el-tree>
    <el-empty v-if="!loading && !nodes.length" description="暂无模块，请先在模块树管理中创建" :image-size="72" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ModuleNode } from '@/api/types'

const props = withDefaults(
  defineProps<{
    nodes: ModuleNode[]
    loading?: boolean
    autoExpand?: boolean
  }>(),
  { loading: false, autoExpand: true },
)

const emit = defineEmits<{
  (event: 'select', node: ModuleNode): void
}>()

const treeRef = ref<{ setCurrentKey: (key: number) => void } | null>(null)

const treeProps = { children: 'children', label: 'name' }

const expandedKeys = computed(() => {
  if (!props.autoExpand) {
    return []
  }
  const keys: number[] = []
  const walk = (list: ModuleNode[]): void => {
    list.forEach((node) => {
      keys.push(node.id)
      if (node.children?.length) {
        walk(node.children)
      }
    })
  }
  walk(props.nodes)
  return keys
})

function typeLabel(nodeType: string): string {
  if (nodeType === 'module') {
    return '模块'
  }
  if (nodeType === 'submodule') {
    return '子模块'
  }
  return '功能'
}

function typeTag(nodeType: string): 'primary' | 'success' | 'warning' {
  if (nodeType === 'module') {
    return 'primary'
  }
  if (nodeType === 'submodule') {
    return 'success'
  }
  return 'warning'
}

function onSelect(data: ModuleNode): void {
  emit('select', data)
}
</script>

<style scoped>
.module-tree__node {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.module-tree__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.module-tree__count {
  font-size: 12px;
  color: var(--aitp-text-weak);
  margin-left: auto;
}
</style>
