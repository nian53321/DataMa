import request from '@/utils/request'

// 用户管理
export const getUsersApi = (params) => request.get('/system/users', { params })
export const createUserApi = (data) => request.post('/system/users', data)
export const updateUserApi = (id, data) => request.put(`/system/users/${id}`, data)
export const deleteUserApi = (id) => request.delete(`/system/users/${id}`)

// 规范管理
export const getStandardsApi = (params) => request.get('/system/standards', { params })
export const createStandardApi = (data) => request.post('/system/standards', data)
export const updateStandardApi = (id, data) => request.put(`/system/standards/${id}`, data)
export const deleteStandardApi = (id) => request.delete(`/system/standards/${id}`)

// 脱敏配置
export const getDesensConfigApi = () => request.get('/system/desensitize/config')
export const saveDesensConfigApi = (data) => request.put('/system/desensitize/config', data)

// 脱敏预览
export const desensitizePreviewApi = (data) =>
  request.post('/system/desensitize/preview', data)

// 角色菜单权限
export const getRoleMenusApi = () => request.get('/system/role-menus')
export const updateRoleMenusApi = (roleKey, data) =>
  request.put(`/system/role-menus/${roleKey}`, data)
export const getMyMenusApi = () => request.get('/system/menus')

// 角色定义 CRUD（管理员）
export const getRolesApi = (params) => request.get('/system/roles', { params })
export const createRoleApi = (data) => request.post('/system/role-menus', data)
export const updateRoleInfoApi = (roleKey, data) =>
  request.put(`/system/role-menus/${roleKey}/info`, data)
export const deleteRoleApi = (roleKey) =>
  request.delete(`/system/role-menus/${roleKey}`)

// 密钥管理
export const getEncryptionInfoApi = () =>
  request.get('/system/encryption/info')
export const rotateKeyApi = (data) =>
  request.post('/system/encryption/rotate', data)
export const verifyKeyApi = () =>
  request.post('/system/encryption/verify')
export const backupKeyApi = (data) =>
  request.post('/system/encryption/backup', data, {
    responseType: 'blob',
    skipErrorHandler: true,
  })
export const importKeyApi = (formData) =>
  request.post('/system/encryption/import', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })

// 外部密钥管理（AES-256-CBC 多组密钥）
export const getExternalKeysApi = () =>
  request.get('/system/external-keys')
export const getExternalKeyApi = (id) =>
  request.get(`/system/external-keys/${id}`, { params: { include_secret: 1 } })
export const createExternalKeyApi = (data) =>
  request.post('/system/external-keys', data)
export const updateExternalKeyApi = (id, data) =>
  request.put(`/system/external-keys/${id}`, data)
export const deleteExternalKeyApi = (id) =>
  request.delete(`/system/external-keys/${id}`)
export const importExternalKeyApi = (formData) =>
  request.post('/system/external-keys/import', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
export const verifyExternalKeyApi = (id, data) =>
  request.post(`/system/external-keys/${id}/verify`, data)
export const getEncFilesApi = (params) =>
  request.get('/system/external-keys/enc-files', { params })


// 受试者信息模板
export const getSubjectTemplateApi = () =>
  request.get('/system/subject-template')
export const saveSubjectTemplateApi = (data) =>
  request.put('/system/subject-template', data)

// 受试者文件夹自动扫描配置
export const getScanConfigsApi = () =>
  request.get('/system/scan-configs')
export const createScanConfigApi = (data) =>
  request.post('/system/scan-configs', data)
export const updateScanConfigApi = (id, data) =>
  request.put(`/system/scan-configs/${id}`, data)
export const deleteScanConfigApi = (id) =>
  request.delete(`/system/scan-configs/${id}`)
export const runScanNowApi = (id) =>
  request.post(`/system/scan-configs/${id}/run`)
