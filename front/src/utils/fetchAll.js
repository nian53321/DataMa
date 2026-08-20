/**
 * 循环分页拉取全量列表数据
 *
 * 用于一次性需要完整列表的场景（导出选择、概览统计等），
 * 避免固定大 page_size（如 500/1000）在数据量超限后静默截断。
 *
 * @param {Function} fetchPage - 分页接口，签名 (params) => Promise，
 *        响应结构 { data: { items: Array, total: number } }
 * @param {Object} params - 额外查询参数（与 page/page_size 合并）
 * @param {number} pageSize - 每页条数，默认 500
 * @returns {Promise<Array>} 全量 items
 */
export async function fetchAllPages(fetchPage, params = {}, pageSize = 500) {
  let page = 1
  let items = []
  // 防御上限：接口 total 异常（如恒大于返回数）时避免死循环
  const MAX_PAGES = 100
  while (page <= MAX_PAGES) {
    const res = await fetchPage({ ...params, page, page_size: pageSize })
    const data = res?.data || {}
    const pageItems = data.items || []
    items = items.concat(pageItems)
    const total = Number(data.total) || 0
    if (items.length >= total || pageItems.length < pageSize) break
    page += 1
  }
  return items
}
