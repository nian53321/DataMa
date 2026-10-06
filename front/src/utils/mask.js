// 前端隐私遮罩工具
//
// 与后端 app/utils/desensitize.py::mask_value 的算法逐条对齐，
// 供管理员「隐私信息可见性」开关关闭时在渲染层打码。
//
// 边界约定（有意为之，勿改）：
// 1. 仅作用于「展示」，不修改数据本身 —— 编辑表单、提交 payload 仍取原始明文，
//    避免把遮罩值（如 张*）写回数据库。
// 2. 后端对非 admin 角色已在接口层完成真实脱敏，前端不再二次遮罩
//    （见 stores/privacy.js 的 mask getter）。
// 3. hash 算法无法在前端复现（HMAC 密钥仅后端持有）→ 用等长占位符代替。

/** hash 规则的展示占位（后端输出为 8 位十六进制） */
export const MASKED_HASH_PLACEHOLDER = '********'

/** 重复 mask_char 共 n 次（与后端 `mask_char * n` 语义一致） */
function repeatChar(maskChar, n) {
  const ch = maskChar || '*'
  const count = Math.max(Number(n) || 0, 0)
  if (!count) return ''
  // mask_char 最长 4 字符（后端入库时已截断），重复计数与后端一致
  return ch.repeat(count)
}

function maskMiddle(s, keepHead, keepTail, maskChar) {
  const head = Number(keepHead) || 0
  const tail = Number(keepTail) || 0
  if (s.length <= head + tail) return repeatChar(maskChar, s.length)
  return (
    s.slice(0, head) +
    repeatChar(maskChar, s.length - head - tail) +
    (tail > 0 ? s.slice(-tail) : '')
  )
}

/**
 * 对单个值应用脱敏规则
 * @param {*} value 原始值
 * @param {{algorithm:string, keep_head?:number, keep_tail?:number, mask_char?:string}} rule 脱敏规则
 * @returns {*} 遮罩后的值；null/undefined/对象/布尔值原样返回（不承载身份信息，替换会无中生有）
 */
export function maskValue(value, rule) {
  if (value === null || value === undefined) return value
  if (typeof value === 'object' || typeof value === 'boolean') return value
  const s = String(value)
  if (!s) return s

  const algo = rule && rule.algorithm
  const maskChar = (rule && rule.mask_char) || '*'
  const keepHead = Number(rule && rule.keep_head) || 0
  const keepTail = Number(rule && rule.keep_tail) || 0

  switch (algo) {
    case 'redact':
      return '[REDACTED]'
    case 'hash':
      // 前端无法复现 HMAC-SHA256（密钥仅后端持有），用等长占位代替
      return MASKED_HASH_PLACEHOLDER
    case 'mask_all':
      return repeatChar(maskChar, s.length)
    case 'mask_email': {
      const at = s.indexOf('@')
      if (at > 0) {
        const local = s.slice(0, at)
        const domain = s.slice(at)
        let maskedLocal
        if (local.length <= 1) maskedLocal = maskChar
        else if (local.length <= 3) maskedLocal = local.slice(0, 1) + repeatChar(maskChar, local.length - 1)
        else maskedLocal = local.slice(0, 1) + repeatChar(maskChar, local.length - 2) + local.slice(-1)
        return maskedLocal + domain
      }
      // 无 @ 时按 mask_middle 处理（与后端一致）
      return maskMiddle(s, keepHead, keepTail, maskChar)
    }
    case 'mask_middle':
      return maskMiddle(s, keepHead, keepTail, maskChar)
    case 'mask_head':
      if (keepHead <= 0) return repeatChar(maskChar, s.length)
      if (keepHead >= s.length) return s
      return s.slice(0, keepHead) + repeatChar(maskChar, s.length - keepHead)
    case 'mask_tail':
      if (keepTail <= 0) return repeatChar(maskChar, s.length)
      if (keepTail >= s.length) return s
      return repeatChar(maskChar, s.length - keepTail) + s.slice(-keepTail)
    default:
      return repeatChar(maskChar, s.length)
  }
}

/**
 * 把后端规则数组编译成 field_key -> rule 的映射
 * 未启用（is_active === false）的规则不参与遮罩，与后端 _load_config 口径一致
 * @param {Array<object>} rules
 * @returns {Record<string, object>}
 */
export function buildRuleMap(rules) {
  const map = {}
  for (const r of Array.isArray(rules) ? rules : []) {
    if (!r || !r.field_key) continue
    if (r.is_active === false) continue
    map[r.field_key] = r
  }
  return map
}
