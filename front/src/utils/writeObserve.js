/**
 * 写入观察期判定（纯函数，不依赖浏览器 API，便于单测与跨端复用）
 *
 * 背景：采集端（或拷贝工具）向受试者目录写入文件时，浏览器目录扫描可能在
 * 文件只写了一半时就读到它 —— 表现为"同一份心电先入库 704.1 KB，61 秒后
 * 又入库 1004.1 KB"，列表里出现两条。
 *
 * 判据：双轮读数比对。首见文件只登记基线（size + lastModified）不上传；
 * 下一轮扫描读到与基线**完全相同**的读数才认为写入已结束。
 * 观察期天然等于一个自动扫描周期，不需要额外的绝对秒数下限 ——
 * "mtime 距今 N 秒"这类绝对时间窗会被"保留源文件 mtime 的拷贝"
 * （robocopy /COPY:DAT、rsync -t）绕过。
 *
 * 残余风险：写入停顿恰好跨越一轮（≥ 一个扫描周期的静默）仍会被误判为
 * 已完成，此时靠后端的同源收敛（scanner._collapse_subject_sources /
 * purge_duplicate_source_assets）兜底，最终收敛为一条。
 */

/**
 * 明确的写入静默窗口：最近 30 秒内被修改过的文件视为**确定仍在写入**，
 * 即使基线读数恰好相同也不放行（覆盖"两次 flush 恰好读到相同的中间值"）。
 * lastModified 晚于当前时间（跨机拷贝保留源机时钟）不算写入中，正常放行。
 */
export const WRITE_QUIET_MS = 30 * 1000

/**
 * 按写入是否结束，把候选文件拆成「可上传」与「继续观察」两组
 *
 * @param {Array<{path: string, size: number, lastModified?: number}>} candidates
 *        本轮 diff 出的新增/变更文件
 * @param {Object} pending 观察期基线 { [path]: { size, lastModified } }
 * @param {Object} [opts]
 * @param {boolean} [opts.observe=true] 关闭时退化为仅按静默窗口过滤
 * @param {number}  [opts.now=Date.now()] 当前时间戳（注入便于测试）
 * @param {number}  [opts.quietMs=WRITE_QUIET_MS] 静默窗口
 * @returns {{ready: Array, observing: Array, pending: Object}}
 *        ready 本轮可上传；observing 本轮只登记；pending 更新后的基线（需持久化）
 */
export function splitByObservation(candidates, pending = {}, opts = {}) {
  const {
    observe = true,
    now = Date.now(),
    quietMs = WRITE_QUIET_MS,
  } = opts

  const ready = []
  const observing = []
  const next = { ...pending }

  for (const f of candidates) {
    const age = now - (f.lastModified || 0)
    // lastModified 晚于当前时间（跨机拷贝保留源机时钟）不视为写入中
    const stillWriting = age >= 0 && age < quietMs

    if (!observe) {
      if (!stillWriting) ready.push(f)
      continue
    }

    const base = next[f.path]
    const settled = !!base &&
      base.size === f.size &&
      base.lastModified === f.lastModified &&
      !stillWriting

    if (settled) {
      ready.push(f)
      delete next[f.path] // 已可上传，基线使命结束
    } else {
      next[f.path] = { size: f.size, lastModified: f.lastModified }
      observing.push(f)
    }
  }

  return { ready, observing, pending: next }
}
