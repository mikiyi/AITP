/** 分页接口顺序取全量：后端 MAX_PAGE_SIZE=100，前端不能再传 200/500 静默丢数据。 */
export interface PageLike<T> {
  list: T[]
  total: number
  pages?: number
  pageNum?: number
  pageSize?: number
}

export async function fetchAllPages<T>(
  fetchPage: (pageNum: number, pageSize: number) => Promise<PageLike<T>>,
  pageSize = 100,
): Promise<T[]> {
  const first = await fetchPage(1, pageSize)
  const items = [...first.list]
  const pages = first.pages && first.pages > 0
    ? first.pages
    : Math.ceil(first.total / pageSize)
  for (let page = 2; page <= pages && page <= 1000; page += 1) {
    const next = await fetchPage(page, pageSize)
    if (!next.list.length) break
    items.push(...next.list)
  }
  return items
}
