<template>
  <div class="page-container">
    <el-alert
      v-if="!isAdmin"
      type="warning"
      :closable="false"
      title="无权限访问"
      description="仅管理员可访问系统管理页面，请联系管理员授权。"
      show-icon
    />
    <template v-else>
      <div class="page-header">
        <span class="title">系统管理</span>
      </div>

      <el-tabs v-model="activeTab" type="card">
        <!-- 用户与权限 -->
        <el-tab-pane label="用户与权限" name="users">
          <el-form :inline="true" style="margin-bottom: 12px">
            <el-form-item label="关键词">
              <el-input
                v-model="userFilters.keyword"
                placeholder="用户名/姓名"
                clearable
                style="width: 180px"
                @keyup.enter="onUserSearch"
                @clear="onUserSearch"
              />
            </el-form-item>
            <el-form-item label="角色">
              <el-select v-model="userFilters.role" placeholder="全部" clearable style="width: 150px" @change="onUserSearch">
                <el-option
                  v-for="r in roleOptions"
                  :key="r.role_key"
                  :label="r.role_label + (r.is_system ? '（系统）' : '')"
                  :value="r.role_key"
                />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :icon="Search" @click="onUserSearch">查询</el-button>
              <el-button type="success" :icon="Plus" @click="openUserDialog">新增用户</el-button>
            </el-form-item>
          </el-form>
          <el-table :data="userList" border stripe v-loading="userLoading">
            <el-table-column prop="username" label="用户名" width="140" />
            <el-table-column prop="real_name" label="姓名" width="120">
              <template #default="{ row }">{{ row.real_name || '—' }}</template>
            </el-table-column>
            <el-table-column prop="role" label="角色" width="130">
              <template #default="{ row }">
                <el-tag :type="roleTagType(row.role)">{{ roleLabelOf(row.role) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="email" label="邮箱" min-width="180">
              <template #default="{ row }">{{ row.email || '—' }}</template>
            </el-table-column>
            <el-table-column prop="license_no" label="执业证号" width="160">
              <template #default="{ row }">{{ row.license_no || '—' }}</template>
            </el-table-column>
            <el-table-column prop="last_login_at" label="最后登录" width="170">
              <template #default="{ row }">{{ row.last_login_at || '从未登录' }}</template>
            </el-table-column>
            <el-table-column prop="is_active" label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">
                  {{ row.is_active ? '启用' : '禁用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="200" fixed="right">
              <template #default="{ row }">
                <el-button size="small" link type="warning" @click="openEditUserDialog(row)">编辑</el-button>
                <el-button size="small" link type="info" @click="openHistory(row, 'user', `用户 ${row.username}`)">历史</el-button>
                <el-button size="small" link type="danger" @click="removeUser(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            style="margin-top: 12px; justify-content: flex-end"
            v-model:current-page="userPagination.page"
            v-model:page-size="userPagination.page_size"
            :total="userPagination.total"
            :page-sizes="[10, 20, 50]"
            layout="total, sizes, prev, pager, next, jumper"
            @current-change="loadUsers"
            @size-change="loadUsers"
          />
        </el-tab-pane>

        <!-- 规范管理 -->
        <el-tab-pane label="规范管理" name="standards">
          <el-alert
            type="info"
            :closable="false"
            title="规范管理用于定义数据资产的命名规则、存储要求、质量阈值、安全策略等标准。命名规范会在文件上传时自动应用；其他规范作为数据治理依据存档，供团队查阅。受试者信息字段模板请在「受试者模板」标签页单独配置。"
            show-icon
            style="margin-bottom: 12px"
          />
          <div style="margin-bottom: 12px; display: flex; align-items: center; gap: 12px">
            <el-button type="primary" :icon="Plus" @click="openStandardDialog">新增规范</el-button>
            <el-select v-model="standardTypeFilter" placeholder="全部类型" clearable size="default" style="width: 160px" @change="loadStandards">
              <el-option label="命名规范" value="naming" />
              <el-option label="存储规范" value="storage" />
              <el-option label="质量规范" value="quality" />
              <el-option label="安全规范" value="security" />
              <el-option label="流程规范" value="process" />
              <el-option label="数据字典" value="dictionary" />
              <el-option label="元数据模板" value="metadata" />
            </el-select>
          </div>
          <el-table :data="standardList" border stripe v-loading="standardLoading">
            <el-table-column prop="name" label="规范名称" min-width="160" show-overflow-tooltip />
            <el-table-column prop="standard_type" label="类型" width="110">
              <template #default="{ row }">
                <el-tag size="small" :type="row.standard_type === 'naming' ? 'primary' : 'info'">
                  {{ standardTypeText(row.standard_type) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="data_type" label="适用模态" width="100">
              <template #default="{ row }">
                {{ dataTypeText(row.data_type) }}
              </template>
            </el-table-column>
            <el-table-column prop="version" label="版本" width="90" />
            <el-table-column label="规则摘要" min-width="240" show-overflow-tooltip>
              <template #default="{ row }">
                <code v-if="row.schema && row.schema.template" style="font-size: 12px; color: #409eff">
                  {{ row.schema.template }}
                </code>
                <span v-else-if="row.schema && row.schema.rules && row.schema.rules.length">
                  {{ row.schema.rules.length }} 条规则（{{ row.schema.rules.slice(0, 2).map(r => r.key || r.field).join('、') }}{{ row.schema.rules.length > 2 ? '...' : '' }}）
                </span>
                <span v-else style="color: #909399">未配置规则</span>
              </template>
            </el-table-column>
            <el-table-column prop="is_active" label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
                  {{ row.is_active ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <el-button size="small" link type="warning" @click="openEditStandardDialog(row)">编辑</el-button>
                <el-button size="small" link type="danger" @click="removeStandard(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- 脱敏配置 -->
        <el-tab-pane label="脱敏配置" name="desensitize">
          <el-alert
            type="info"
            :closable="false"
            title="配置敏感字段脱敏规则，启用后非管理员查询数据时自动应用。管理员始终查看明文。"
            style="margin-bottom: 12px"
          />
          <div style="margin-bottom: 12px; display: flex; align-items: center; gap: 16px">
            <span>脱敏总开关：</span>
            <el-switch v-model="desensConfig.enabled" />
            <span style="color: #909399; font-size: 12px">
              {{ desensConfig.enabled ? '已开启（非管理员生效）' : '已关闭（全部明文）' }}
            </span>
            <div style="flex: 1" />
            <el-button :icon="Plus" @click="addRule">新增字段规则</el-button>
            <el-button type="primary" :loading="previewLoading" @click="handlePreview">效果预览</el-button>
            <el-button type="success" :loading="desensSaving" @click="saveConfig">保存配置</el-button>
          </div>
          <el-table :data="desensConfig.rules" border v-loading="desensLoading" size="small">
            <el-table-column type="index" label="#" width="50" align="center" />
            <el-table-column label="字段标识" width="160">
              <template #default="{ row }">
                <el-input v-model="row.field_key" size="small" placeholder="如 phone" />
              </template>
            </el-table-column>
            <el-table-column label="中文名" width="120">
              <template #default="{ row }">
                <el-input v-model="row.field_label" size="small" placeholder="如 电话" />
              </template>
            </el-table-column>
            <el-table-column label="脱敏算法" width="180">
              <template #default="{ row }">
                <el-select v-model="row.algorithm" size="small" placeholder="选择算法">
                  <el-option
                    v-for="alg in algorithms"
                    :key="alg.value"
                    :label="alg.label"
                    :value="alg.value"
                  />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column label="保留前N位" width="100" align="center">
              <template #default="{ row }">
                <el-input-number
                  v-model="row.keep_head"
                  :min="0"
                  :max="50"
                  size="small"
                  controls-position="right"
                  style="width: 80px"
                />
              </template>
            </el-table-column>
            <el-table-column label="保留后N位" width="100" align="center">
              <template #default="{ row }">
                <el-input-number
                  v-model="row.keep_tail"
                  :min="0"
                  :max="50"
                  size="small"
                  controls-position="right"
                  style="width: 80px"
                />
              </template>
            </el-table-column>
            <el-table-column label="替换字符" width="80" align="center">
              <template #default="{ row }">
                <el-input v-model="row.mask_char" size="small" maxlength="2" style="width: 50px" />
              </template>
            </el-table-column>
            <el-table-column label="启用" width="80" align="center">
              <template #default="{ row }">
                <el-switch v-model="row.is_active" />
              </template>
            </el-table-column>
            <el-table-column label="操作" width="80" align="center" fixed="right">
              <template #default="{ $index }">
                <el-button size="small" link type="danger" @click="removeRule($index)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div style="margin-top: 8px; color: #909399; font-size: 12px">
            字段标识需与后端模型 to_dict() 输出的 key 一致，如 real_name/phone/email/license_no/gender/age/pseudo_id/remark 等。
          </div>
        </el-tab-pane>

        <!-- 角色权限 -->
        <el-tab-pane label="角色权限" name="permissions">
          <el-alert
            type="info"
            :closable="false"
            title="在此配置每个角色可访问的菜单。管理员始终保持全部权限且不可修改；其他角色修改后，该角色用户下次登录或刷新后生效。系统内置角色不可删除，自定义角色可在无用户使用时删除。"
            style="margin-bottom: 12px"
          />
          <div style="margin-bottom: 12px; display: flex; align-items: center; gap: 12px">
            <el-button type="success" :icon="Plus" @click="openRoleDialog">新增角色</el-button>
            <el-button :icon="Refresh" @click="loadRoleMenus">刷新</el-button>
            <span style="flex: 1" />
            <span style="color: #909399; font-size: 12px">
              共 {{ roleMenuList.length }} 个角色（其中 {{ roleMenuList.filter(r => r.is_system).length }} 个系统内置）
            </span>
          </div>
          <el-table :data="roleMenuList" border stripe v-loading="roleMenuLoading">
            <el-table-column prop="role_key" label="角色标识" width="140">
              <template #default="{ row }">
                <el-tag :type="row.is_admin ? 'danger' : (row.is_system ? 'primary' : 'success')">
                  {{ row.role_key }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="role_label" label="角色名称" width="140">
              <template #default="{ row }">
                {{ row.role_label }}
                <el-tag v-if="row.is_system" size="small" type="info" style="margin-left: 4px">系统</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="description" label="说明" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ row.description || '—' }}</template>
            </el-table-column>
            <el-table-column label="可访问菜单" min-width="280">
              <template #default="{ row }">
                <el-tag
                  v-for="mk in row.menus"
                  :key="mk"
                  size="small"
                  style="margin: 2px 4px 2px 0"
                >
                  {{ menuTitleMap[mk] || mk }}
                </el-tag>
                <span v-if="!row.menus.length" style="color: #909399">无权限</span>
              </template>
            </el-table-column>
            <el-table-column label="菜单数" width="80" align="center">
              <template #default="{ row }">{{ row.menus.length }}</template>
            </el-table-column>
            <el-table-column prop="user_count" label="用户数" width="80" align="center">
              <template #default="{ row }">{{ row.user_count ?? 0 }}</template>
            </el-table-column>
            <el-table-column label="状态" width="80" align="center">
              <template #default="{ row }">
                <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
                  {{ row.is_active ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="220" fixed="right">
              <template #default="{ row }">
                <el-button
                  size="small"
                  link
                  type="primary"
                  :disabled="row.is_admin"
                  @click="openPermDialog(row)"
                >
                  {{ row.is_admin ? '不可修改' : '配置菜单' }}
                </el-button>
                <el-button
                  size="small"
                  link
                  type="warning"
                  @click="openEditRoleDialog(row)"
                >
                  编辑信息
                </el-button>
                <el-button
                  size="small"
                  link
                  type="danger"
                  :disabled="row.is_admin || row.is_system"
                  @click="removeRole(row)"
                >
                  删除
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <!-- 密钥管理（主密钥 + 外部密钥） -->
        <el-tab-pane label="密钥管理" name="encryption">
          <div v-loading="encLoading">
            <el-alert
              type="warning"
              :closable="false"
              title="本页面集中管理数据湖两类密钥：① 主密钥（AES-256-GCM）用于数据湖内部存储加密；② 外部密钥（AES-256-CBC）用于解密外部采集设备（如 AgeCog）导出的 .enc 文件。主密钥是数据湖文件加解密的核心，请妥善保管；密钥轮换会重新加密所有文件，操作前请确保系统处于低负载时段。"
              show-icon
              style="margin-bottom: 16px"
            />

            <!-- 密钥信息卡片 -->
            <el-row :gutter="16" style="margin-bottom: 16px">
              <el-col :span="12">
                <el-card shadow="hover">
                  <template #header><span>主密钥信息</span></template>
                  <el-descriptions :column="1" border size="small">
                    <el-descriptions-item label="加密算法">{{ encInfo.key?.algorithm || '—' }}</el-descriptions-item>
                    <el-descriptions-item label="密钥长度">{{ encInfo.key?.key_size || 32 }} 字节（256 位）</el-descriptions-item>
                    <el-descriptions-item label="密钥指纹">
                      <code style="font-size: 13px; color: #409eff">{{ encInfo.key?.fingerprint || '—' }}</code>
                    </el-descriptions-item>
                    <el-descriptions-item label="创建时间">{{ encInfo.key?.created_at || '—' }}</el-descriptions-item>
                    <el-descriptions-item label="最后更新">{{ encInfo.key?.updated_at || '—' }}</el-descriptions-item>
                    <el-descriptions-item label="密钥文件路径">{{ encInfo.key?.key_path || '—' }}</el-descriptions-item>
                  </el-descriptions>
                </el-card>
              </el-col>
              <el-col :span="12">
                <el-card shadow="hover">
                  <template #header><span>加密文件统计</span></template>
                  <el-descriptions :column="1" border size="small">
                    <el-descriptions-item label="加密状态">
                      <el-tag :type="encInfo.key?.encrypt_enabled ? 'success' : 'info'" size="small">
                        {{ encInfo.key?.encrypt_enabled ? '已启用' : '未启用' }}
                      </el-tag>
                    </el-descriptions-item>
                    <el-descriptions-item label="已加密文件数">{{ encInfo.files?.encrypted_count || 0 }} 个</el-descriptions-item>
                    <el-descriptions-item label="加密文件总大小">{{ formatFileSize(encInfo.files?.total_size || 0) }}</el-descriptions-item>
                    <el-descriptions-item label="数据湖目录">{{ encInfo.data_lake_dir || '—' }}</el-descriptions-item>
                  </el-descriptions>
                </el-card>
              </el-col>
            </el-row>

            <!-- 操作按钮 -->
            <el-card shadow="never">
              <template #header><span>密钥操作</span></template>
              <el-space wrap>
                <el-button type="primary" :icon="Refresh" :loading="verifyLoading" @click="handleVerifyKey">
                  验证密钥完整性
                </el-button>
                <el-button type="success" :icon="Download" @click="openBackupDialog">
                  下载密钥备份
                </el-button>
                <el-button type="warning" :icon="Upload" @click="openImportDialog">
                  上传/替换密钥
                </el-button>
                <el-button type="danger" :icon="Key" @click="openRotateDialog">
                  轮换主密钥
                </el-button>
                <el-button :icon="RefreshRight" @click="loadEncryptionInfo">
                  刷新信息
                </el-button>
              </el-space>

              <!-- 验证结果 -->
              <el-alert
                v-if="verifyResult"
                :type="verifyResult.valid ? 'success' : 'error'"
                :closable="false"
                :title="verifyResult.message"
                show-icon
                style="margin-top: 16px"
              />
            </el-card>

            <!-- ============ 外部密钥管理（AES-256-CBC 多组密钥） ============ -->
            <el-divider content-position="left">
              <span style="font-size: 15px; font-weight: 600; color: #303133">外部密钥管理</span>
            </el-divider>

            <div v-loading="extKeyLoading">
              <el-alert
                type="info"
                :closable="false"
                title="外部密钥用于解密外部采集设备（如 AgeCog）导出的 AES-256-CBC 加密文件（.enc 后缀）。scanner 扫描时会自动尝试所有启用的外部密钥；若全部失败，文件会被跳过并提示上传对应密钥。同一来源/批次通常共用一组 key+iv。"
                show-icon
                style="margin-bottom: 16px"
              />

              <div style="margin-bottom: 12px">
                <el-button type="primary" :icon="Plus" @click="openCreateExtKeyDialog">新增密钥</el-button>
                <el-button type="success" :icon="Upload" @click="openImportExtKeyDialog">从密钥文件导入</el-button>
                <el-button :icon="RefreshRight" @click="loadExternalKeys">刷新</el-button>
              </div>

              <el-table :data="externalKeyList" border stripe>
                <el-table-column prop="name" label="密钥名称" min-width="140" show-overflow-tooltip />
                <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip>
                  <template #default="{ row }">
                    {{ row.description || '—' }}
                  </template>
                </el-table-column>
                <el-table-column label="密钥指纹" min-width="220">
                  <template #default="{ row }">
                    <code style="font-size: 12px; color: #409eff">{{ row.key_fingerprint }}</code>
                  </template>
                </el-table-column>
                <el-table-column prop="is_active" label="状态" width="80" align="center">
                  <template #default="{ row }">
                    <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
                      {{ row.is_active ? '启用' : '禁用' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="created_at" label="创建时间" width="170" />
                <el-table-column label="操作" width="240" fixed="right" align="center">
                  <template #default="{ row }">
                    <el-button link type="primary" @click="openEditExtKeyDialog(row)">编辑</el-button>
                    <el-button link type="warning" @click="openVerifyExtKeyDialog(row)">验证</el-button>
                    <el-button link type="success" @click="handleDownloadExtKey(row)">下载</el-button>
                    <el-button link type="danger" @click="handleDeleteExtKey(row)">删除</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </div>
        </el-tab-pane>

        <!-- 受试者信息模板 -->
        <el-tab-pane label="受试者模板" name="subjectTemplate">
          <div v-loading="tplLoading">
            <el-alert
              type="info"
              :closable="false"
              title="配置受试者信息采集表单的字段，支持启用/停用、必填设置、字段类型与选项编辑。停用的字段在新增/编辑受试者时不显示。"
              show-icon
              style="margin-bottom: 16px"
            />
            <div style="margin-bottom: 12px">
              <el-button type="primary" :icon="Plus" @click="addField">新增字段</el-button>
              <el-button type="success" :icon="Check" :loading="tplSaving" @click="saveTemplate">保存模板</el-button>
              <el-button @click="loadTemplate">重置</el-button>
              <el-button type="warning" :icon="View" @click="openLayoutPreview">预览布局</el-button>
            </div>
            <el-table :data="tplFields" border size="small">
              <el-table-column type="index" label="序号" width="60" align="center" />
              <el-table-column label="字段标识" width="160">
                <template #default="{ row }">
                  <el-input v-model="row.field_key" size="small" placeholder="如 phone" />
                </template>
              </el-table-column>
              <el-table-column label="中文名" width="140">
                <template #default="{ row }">
                  <el-input v-model="row.field_label" size="small" placeholder="如 联系电话" />
                </template>
              </el-table-column>
              <el-table-column label="字段类型" width="130">
                <template #default="{ row }">
                  <el-select v-model="row.field_type" size="small" style="width: 100%">
                    <el-option v-for="t in tplFieldTypes" :key="t.value" :label="t.label" :value="t.value" />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column label="占位提示" min-width="140">
                <template #default="{ row }">
                  <el-input v-model="row.placeholder" size="small" placeholder="输入提示" />
                </template>
              </el-table-column>
              <el-table-column label="下拉选项" min-width="200">
                <template #default="{ row }">
                  <template v-if="row.field_type === 'select'">
                    <div v-for="(opt, i) in row.options" :key="i" style="display: flex; gap: 4px; margin-bottom: 4px">
                      <el-input v-model="opt.label" size="small" placeholder="显示名" style="width: 50%" />
                      <el-input v-model="opt.value" size="small" placeholder="存储值" style="width: 40%" />
                      <el-button size="small" link type="danger" @click="row.options.splice(i, 1)">×</el-button>
                    </div>
                    <el-button size="small" link type="primary" @click="row.options.push({ label: '', value: '' })">+ 添加选项</el-button>
                  </template>
                  <span v-else style="color: #909399">—</span>
                </template>
              </el-table-column>
              <el-table-column label="宽度" width="110" align="center">
                <template #default="{ row }">
                  <el-select v-model="row.span" size="small" style="width: 100%">
                    <el-option :value="24" label="整行" />
                    <el-option :value="12" label="半行" />
                    <el-option :value="8" label="三分之一" />
                  </el-select>
                </template>
              </el-table-column>
              <el-table-column label="必填" width="70" align="center">
                <template #default="{ row }">
                  <el-switch v-model="row.required" size="small" />
                </template>
              </el-table-column>
              <el-table-column label="启用" width="70" align="center">
                <template #default="{ row }">
                  <el-switch v-model="row.enabled" size="small" />
                </template>
              </el-table-column>
              <el-table-column label="排序" width="90" align="center">
                <template #default="{ row, $index }">
                  <el-button-group>
                    <el-button size="small" link :disabled="$index === 0" @click="moveField($index, -1)">↑</el-button>
                    <el-button size="small" link :disabled="$index === tplFields.length - 1" @click="moveField($index, 1)">↓</el-button>
                  </el-button-group>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="80" align="center">
                <template #default="{ $index }">
                  <el-button size="small" link type="danger" @click="tplFields.splice($index, 1)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>

        <!-- 数据导出 -->
        <el-tab-pane label="数据导出" name="export">
          <el-alert
            type="info"
            :closable="false"
            title="批量导出数据资产为 ZIP 压缩包"
            description="勾选要导出的受试者、数据类型、数据层；不勾选某维度 = 该维度全选。加密导出时使用数据湖主密钥加密，下载后需用对应密钥解密。"
            show-icon
            style="margin-bottom: 16px"
          />

          <el-row :gutter="16">
            <!-- 左侧：受试者表格 -->
            <el-col :span="14">
              <el-card shadow="never">
                <template #header>
                  <div style="display: flex; justify-content: space-between; align-items: center">
                    <span>受试者（{{ exportSelectedSubjects.length }} / {{ exportFilteredSubjects.length }} 选中，共 {{ exportSubjectList.length }}）</span>
                    <el-button type="primary" plain size="small" @click="exportToggleAllSubjects">全选/反选当前列表</el-button>
                  </div>
                </template>
                <!-- 搜索筛选 -->
                <el-form :inline="true" size="small" style="margin-bottom: 8px">
                  <el-form-item label="关键词">
                    <el-input
                      v-model="exportFilter.keyword"
                      placeholder="伪ID/姓名/备注"
                      clearable
                      style="width: 160px"
                    />
                  </el-form-item>
                  <el-form-item label="性别">
                    <el-select v-model="exportFilter.gender" placeholder="全部" clearable style="width: 90px">
                      <el-option label="男" value="男" />
                      <el-option label="女" value="女" />
                    </el-select>
                  </el-form-item>
                  <el-form-item label="风险分级">
                    <el-select v-model="exportFilter.riskLevel" placeholder="全部" clearable style="width: 130px">
                      <el-option label="无" value="无" />
                      <el-option label="轻度" value="轻度" />
                      <el-option label="中度" value="中度" />
                      <el-option label="重度" value="重度" />
                      <el-option label="正常" value="normal" />
                      <el-option label="轻度认知障碍" value="mci" />
                      <el-option label="痴呆" value="dementia" />
                      <el-option label="未评估" value="none" />
                    </el-select>
                  </el-form-item>
                  <el-form-item label="批次">
                    <el-input
                      v-model="exportFilter.batch"
                      placeholder="如 BATCH_001"
                      clearable
                      style="width: 140px"
                    />
                  </el-form-item>
                </el-form>
                <el-table
                  ref="exportSubjectTableRef"
                  :data="exportFilteredSubjects"
                  row-key="id"
                  border
                  stripe
                  height="380"
                  @selection-change="onExportSelectionChange"
                  @expand-change="onExportRowExpand"
                >
                  <el-table-column type="expand" width="30">
                    <template #default="{ row }">
                      <div style="padding: 8px 12px">
                        <div v-if="subjectAssetCache[row.id]?.loading" v-loading="true" style="min-height: 60px" />
                        <el-empty
                          v-else-if="!subjectAssetCache[row.id]?.items?.length"
                          description="该受试者暂无数据资产"
                          :image-size="50"
                        />
                        <template v-else>
                          <div style="display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px">
                            <el-tag v-for="(cnt, type) in groupAssetsByType(subjectAssetCache[row.id].items)" :key="type" size="small" type="info">
                              {{ exportTypeText(type) }} × {{ cnt }}
                            </el-tag>
                          </div>
                          <el-table :data="subjectAssetCache[row.id].items" size="small" border>
                            <el-table-column label="文件名" min-width="220" show-overflow-tooltip>
                              <template #default="{ row: a }">{{ a.file_name || `资产 #${a.id}` }}</template>
                            </el-table-column>
                            <el-table-column label="类型" width="70" align="center">
                              <template #default="{ row: a }">{{ exportTypeText(a.data_type) }}</template>
                            </el-table-column>
                            <el-table-column label="数据层" width="80" align="center">
                              <template #default="{ row: a }">{{ exportLayerText(a.layer) }}</template>
                            </el-table-column>
                            <el-table-column label="大小" width="80" align="right">
                              <template #default="{ row: a }">{{ formatFileSize(a.file_size) }}</template>
                            </el-table-column>
                            <el-table-column label="采集时间" width="150" align="center" show-overflow-tooltip>
                              <template #default="{ row: a }">{{ a.timestamp_utc || '-' }}</template>
                            </el-table-column>
                          </el-table>
                        </template>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column type="selection" width="45" />
                  <el-table-column prop="pseudo_id" label="伪ID" min-width="110" show-overflow-tooltip />
                  <el-table-column prop="real_name" label="姓名" width="90" show-overflow-tooltip>
                    <template #default="{ row }">{{ row.real_name || '—' }}</template>
                  </el-table-column>
                  <el-table-column prop="age" label="年龄" width="60" align="center" />
                  <el-table-column prop="gender" label="性别" width="60" align="center" />
                  <el-table-column label="风险分级" width="100" align="center">
                    <template #default="{ row }">
                      <el-tag :type="exportRiskTagType(row.cognitive_risk_level)" size="small">
                        {{ exportRiskText(row.cognitive_risk_level) }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column prop="collection_batch" label="批次" width="100" show-overflow-tooltip />
                </el-table>
              </el-card>
            </el-col>

            <!-- 右侧：数据类型/层/加密/导出 -->
            <el-col :span="10">
              <el-card shadow="never">
                <template #header><span>导出条件</span></template>

                <div style="margin-bottom: 16px">
                  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px">
                    <span style="font-weight: 600">数据类型</span>
                    <div>
                      <el-button type="success" plain size="small" @click="exportForm.data_types = [...exportDataTypeOptions.map(o => o.value)]">全选</el-button>
                      <el-button type="info" plain size="small" @click="exportForm.data_types = []">清空</el-button>
                    </div>
                  </div>
                  <el-checkbox-group v-model="exportForm.data_types">
                    <el-checkbox
                      v-for="opt in exportDataTypeOptions"
                      :key="opt.value"
                      :label="opt.label"
                      :value="opt.value"
                      style="margin-right: 12px; margin-bottom: 4px"
                    />
                  </el-checkbox-group>
                  <div style="color: #909399; font-size: 12px; margin-top: 4px">
                    已选 {{ exportForm.data_types.length }} / {{ exportDataTypeOptions.length }}（留空=全部）
                  </div>
                </div>

                <el-divider style="margin: 8px 0" />

                <div style="margin-bottom: 16px">
                  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px">
                    <span style="font-weight: 600">数据层</span>
                    <div>
                      <el-button type="success" plain size="small" @click="exportForm.layers = [...exportLayerOptions.map(o => o.value)]">全选</el-button>
                      <el-button type="info" plain size="small" @click="exportForm.layers = []">清空</el-button>
                    </div>
                  </div>
                  <el-checkbox-group v-model="exportForm.layers">
                    <el-checkbox
                      v-for="opt in exportLayerOptions"
                      :key="opt.value"
                      :label="opt.label"
                      :value="opt.value"
                      style="margin-right: 12px; margin-bottom: 4px"
                    />
                  </el-checkbox-group>
                  <div style="color: #909399; font-size: 12px; margin-top: 4px">
                    已选 {{ exportForm.layers.length }} / {{ exportLayerOptions.length }}（留空=全部）
                  </div>
                </div>

                <el-divider style="margin: 8px 0" />

                <div style="margin-bottom: 16px">
                  <div style="display: flex; align-items: center; margin-bottom: 4px">
                    <span style="font-weight: 600; margin-right: 12px">加密导出</span>
                    <el-switch v-model="exportForm.encrypted" />
                    <span style="margin-left: 8px; color: #909399; font-size: 12px">
                      {{ exportForm.encrypted ? '主密钥加密' : '明文导出' }}
                    </span>
                  </div>
                </div>

                <el-divider style="margin: 8px 0" />

                <div style="margin-bottom: 16px">
                  <div style="display: flex; align-items: center; margin-bottom: 4px">
                    <span style="font-weight: 600; margin-right: 12px">脱敏导出</span>
                    <el-switch v-model="exportForm.desensitize.enabled" />
                    <span style="margin-left: 8px; color: #909399; font-size: 12px">
                      {{ exportForm.desensitize.enabled ? '已启用（按模态选择）' : '不脱敏（导出原始数据）' }}
                    </span>
                  </div>
                  <div v-if="exportForm.desensitize.enabled" style="margin-top: 8px">
                    <el-checkbox v-model="exportForm.desensitize.userinfo">受试者信息（userInfo）</el-checkbox>
                    <el-checkbox v-model="exportForm.desensitize.audio">音频（声纹）</el-checkbox>
                    <el-checkbox v-model="exportForm.desensitize.video">视频（人脸马赛克）</el-checkbox>
                    <el-checkbox v-model="exportForm.desensitize.eeg">脑电（EEG）</el-checkbox>
                    <el-checkbox v-model="exportForm.desensitize.ecg">心电（ECG）</el-checkbox>
                  </div>
                  <div
                    v-if="exportForm.desensitize.enabled && (exportForm.desensitize.eeg || exportForm.desensitize.ecg || exportForm.desensitize.audio || exportForm.encrypted)"
                    style="margin-top: 8px; padding: 8px 10px; border: 1px solid #e4e7ed; border-radius: 6px"
                  >
                    <el-checkbox v-model="exportForm.desensitize.restore_kit">
                      随包附带还原包（离线还原脚本 + 本包专用密钥）
                      <span style="color: #909399; font-size: 12px">— 默认勾选</span>
                    </el-checkbox>
                    <div style="color: #e6a23c; font-size: 12px; line-height: 1.6; margin-top: 4px">
                      勾选后包内多出 <b>_restore_kit/</b>：还原脚本（纯标准库，接收方解压即可运行）、
                      <b>本包专用密钥</b> 与使用说明。<b>任何拿到该包的人都能还原出原始脑电/心电/音频</b>，
                      等于把「脱敏」降级为「加扰」—— 这是<b>默认勾选</b>带来的代价，若接收方无需还原请取消勾选。
                      密钥是本次导出<b>现生成的一次性密钥</b>（非平台主密钥）：单包泄露不波及历史/其他脱敏包，
                      也无法反查 userInfo 的哈希脱敏。导出审计会记录此次「含还原密钥」。
                      <br /><b>脚本会解密包内全部 .dmec</b>（不只脑电/心电/音频）：脑电/心电/音频解密后再去脱敏，
                      得到<b>原始文件</b>；其余文件（视频/受试者信息/眼动等）只解密，得到<b>脱敏后的明文</b>
                      —— 它们的脱敏不可逆，解出来人脸仍是马赛克块、身份字段仍是掩码。
                      <template v-if="exportForm.encrypted">
                        <br />当前是<b>加密导出</b>：还会额外随包给出一把<b>本包专用主密钥</b>（master.key），
                        接收方运行脚本可<b>先解密 .dmec、再还原</b>，一步拿到原始文件；
                        平台主密钥始终不出包。解密为脚本内置实现（纯标准库，约 0.8 MB/s），
                        无需接收方安装任何密码学包。
                      </template>
                    </div>
                  </div>
                  <div v-if="exportForm.desensitize.enabled" style="color: #e6a23c; font-size: 12px; line-height: 1.6; margin-top: 6px">
                    <div v-if="exportForm.desensitize.userinfo">
                      · <b>受试者信息</b>：按「系统管理 → 脱敏配置」已启用规则替换 userInfo 内姓名/电话/性别/年龄等字段（性别/年龄在文件内为数值，脱敏后按字符串写回）
                    </div>
                    <div v-if="exportForm.desensitize.audio">
                      · <b>音频</b>：在定点 PCM 域加<b>确定性噪声</b>（按受试者 + 逻辑路径 + 声道派生，幅度 R 随录音响度自适应，SNR 约 -9.5 dB）破坏声纹（语音内容基本不可懂），<b>只改样本区</b>——RIFF 头块等其余字节一字不动，<b>采样率/声道/帧数逐样本不变</b>。非整数 PCM 的源（mp3/m4a/浮点 wav）会先转成 <b>16-bit PCM WAV</b>（文件名后缀同步改为 .wav）。仅作用于「音频」类型；<b>视频内音轨由「视频」开关处理（直接移除）</b>。凭导出时的同一密钥可<b>逐字节无损还原</b>（不再有变调带来的毫秒级时间漂移）
                    </div>
                    <div v-if="exportForm.desensitize.video">
                      · <b>视频</b>：逐帧检测人脸，<b>只对人脸区域做不可逆马赛克（像素化）</b>——把脸区降采样成粗块再放大回来，块边长 = <b>人脸短边 ÷ 8</b>（按当帧人脸大小自适应分级，脸越近块越粗），背景/身体/衣着逐像素保留。实测 4 档人脸尺度（脸短边 37 / 77 / 116 / 151 px）SFace 余弦 = <b>0.114 / 0.043 / 0.063 / 0.062</b>，全部远低于同一人判定阈值 0.363（旧版模糊档最差 0.260，已贴近阈值）；YuNet 在脱敏画面上<b>四档全部检不出人脸</b>。块内像素被整块替换，属于<b>结构性破坏</b>而非「只是更糊」：<b>脱敏后人脸区域不能再做任何下游人脸分析</b>（检测 / 关键点 / 表情 / 视线均不可用），旧版「弱模糊仍可定位大致位置」的说法已不再成立；人脸以外的发型轮廓、体型、衣着等软生物特征不在覆盖范围内。<b>音轨一律移除</b>（视频音轨含声纹，音频开关只作用于「音频」类型资产）；输出统一重编码为 <b>H.264 MP4</b>（文件名后缀同步改为 .mp4），<b>无法还原</b>——不写还原清单（包内不存在可回推参数）。加密导出时该文件仍会被随包脚本<b>解密</b>，但解出来的仍是人脸马赛克（像素化）的视频。仅支持单视频轨，含深度/红外多轨的录制会被跳过并提示
                    </div>
                    <div v-if="exportForm.desensitize.eeg">
                      · <b>脑电</b>：逐通道按信号幅度自适应加噪，破坏脑纹可识别性；<b>全列保留</b>——列名、通道数、采样点数均不变，绝对时间列（Timestamp / Board Timestamp）做<b>整列常量平移</b>（保持采样间隔与单调递增，无法定位真实采集时刻）。实测：波形相关约 0.99（时间结构仍可用），频带功率占比改变 20%~43%、通道间相干性下降 6%~52%（随原始通道间相关性而变）；凭导出时的同一密钥可<b>逐字节无损还原</b>
                    </div>
                    <div v-if="exportForm.desensitize.ecg">
                      · <b>心电</b>：value 列按 SNR 约 10 dB 加噪，<b>heart_rate 等派生生理列同样逐点加噪</b>（保留列但破坏 HRV，避免下游从脱敏波形反推真实心率）；<b>全列保留</b>——行数、列名、相对时间轴均不变，绝对时间列做<b>整列常量平移</b>；凭导出时的同一密钥可<b>逐字节无损还原</b>
                    </div>
                    <div style="margin-top: 4px; color: #909399">
                      加密导出时统一走「解密 → 脱敏 → 重新加密」；任一文件脱敏失败会被<b>跳过</b>，不会回退为原始明文。
                      <template v-if="exportForm.desensitize.eeg || exportForm.desensitize.ecg || exportForm.desensitize.audio">
                        导出包内附 <b>_desens_manifest.json</b> 还原清单（只含列参数/每声道噪声幅度，不含密钥）：
                        <template v-if="exportForm.desensitize.restore_kit">
                          已勾选还原包，接收方直接运行包内 <b>_restore_kit/restore_signal_desens.py</b> 即可逐字节还原
                          <template v-if="exportForm.encrypted">（脚本会用包内 master.key 先解密再还原）</template>
                          （脚本自动读取包内密钥）
                        </template>
                        <template v-else>
                          凭导出时相同的 DESENS_HMAC_KEY 运行 <b>back/tools/restore_signal_desens.py</b> 即可逐字节还原
                        </template>
                      </template>
                      <template v-if="exportForm.desensitize.video">
                        <br /><b>视频不产生还原清单</b>：人脸马赛克是有损变换（块内像素被整块替换，原始细节已不存在），包内不存在任何可回推原始人脸的密钥或参数，接收方无法还原内容 —— 加密导出时视频仍会被随包脚本<b>解密</b>（否则包里会残留一半密文），但解出来的仍是人脸马赛克（像素化）的视频。
                      </template>
                    </div>
                  </div>
                </div>

                <el-divider style="margin: 8px 0" />

                <!-- 导出预览：命中资产统计 -->
                <div style="margin-bottom: 16px">
                  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px">
                    <span style="font-weight: 600">导出预览</span>
                    <el-tag v-if="exportPreview.loading" size="small" type="info">统计中...</el-tag>
                    <el-button v-else size="small" text :icon="Refresh" style="padding: 0" @click="refreshExportPreview">刷新</el-button>
                  </div>
                  <div v-if="exportPreview.loaded">
                    <div style="font-size: 13px; color: #606266">
                      命中
                      <b style="color: #409eff; font-size: 16px">{{ exportPreview.totalCount }}</b>
                      个资产，共 <b>{{ formatFileSize(exportPreview.totalSize) }}</b>
                    </div>
                    <div v-if="exportPreview.totalCount === 0" style="font-size: 12px; color: #909399; margin-top: 4px">
                      当前条件下没有可导出的数据资产
                    </div>
                    <div v-else style="margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px">
                      <el-tag v-for="(count, type) in exportPreview.byType" :key="type" size="small" type="info">
                        {{ exportTypeText(type) }} × {{ count }}
                      </el-tag>
                    </div>
                  </div>
                  <div v-else-if="!exportPreview.loading" style="font-size: 12px; color: #909399">
                    预览加载失败，导出功能不受影响
                  </div>
                </div>

                <el-button
                  type="primary"
                  :icon="Download"
                  :loading="exporting"
                  style="width: 100%"
                  @click="handleExport"
                >
                  导出 ZIP（{{ exportSelectedSubjects.length || '全部' }} 受试者）
                </el-button>

                <!-- 进度条 -->
                <div v-if="exportProgress.visible" style="margin-top: 12px">
                  <div style="display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: #606266; margin-bottom: 4px">
                    <span>
                      <el-tag size="small" :type="exportProgress.phase === 'download' ? 'success' : 'warning'" style="margin-right: 6px">
                        {{ exportProgress.phase === 'download' ? '下载中' : '压缩中' }}
                      </el-tag>
                      {{ exportProgress.status }}
                    </span>
                    <span style="font-weight: 600">{{ exportProgress.percent }}%</span>
                  </div>
                  <el-progress
                    :percentage="exportProgress.percent"
                    :status="exportProgress.percent >= 100 ? 'success' : ''"
                    :stroke-width="10"
                  />
                  <div style="font-size: 12px; color: #909399; margin-top: 4px; display: flex; justify-content: space-between">
                    <span v-if="exportProgress.totalFiles">
                      文件：{{ exportProgress.processedFiles }} / {{ exportProgress.totalFiles }}
                    </span>
                    <span>
                      {{ formatFileSize(exportProgress.loaded) }}
                      <span v-if="exportProgress.total"> / {{ formatFileSize(exportProgress.total) }}</span>
                    </span>
                  </div>
                </div>
              </el-card>
            </el-col>
          </el-row>

          <!-- 命中资产明细：展示当前条件下将导出的具体数据资产 -->
          <el-card shadow="never" style="margin-top: 16px">
            <template #header>
              <div style="display: flex; justify-content: space-between; align-items: center">
                <span>
                  将导出的数据资产明细
                  <span style="color: #909399; font-size: 12px; font-weight: normal; margin-left: 6px">
                    共 {{ exportPreview.totalCount }} 条{{ exportPreview.items.length && exportPreview.items.length < exportPreview.totalCount ? `（仅显示前 ${exportPreview.items.length} 条）` : '' }}
                  </span>
                </span>
                <el-tag v-if="exportPreview.loaded && exportPreview.totalCount > 0" size="small" type="info">
                  {{ formatFileSize(exportPreview.totalSize) }}
                </el-tag>
              </div>
            </template>
            <el-table
              v-loading="exportPreview.loading"
              :data="exportPreview.items"
              border
              stripe
              size="small"
              height="280"
            >
              <el-table-column label="所属受试者" min-width="130" show-overflow-tooltip>
                <template #default="{ row }">{{ row.pseudo_id }}</template>
              </el-table-column>
              <el-table-column label="姓名" width="90" show-overflow-tooltip>
                <template #default="{ row }">{{ row.real_name || '—' }}</template>
              </el-table-column>
              <el-table-column label="文件名" min-width="240" show-overflow-tooltip>
                <template #default="{ row }">{{ row.file_name || `资产 #${row.asset_id}` }}</template>
              </el-table-column>
              <el-table-column label="类型" width="80" align="center">
                <template #default="{ row }">{{ exportTypeText(row.data_type) }}</template>
              </el-table-column>
              <el-table-column label="数据层" width="90" align="center">
                <template #default="{ row }">{{ exportLayerText(row.layer) }}</template>
              </el-table-column>
              <el-table-column label="大小" width="90" align="right">
                <template #default="{ row }">{{ formatFileSize(row.file_size) }}</template>
              </el-table-column>
              <template #empty>
                <el-empty
                  :description="exportPreview.loading ? '正在统计...' : '当前条件下没有可导出的数据资产'"
                  :image-size="60"
                />
              </template>
            </el-table>
          </el-card>
        </el-tab-pane>
      </el-tabs>

      <!-- 脱敏效果预览弹窗 -->
      <el-dialog v-model="previewDialog" title="脱敏效果预览" width="780px">
        <el-alert
          type="info"
          :closable="false"
          :title="previewEnabled ? '基于当前配置规则对样例数据做脱敏对比' : '脱敏总开关已关闭，下方展示规则定义但不会实际脱敏'"
          style="margin-bottom: 12px"
        />
        <el-table :data="previewData" border>
          <el-table-column label="字段" width="120">
            <template #default="{ row }">{{ row.field_label || row.field_key }}</template>
          </el-table-column>
          <el-table-column label="算法" width="120">
            <template #default="{ row }">{{ algorithmText(row.algorithm) }}</template>
          </el-table-column>
          <el-table-column label="脱敏前" min-width="200">
            <template #default="{ row }">
              <span style="color: #f56c6c">{{ row.before }}</span>
            </template>
          </el-table-column>
          <el-table-column label="脱敏后" min-width="200">
            <template #default="{ row }">
              <span :style="{ color: row.desensitized ? '#67c23a' : '#909399' }">
                {{ row.after }}
                <el-tag v-if="row.desensitized" size="small" type="success" style="margin-left: 4px">已处理</el-tag>
                <el-tag v-else size="small" type="info" style="margin-left: 4px">未启用</el-tag>
              </span>
            </template>
          </el-table-column>
        </el-table>
        <template #footer>
          <el-button @click="previewDialog = false">关闭</el-button>
        </template>
      </el-dialog>

      <!-- 受试者模板布局预览弹窗 -->
      <el-dialog v-model="layoutPreviewDialog" title="表单布局预览（可拖拽排序、点击切换宽度）" width="760px" class="layout-preview-dialog">
        <el-alert
          type="warning"
          :closable="false"
          title="拖拽字段卡片可调整顺序；点击右上角宽度按钮切换整行/半行/三分之一；停用字段不在此显示。"
          show-icon
          style="margin-bottom: 12px"
        />
        <div class="layout-preview-form">
          <el-row :gutter="12">
            <el-col
              v-for="(f, idx) in layoutPreviewFields"
              :key="f.field_key"
              :span="f.span || 24"
            >
              <div
                class="layout-field-card"
                :class="{ dragging: dragIdx === idx, dragOver: dragOverIdx === idx }"
                draggable="true"
                @dragstart="onDragStart(idx)"
                @dragover.prevent="onDragOver(idx)"
                @dragend="onDragEnd"
                @drop.prevent="onDrop(idx)"
              >
                <div class="layout-field-head">
                  <el-icon class="drag-handle"><Rank /></el-icon>
                  <span class="layout-field-label">{{ f.field_label || f.field_key }}</span>
                  <el-button-group class="span-toggle">
                    <el-button size="small" :type="f.span === 24 ? 'primary' : ''" @click="f.span = 24">整行</el-button>
                    <el-button size="small" :type="f.span === 12 ? 'primary' : ''" @click="f.span = 12">半行</el-button>
                    <el-button size="small" :type="f.span === 8 ? 'primary' : ''" @click="f.span = 8">1/3</el-button>
                  </el-button-group>
                </div>
                <el-form-item :label="f.field_label" :required="f.required" label-width="100px">
                  <el-input v-if="f.field_type === 'input'" :placeholder="f.placeholder" disabled />
                  <el-input-number v-else-if="f.field_type === 'number'" :placeholder="f.placeholder" disabled style="width: 100%" />
                  <el-select v-else-if="f.field_type === 'select'" :placeholder="f.placeholder" disabled style="width: 100%">
                    <el-option v-for="o in f.options" :key="o.value" :label="o.label" :value="o.value" />
                  </el-select>
                  <el-input v-else-if="f.field_type === 'textarea'" type="textarea" :rows="2" :placeholder="f.placeholder" disabled />
                  <el-date-picker v-else-if="f.field_type === 'date'" type="date" disabled style="width: 100%" />
                </el-form-item>
              </div>
            </el-col>
          </el-row>
        </div>
        <template #footer>
          <el-button @click="layoutPreviewDialog = false">取消</el-button>
          <el-button type="primary" @click="applyLayoutPreview">应用布局</el-button>
        </template>
      </el-dialog>

      <!-- 新增/编辑用户弹窗 -->
      <el-dialog v-model="dialogVisible" :title="editingUserId ? '编辑用户' : '新增用户'" width="480px">
        <el-form :model="userForm" label-width="100px">
          <el-form-item label="用户名" required>
            <el-input v-model="userForm.username" :disabled="!!editingUserId" placeholder="登录用户名" />
          </el-form-item>
          <el-form-item :label="editingUserId ? '新密码' : '密码'" :required="!editingUserId">
            <el-input
              v-model="userForm.password"
              type="password"
              show-password
              :placeholder="editingUserId ? '留空则不修改密码' : '至少 6 位'"
            />
          </el-form-item>
          <el-form-item label="姓名">
            <el-input v-model="userForm.real_name" />
          </el-form-item>
          <el-form-item label="角色" required>
            <el-select v-model="userForm.role" style="width: 100%" filterable>
              <el-option
                v-for="r in roleOptions"
                :key="r.role_key"
                :label="r.role_label + (r.is_system ? '（系统）' : '')"
                :value="r.role_key"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="邮箱">
            <el-input v-model="userForm.email" />
          </el-form-item>
          <el-form-item label="电话">
            <el-input v-model="userForm.phone" />
          </el-form-item>
          <el-form-item label="执业证号">
            <el-input v-model="userForm.license_no" placeholder="医生必填" />
          </el-form-item>
          <el-form-item label="启用状态">
            <el-switch v-model="userForm.is_active" />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="dialogVisible = false">取消</el-button>
          <el-button type="primary" :loading="userSaving" @click="handleSaveUser">确定</el-button>
        </template>
      </el-dialog>

      <!-- 规范弹窗 -->
      <el-dialog v-model="standardDialog" :title="editingStandardId ? '编辑规范' : '新增规范'" width="760px">
        <el-form :model="standardForm" label-width="110px">
          <el-form-item label="规范名称" required>
            <el-input v-model="standardForm.name" placeholder="如 视频采集命名规范" />
          </el-form-item>
          <el-form-item label="规范类型" required>
            <el-select v-model="standardForm.standard_type" style="width: 100%">
              <el-option label="命名规范 - 上传文件时自动生成规范化文件名" value="naming" />
              <el-option label="存储规范 - 定义存储路径/加密/保留策略" value="storage" />
              <el-option label="质量规范 - 定义数据完整性/有效性校验规则" value="quality" />
              <el-option label="安全规范 - 定义访问控制/脱敏/审计要求" value="security" />
              <el-option label="流程规范 - 定义采集/清洗/标注流程要求" value="process" />
              <el-option label="数据字典 - 定义字段含义与取值范围" value="dictionary" />
              <el-option label="元数据模板 - 定义数据资产元数据字段" value="metadata" />
            </el-select>
            <el-alert
              :type="standardTypeMeta[standardForm.standard_type]?.autoApply ? 'success' : 'info'"
              :closable="false"
              show-icon
              :title="standardTypeMeta[standardForm.standard_type]?.desc"
              style="margin-top: 8px"
            />
          </el-form-item>
          <el-form-item label="适用模态" required>
            <el-select v-model="standardForm.data_type" style="width: 100%">
              <el-option label="通用" value="all" />
              <el-option label="视频" value="video" />
              <el-option label="音频" value="audio" />
              <el-option label="脑电 EEG" value="eeg" />
              <el-option label="心电 ECG" value="ecg" />
              <el-option label="眼动" value="eye" />
              <el-option label="步态" value="gait" />
              <el-option label="量表" value="scale" />
              <el-option label="认知任务" value="task" />
            </el-select>
          </el-form-item>
          <el-form-item label="版本" required>
            <el-input v-model="standardForm.version" placeholder="如 v1.0.0" />
          </el-form-item>

          <!-- 命名规范专属配置 -->
          <template v-if="standardForm.standard_type === 'naming'">
            <el-divider content-position="left">命名规则配置</el-divider>
            <el-form-item label="命名模板">
              <el-input
                v-model="standardForm.schema.template"
                placeholder="{data_type}_{pseudo_id}_{timestamp}_{scene}_{batch}"
              />
              <div class="form-tip">
                可用变量：
                <el-tag
                  v-for="v in namingVariables"
                  :key="v.key"
                  size="small"
                  style="margin: 2px"
                  @click="insertNamingVar(v.key)"
                >
                  {{ '{' + v.key + '}' }}
                </el-tag>
              </div>
            </el-form-item>
            <el-form-item label="时间戳格式">
              <el-input
                v-model="standardForm.schema.timestamp_format"
                placeholder="%Y%m%d%H%M%S"
                style="width: 220px"
              />
              <span class="form-tip" style="margin-left: 8px">%Y年%m月%d日%H时%M分%S秒</span>
            </el-form-item>
            <el-form-item label="默认场景代码">
              <el-input
                v-model="standardForm.schema.defaults.scene"
                placeholder="SC（受试者无场景时使用）"
                style="width: 220px"
              />
            </el-form-item>
            <el-form-item label="默认批次号">
              <el-input
                v-model="standardForm.schema.defaults.batch"
                placeholder="B01（受试者无批次时使用）"
                style="width: 220px"
              />
            </el-form-item>
            <el-form-item label="允许扩展名">
              <el-input
                v-model="standardForm.schema.allowed_extensions_text"
                placeholder="留空=允许全部；多个用逗号分隔，如 mp4,mkv,wav"
              />
              <div class="form-tip">上传时校验文件扩展名，不符合则拒绝</div>
            </el-form-item>
            <el-form-item label="预览">
              <el-input
                :model-value="namingPreview"
                readonly
                placeholder="填写模板后显示预览效果"
              />
            </el-form-item>
          </template>

          <!-- 非命名规范：键值对规则配置 -->
          <template v-if="standardForm.standard_type !== 'naming'">
            <el-divider content-position="left">{{ standardTypeText(standardForm.standard_type) }}规则配置</el-divider>
            <div style="margin-bottom: 8px; color: #909399; font-size: 12px">
              以键值对形式定义规则条目，例如：保留天数=90天、加密算法=AES-256、采样率≥200Hz 等。
            </div>
            <el-table :data="standardForm.schema.rules" border size="small" style="margin-bottom: 8px">
              <el-table-column label="键/字段" min-width="140">
                <template #default="{ row }">
                  <el-input v-model="row.key" size="small" placeholder="如 保留天数" />
                </template>
              </el-table-column>
              <el-table-column label="值/要求" min-width="160">
                <template #default="{ row }">
                  <el-input v-model="row.value" size="small" placeholder="如 90天" />
                </template>
              </el-table-column>
              <el-table-column label="说明" min-width="180">
                <template #default="{ row }">
                  <el-input v-model="row.note" size="small" placeholder="规则说明（可选）" />
                </template>
              </el-table-column>
              <el-table-column label="操作" width="70" align="center">
                <template #default="{ $index }">
                  <el-button size="small" link type="danger" @click="standardForm.schema.rules.splice($index, 1)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-button size="small" :icon="Plus" @click="standardForm.schema.rules.push({ key: '', value: '', note: '' })">添加规则</el-button>
          </template>

          <el-form-item label="描述">
            <el-input
              v-model="standardForm.description"
              type="textarea"
              :rows="2"
              placeholder="规范说明（可选）"
            />
          </el-form-item>
          <el-form-item label="启用状态">
            <el-switch v-model="standardForm.is_active" />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="standardDialog = false">取消</el-button>
          <el-button type="primary" :loading="standardSaving" @click="handleSaveStandard">保存</el-button>
        </template>
      </el-dialog>

      <!-- 新增/编辑角色信息弹窗 -->
      <el-dialog
        v-model="roleDialog"
        :title="editingRoleKey ? '编辑角色信息' : '新增角色'"
        width="560px"
      >
        <el-form :model="roleForm" label-width="100px">
          <el-form-item label="角色标识" required>
            <el-input
              v-model="roleForm.role_key"
              :disabled="!!editingRoleKey"
              placeholder="字母/数字/下划线，2-32 位，如 reviewer"
            />
            <div class="form-tip" v-if="!editingRoleKey">
              创建后不可修改；不能使用 admin；将作为用户角色 key
            </div>
          </el-form-item>
          <el-form-item label="角色名称" required>
            <el-input v-model="roleForm.role_label" placeholder="如 复核员" />
          </el-form-item>
          <el-form-item label="说明">
            <el-input
              v-model="roleForm.description"
              type="textarea"
              :rows="2"
              placeholder="角色职责说明（可选）"
            />
          </el-form-item>
          <el-form-item label="启用状态">
            <el-switch v-model="roleForm.is_active" :disabled="editingRoleKey === 'admin'" />
            <span class="form-tip" style="margin-left: 8px">停用后不能分配给新用户</span>
          </el-form-item>
          <!-- 新增角色时同时配置菜单 -->
          <template v-if="!editingRoleKey">
            <el-divider content-position="left">菜单授权</el-divider>
            <el-form-item label="可访问菜单" required>
              <el-tree
                ref="roleTreeRef"
                :data="menuTreeData"
                show-checkbox
                node-key="key"
                default-expand-all
                :props="{ label: 'title' }"
                :default-checked-keys="roleForm.menus"
              />
            </el-form-item>
          </template>
        </el-form>
        <template #footer>
          <el-button @click="roleDialog = false">取消</el-button>
          <el-button type="primary" :loading="roleSaving" @click="handleSaveRole">保存</el-button>
        </template>
      </el-dialog>

      <!-- 配置角色菜单弹窗 -->
      <el-dialog v-model="permDialog" :title="`配置菜单权限 - ${permForm.role_label || permForm.role_key}`" width="480px">
        <el-alert
          type="warning"
          :closable="false"
          title="至少保留一个菜单。修改后该角色用户需重新登录或刷新页面生效。"
          style="margin-bottom: 12px"
        />
        <el-tree
          ref="permTreeRef"
          :data="menuTreeData"
          show-checkbox
          node-key="key"
          default-expand-all
          :props="{ label: 'title' }"
          :default-checked-keys="permForm.menus"
        />
        <template #footer>
          <el-button @click="permDialog = false">取消</el-button>
          <el-button type="primary" :loading="permSaving" @click="handlePermSubmit">保存</el-button>
        </template>
      </el-dialog>

      <!-- 历史版本弹窗 -->
      <VersionHistoryDialog
        v-model:visible="historyVisible"
        :model-type="historyModel.modelType"
        :model-id="historyModel.modelId"
        :model-label="historyModel.modelLabel"
        @rollback-success="loadUsers"
      />

      <!-- 密钥轮换确认弹窗 -->
      <el-dialog v-model="rotateDialog" title="轮换主密钥" width="480px">
        <el-alert
          type="error"
          :closable="false"
          title="高危操作"
          description="密钥轮换将生成新主密钥并重新加密数据湖中的所有文件。操作期间文件不可访问，请确保无用户正在使用系统。"
          show-icon
          style="margin-bottom: 16px"
        />
        <el-alert
          type="info"
          :closable="false"
          :title="`当前将重新加密 ${encInfo.files?.encrypted_count || 0} 个文件（约 ${formatFileSize(encInfo.files?.total_size || 0)}）`"
          style="margin-bottom: 16px"
        />
        <el-form label-width="120px">
          <el-form-item label="当前密钥指纹">
            <code style="font-size: 12px; color: #909399">{{ encInfo.key?.fingerprint || '—' }}</code>
          </el-form-item>
          <el-form-item label="管理员密码" required>
            <el-input v-model="rotateForm.password" type="password" placeholder="请输入管理员密码以确认操作" show-password />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="rotateDialog = false">取消</el-button>
          <el-button type="danger" :loading="rotateLoading" @click="handleRotateKey">
            确认轮换密钥
          </el-button>
        </template>
      </el-dialog>

      <!-- 下载密钥备份确认弹窗 -->
      <el-dialog v-model="backupDialog" title="下载密钥备份" width="480px">
        <el-alert
          type="warning"
          :closable="false"
          title="敏感操作"
          description="主密钥备份文件是解密所有数据湖文件的唯一凭证，一旦泄露将导致全部数据暴露。请确认环境安全，下载后妥善离线保管。"
          show-icon
          style="margin-bottom: 16px"
        />
        <el-form label-width="120px">
          <el-form-item label="当前密钥指纹">
            <code style="font-size: 12px; color: #909399">{{ encInfo.key?.fingerprint || '—' }}</code>
          </el-form-item>
          <el-form-item label="管理员密码" required>
            <el-input v-model="backupForm.password" type="password" placeholder="请输入管理员密码以确认操作" show-password />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="backupDialog = false">取消</el-button>
          <el-button type="success" :loading="backupLoading" @click="handleBackupKey">
            确认下载
          </el-button>
        </template>
      </el-dialog>

      <!-- 上传/替换密钥弹窗 -->
      <el-dialog v-model="importDialog" title="上传/替换主密钥" width="520px">
        <el-alert
          type="error"
          :closable="false"
          title="高危操作"
          description="上传新密钥后将用新密钥重新加密数据湖中的所有文件。请确保密钥文件来源可信，错误的密钥将导致数据无法恢复。"
          show-icon
          style="margin-bottom: 16px"
        />
        <el-alert
          type="info"
          :closable="false"
          :title="`当前将重新加密 ${encInfo.files?.encrypted_count || 0} 个文件（约 ${formatFileSize(encInfo.files?.total_size || 0)}）`"
          style="margin-bottom: 16px"
        />
        <el-form label-width="120px">
          <el-form-item label="当前密钥指纹">
            <code style="font-size: 12px; color: #909399">{{ encInfo.key?.fingerprint || '—' }}</code>
          </el-form-item>
          <el-form-item label="密钥文件" required>
            <el-upload
              :auto-upload="false"
              :limit="1"
              :on-change="handleImportFileChange"
              :on-remove="() => importForm.file = null"
              :file-list="importFileList"
              accept=".key,.bin"
            >
              <el-button type="primary" plain :icon="Upload">选择密钥文件</el-button>
              <template #tip>
                <div class="el-upload__tip">请选择之前下载的 .key 备份文件（32 字节二进制）</div>
              </template>
            </el-upload>
          </el-form-item>
          <el-form-item label="管理员密码" required>
            <el-input v-model="importForm.password" type="password" placeholder="请输入管理员密码以确认操作" show-password />
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="importDialog = false">取消</el-button>
          <el-button type="warning" :loading="importLoading" @click="handleImportKey">
            确认替换密钥
          </el-button>
        </template>
      </el-dialog>

      <!-- 新增/编辑外部密钥 -->
      <el-dialog
        v-model="extKeyDialog"
        :title="extKeyForm.id ? '编辑外部密钥' : '新增外部密钥'"
        width="560px"
      >
        <el-form :model="extKeyForm" label-width="100px">
          <el-form-item label="密钥名称" required>
            <el-input v-model="extKeyForm.name" placeholder="如 AgeCog-2026Q1，便于识别" maxlength="64" show-word-limit />
          </el-form-item>
          <el-form-item label="描述">
            <el-input v-model="extKeyForm.description" placeholder="来源/批次/用途（可选）" maxlength="256" />
          </el-form-item>
          <el-form-item label="AES Key" required>
            <el-input v-model="extKeyForm.key_b64" placeholder="Base64 编码的 32 字节 key" />
            <div style="font-size: 12px; color: #909399; margin-top: 4px">Base64 编码后必须解码为 32 字节</div>
          </el-form-item>
          <el-form-item label="AES IV" required>
            <el-input v-model="extKeyForm.iv_b64" placeholder="Base64 编码的 16 字节 IV" />
            <div style="font-size: 12px; color: #909399; margin-top: 4px">Base64 编码后必须解码为 16 字节</div>
          </el-form-item>
          <el-form-item label="启用状态">
            <el-switch v-model="extKeyForm.is_active" />
            <span style="margin-left: 8px; font-size: 12px; color: #909399">
              {{ extKeyForm.is_active ? '启用（参与 scanner 自动解密）' : '禁用（保留记录但不参与解密）' }}
            </span>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="extKeyDialog = false">取消</el-button>
          <el-button type="primary" :loading="extKeySaving" @click="handleSaveExtKey">
            {{ extKeyForm.id ? '保存修改' : '创建密钥' }}
          </el-button>
        </template>
      </el-dialog>

      <!-- 从密钥文件导入 -->
      <el-dialog v-model="extKeyImportDialog" title="从密钥.txt 文件导入" width="520px">
        <el-alert
          type="info"
          :closable="false"
          title="密钥.txt 文件格式：每行一个 KEY=VALUE，需包含 CRYPTO_AES_KEY（Base64 32字节）和 CRYPTO_AES_IV（Base64 16字节）"
          show-icon
          style="margin-bottom: 16px"
        />
        <el-form label-width="100px">
          <el-form-item label="密钥名称" required>
            <el-input v-model="extKeyImportForm.name" placeholder="如 AgeCog-2026Q1" maxlength="64" />
          </el-form-item>
          <el-form-item label="描述">
            <el-input v-model="extKeyImportForm.description" placeholder="来源/批次（可选）" maxlength="256" />
          </el-form-item>
          <el-form-item label="密钥文件" required>
            <el-upload
              :auto-upload="false"
              :limit="1"
              :on-change="handleExtKeyFileChange"
              :on-remove="handleExtKeyFileRemove"
            >
              <el-button :icon="Upload">选择密钥.txt 文件</el-button>
            </el-upload>
          </el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="extKeyImportDialog = false">取消</el-button>
          <el-button type="success" :loading="extKeyImporting" @click="handleImportExtKey">
            确认导入
          </el-button>
        </template>
      </el-dialog>

      <!-- 验证外部密钥 -->
      <el-dialog v-model="extKeyVerifyDialog" title="验证外部密钥" width="560px">
        <el-alert
          type="info"
          :closable="false"
          :title="`使用密钥「${extKeyVerifyForm.keyName}」尝试解密指定 .enc 文件`"
          show-icon
          style="margin-bottom: 16px"
        />
        <el-form label-width="100px">
          <el-form-item label="文件路径" required>
            <div style="display: flex; gap: 8px; width: 100%">
              <el-input
                v-model="extKeyVerifyForm.file_path"
                placeholder="绝对路径或相对 DATA_LAKE_DIR 的路径"
                style="flex: 1"
              />
              <el-button :icon="FolderOpened" @click="openEncFileDialog">选择文件</el-button>
            </div>
            <div style="font-size: 12px; color: #909399; margin-top: 4px">
              可点击「选择文件」从数据湖中选取 .enc 文件，也可手动输入绝对路径或相对路径（如 raw/PSEUDO_ID/eeg_xxx.csv.enc）
            </div>
          </el-form-item>
        </el-form>
        <el-alert
          v-if="extKeyVerifyResult"
          :type="extKeyVerifyResult.valid ? 'success' : 'error'"
          :closable="false"
          :title="extKeyVerifyResult.message"
          show-icon
          style="margin-top: 12px"
        />
        <template #footer>
          <el-button @click="extKeyVerifyDialog = false">关闭</el-button>
          <el-button type="warning" :loading="extKeyVerifying" @click="handleVerifyExtKey">
            验证
          </el-button>
        </template>
      </el-dialog>

      <!-- .enc 文件选择对话框（验证密钥辅助） -->
      <el-dialog
        v-model="encFileDialog"
        title="选择 .enc 文件"
        width="720px"
        append-to-body
        destroy-on-close
      >
        <div style="margin-bottom: 12px; display: flex; gap: 8px; align-items: center">
          <el-input
            v-model="encFileKeyword"
            placeholder="按路径关键词过滤（如 pseudo_id、eeg）"
            clearable
            style="flex: 1"
            @keyup.enter="loadEncFiles"
            @clear="loadEncFiles"
          />
          <el-button type="primary" :icon="Search" @click="loadEncFiles">搜索</el-button>
          <el-button :icon="RefreshRight" @click="loadEncFiles">刷新</el-button>
        </div>
        <el-table
          :data="encFileList"
          border
          stripe
          height="380"
          v-loading="encFileLoading"
          highlight-current-row
          @current-change="onEncFileCurrentChange"
          empty-text="数据湖中暂无 .enc 文件"
        >
          <el-table-column label="文件路径（相对 DATA_LAKE_DIR）" min-width="380" show-overflow-tooltip>
            <template #default="{ row }">
              <code style="font-size: 12px; color: #409eff">{{ row.path }}</code>
            </template>
          </el-table-column>
          <el-table-column label="大小" width="100" align="right">
            <template #default="{ row }">{{ formatFileSize(row.size) }}</template>
          </el-table-column>
          <el-table-column label="修改时间" width="170" align="center">
            <template #default="{ row }">{{ formatEncFileTime(row.modified) }}</template>
          </el-table-column>
        </el-table>
        <template #footer>
          <el-button @click="encFileDialog = false">取消</el-button>
          <el-button
            type="primary"
            :disabled="!encFileCurrent"
            @click="confirmEncFile"
          >
            确定
          </el-button>
        </template>
      </el-dialog>
    </template>
  </div>
</template>


<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { Plus, Document, Search, Refresh, Download, Upload, Key, RefreshRight, Check, View, Rank, FolderOpened } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute } from 'vue-router'
import { useUserStore } from '@/stores/user'
import {
  getUsersApi,
  createUserApi,
  updateUserApi,
  deleteUserApi,
  getStandardsApi,
  createStandardApi,
  updateStandardApi,
  deleteStandardApi,
  getDesensConfigApi,
  saveDesensConfigApi,
  desensitizePreviewApi,
  getRoleMenusApi,
  updateRoleMenusApi,
  getRolesApi,
  createRoleApi,
  updateRoleInfoApi,
  deleteRoleApi,
  getEncryptionInfoApi,
  rotateKeyApi,
  verifyKeyApi,
  backupKeyApi,
  importKeyApi,
  getExternalKeysApi,
  getExternalKeyApi,
  createExternalKeyApi,
  updateExternalKeyApi,
  deleteExternalKeyApi,
  importExternalKeyApi,
  verifyExternalKeyApi,
  downloadExternalKeyApi,
  getEncFilesApi,
  getSubjectTemplateApi,
  saveSubjectTemplateApi,
} from '@/api/system'
import { getSubjectsApi, getAssetsApi, exportStartApi, exportProgressApi, exportDownloadApi, exportPreviewApi } from '@/api/data'
import { fetchAllPages } from '@/utils/fetchAll'
import { fetchSignedUrlApi } from '@/api/media'
import VersionHistoryDialog from '@/components/VersionHistoryDialog.vue'

const userStore = useUserStore()
const route = useRoute()
const isAdmin = computed(() => userStore.role === 'admin')

// 历史版本弹窗
const historyVisible = ref(false)
const historyModel = reactive({ modelType: '', modelId: null, modelLabel: '' })
const openHistory = (row, modelType, label) => {
  historyModel.modelType = modelType
  historyModel.modelId = row.id
  historyModel.modelLabel = label
  historyVisible.value = true
}

const activeTab = ref('users')
const userList = ref([])
const standardList = ref([])
const userLoading = ref(false)
const standardLoading = ref(false)
const userSaving = ref(false)
const standardSaving = ref(false)
const userFilters = reactive({ keyword: '', role: '' })
const userPagination = reactive({ page: 1, page_size: 20, total: 0 })
const onUserSearch = () => {
  userPagination.page = 1
  loadUsers()
}

const dialogVisible = ref(false)
const editingUserId = ref(null)

const defaultUserForm = () => ({
  username: '',
  password: '',
  real_name: '',
  role: 'annotator',
  email: '',
  phone: '',
  license_no: '',
  is_active: true,
})

const userForm = reactive(defaultUserForm())

const resetUserForm = () => {
  Object.assign(userForm, defaultUserForm())
}

// 规范弹窗
const standardDialog = ref(false)
const editingStandardId = ref(null)
const standardTypeFilter = ref('')

// 各规范类型的说明与是否自动应用
const standardTypeMeta = {
  naming: { desc: '命名规范：上传文件时自动按模板生成规范化文件名，并校验扩展名。系统会在文件上传环节自动调用。', autoApply: true },
  storage: { desc: '存储规范：定义数据存储路径、加密方式、保留天数等策略。作为团队数据存储的统一约定。', autoApply: false },
  quality: { desc: '质量规范：定义数据完整性、有效性、采样率等校验规则。用于数据质量评估参考。', autoApply: false },
  security: { desc: '安全规范：定义访问控制、脱敏要求、审计策略。作为数据安全治理依据。', autoApply: false },
  process: { desc: '流程规范：定义采集、清洗、标注各环节的操作流程要求。供团队协作参考。', autoApply: false },
  dictionary: { desc: '数据字典：定义字段含义、取值范围、编码规则。用于统一字段口径。', autoApply: false },
  metadata: { desc: '元数据模板：定义数据资产应包含的元数据字段。作为元数据采集规范。', autoApply: false },
}

const defaultStandardForm = () => ({
  name: '',
  standard_type: 'naming',
  data_type: 'all',
  version: '1.0.0',
  is_active: true,
  description: '',
  schema: {
    template: '{data_type}_{pseudo_id}_{timestamp}_{scene}_{batch}',
    timestamp_format: '%Y%m%d%H%M%S',
    defaults: { scene: 'SC', batch: 'B01' },
    allowed_extensions_text: '',
    rules: [],
  },
})
const standardForm = reactive(defaultStandardForm())

// 命名规范变量说明
const namingVariables = [
  { key: 'data_type', desc: '模态类型' },
  { key: 'pseudo_id', desc: '受试者伪ID' },
  { key: 'timestamp', desc: '北京时间戳' },
  { key: 'date', desc: '日期 YYYYMMDD' },
  { key: 'scene', desc: '采集场景' },
  { key: 'batch', desc: '采集批次' },
  { key: 'seq', desc: '序号(3位)' },
  { key: 'original', desc: '原始文件名' },
  { key: 'video_type', desc: '视频子类型(face/body/gait，仅视频)' },
  { key: 'scale_type', desc: '量表类型(moca/mmse/ad8，仅量表，未引用时自动追加)' },
]

// 插入命名变量到模板
const insertNamingVar = (key) => {
  const tag = `{${key}}`
  standardForm.schema.template = (standardForm.schema.template || '') + tag
}

// 命名预览
const namingPreview = computed(() => {
  const tpl = standardForm.schema.template
  if (!tpl) return ''
  const now = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  const ts = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  const vars = {
    data_type: standardForm.data_type === 'all' ? 'video' : (standardForm.data_type || 'video'),
    pseudo_id: 'SUBJ001',
    timestamp: ts,
    date: ts.slice(0, 8),
    scene: standardForm.schema.defaults?.scene || 'SC',
    batch: standardForm.schema.defaults?.batch || 'B01',
    seq: '001',
    original: '原始文件名',
    video_type: 'face',
    scale_type: 'moca',
  }
  try {
    return tpl.replace(/\{(\w+)\}/g, (_, k) => vars[k] || `{${k}}`) + '.mp4'
  } catch {
    return ''
  }
})

const standardTypeText = (t) => ({
  naming: '命名规范',
  storage: '存储规范',
  quality: '质量规范',
  security: '安全规范',
  process: '流程规范',
  dictionary: '数据字典',
  metadata: '元数据模板',
}[t] || t || '—')

const dataTypeText = (t) => ({
  all: '通用', video: '视频', audio: '音频', eeg: '脑电', ecg: '心电',
  eye: '眼动', gait: '步态', scale: '量表', task: '认知任务',
}[t] || t || '—')

const desensConfig = reactive({
  enabled: true,
  rules: [],
})
const desensLoading = ref(false)
const desensSaving = ref(false)
const algorithms = ref([
  { value: 'mask_all', label: '全部替换' },
  { value: 'mask_middle', label: '保留首尾(中间替换)' },
  { value: 'mask_head', label: '保留前N位(尾部替换)' },
  { value: 'mask_tail', label: '保留后N位(头部替换)' },
  { value: 'mask_email', label: '邮箱专用' },
  { value: 'hash', label: '哈希(SHA256前8位)' },
  { value: 'redact', label: '完全抹除([REDACTED])' },
])
const algorithmText = (a) => algorithms.value.find((x) => x.value === a)?.label || a || '—'

const addRule = () => {
  desensConfig.rules.push({
    field_key: '',
    field_label: '',
    algorithm: 'mask_middle',
    keep_head: 0,
    keep_tail: 0,
    mask_char: '*',
    is_active: true,
    sort_order: desensConfig.rules.length + 1,
  })
}
const removeRule = (idx) => {
  desensConfig.rules.splice(idx, 1)
}

const loadDesensConfig = async () => {
  desensLoading.value = true
  try {
    const res = await getDesensConfigApi()
    desensConfig.enabled = res.data?.enabled ?? true
    desensConfig.rules = res.data?.rules || []
    if (res.data?.algorithms) algorithms.value = res.data.algorithms
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    desensLoading.value = false
  }
}

const saveConfig = async () => {
  // 前端基础校验
  const seen = new Set()
  for (const r of desensConfig.rules) {
    const key = (r.field_key || '').trim()
    if (!key) {
      ElMessage.warning('字段标识不能为空')
      return
    }
    if (seen.has(key)) {
      ElMessage.warning(`字段标识重复：${key}`)
      return
    }
    seen.add(key)
    if (!(r.field_label || '').trim()) {
      ElMessage.warning(`字段【${key}】中文名不能为空`)
      return
    }
  }
  desensSaving.value = true
  try {
    const payload = {
      enabled: desensConfig.enabled,
      rules: desensConfig.rules.map((r, i) => ({
        field_key: (r.field_key || '').trim(),
        field_label: (r.field_label || '').trim(),
        algorithm: r.algorithm,
        keep_head: Number(r.keep_head) || 0,
        keep_tail: Number(r.keep_tail) || 0,
        mask_char: r.mask_char || '*',
        is_active: !!r.is_active,
        sort_order: i + 1,
      })),
    }
    await saveDesensConfigApi(payload)
    ElMessage.success('脱敏配置已保存')
    loadDesensConfig()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    desensSaving.value = false
  }
}

// 脱敏预览
const previewDialog = ref(false)
const previewLoading = ref(false)
const previewData = ref([])
const previewEnabled = ref(true)

const loadUsers = async () => {
  userLoading.value = true
  try {
    const res = await getUsersApi({
      page: userPagination.page,
      page_size: userPagination.page_size,
      keyword: userFilters.keyword,
      role: userFilters.role,
    })
    userList.value = res.data.items || []
    userPagination.total = res.data.total || 0
  } catch (e) {
    userList.value = []
  } finally {
    userLoading.value = false
  }
}

const loadStandards = async () => {
  standardLoading.value = true
  try {
    const params = {}
    if (standardTypeFilter.value) params.type = standardTypeFilter.value
    const res = await getStandardsApi(params)
    standardList.value = res.data || []
  } catch (e) {
    standardList.value = []
  } finally {
    standardLoading.value = false
  }
}

// 新增/编辑用户
const openUserDialog = () => {
  editingUserId.value = null
  resetUserForm()
  dialogVisible.value = true
}

const openEditUserDialog = (row) => {
  editingUserId.value = row.id
  Object.assign(userForm, {
    username: row.username || '',
    password: '',  // 编辑时密码留空，表示不修改
    real_name: row.real_name || '',
    role: row.role || 'annotator',
    email: row.email || '',
    phone: row.phone || '',
    license_no: row.license_no || '',
    is_active: row.is_active ?? true,
  })
  dialogVisible.value = true
}

const handleSaveUser = async () => {
  if (!userForm.username) {
    ElMessage.warning('请填写用户名')
    return
  }
  if (!userForm.role) {
    ElMessage.warning('请选择角色')
    return
  }
  // 新增时密码必填，编辑时密码留空表示不修改
  if (!editingUserId.value) {
    if (!userForm.password) {
      ElMessage.warning('请填写密码')
      return
    }
    if (userForm.password.length < 6) {
      ElMessage.warning('密码长度不能少于 6 位')
      return
    }
  } else if (userForm.password && userForm.password.length < 6) {
    ElMessage.warning('新密码长度不能少于 6 位')
    return
  }
  userSaving.value = true
  try {
    if (editingUserId.value) {
      // 编辑：仅传需要更新的字段，密码为空则不传
      const payload = { ...userForm }
      delete payload.username
      if (!payload.password) delete payload.password
      await updateUserApi(editingUserId.value, payload)
      ElMessage.success('用户信息已更新')
    } else {
      // 新增：传完整密码
      await createUserApi({ ...userForm })
      ElMessage.success('创建成功')
    }
    dialogVisible.value = false
    resetUserForm()
    loadUsers()
  } catch (e) {
    /* 错误已处理 */
  } finally {
    userSaving.value = false
  }
}

const removeUser = (row) => {
  ElMessageBox.confirm(
    `确认删除用户「${row.username}」？该操作不可恢复。`,
    '危险操作',
    { type: 'warning', confirmButtonText: '确认删除', confirmButtonClass: 'el-button--danger' }
  ).then(async () => {
    try {
      await deleteUserApi(row.id)
      ElMessage.success('用户已删除')
      loadUsers()
    } catch (e) { /* 拦截器已提示 */ }
  }).catch(() => {})
}

// 规范弹窗
const openStandardDialog = () => {
  editingStandardId.value = null
  Object.assign(standardForm, defaultStandardForm())
  // 深拷贝 schema 避免引用污染
  standardForm.schema = {
    template: '{data_type}_{pseudo_id}_{timestamp}_{scene}_{batch}',
    timestamp_format: '%Y%m%d%H%M%S',
    defaults: { scene: 'SC', batch: 'B01' },
    allowed_extensions_text: '',
    rules: [],
  }
  standardDialog.value = true
}

const openEditStandardDialog = (row) => {
  editingStandardId.value = row.id
  const schema = row.schema || {}
  const extArr = Array.isArray(schema.allowed_extensions) ? schema.allowed_extensions : []
  const rules = Array.isArray(schema.rules) ? schema.rules.map(r => ({ ...r })) : []
  Object.assign(standardForm, {
    name: row.name || '',
    standard_type: row.standard_type || 'naming',
    data_type: row.data_type || 'all',
    version: row.version || '1.0.0',
    is_active: row.is_active ?? true,
    description: row.description || '',
    schema: {
      template: schema.template || '{data_type}_{pseudo_id}_{timestamp}_{scene}_{batch}',
      timestamp_format: schema.timestamp_format || '%Y%m%d%H%M%S',
      defaults: {
        scene: schema.defaults?.scene || 'SC',
        batch: schema.defaults?.batch || 'B01',
      },
      allowed_extensions_text: extArr.join(','),
      rules,
    },
  })
  standardDialog.value = true
}

const handleSaveStandard = async () => {
  if (!standardForm.name) {
    ElMessage.warning('请填写规范名称')
    return
  }
  if (!standardForm.version) {
    ElMessage.warning('请填写版本')
    return
  }
  // 构造 schema：命名规范才传 schema
  const payload = {
    name: standardForm.name,
    standard_type: standardForm.standard_type,
    data_type: standardForm.data_type,
    version: standardForm.version,
    is_active: standardForm.is_active,
    description: standardForm.description,
  }
  if (standardForm.standard_type === 'naming') {
    const extText = (standardForm.schema.allowed_extensions_text || '').trim()
    const allowed_extensions = extText
      ? extText.split(',').map((s) => s.trim().toLowerCase().replace(/^\./, '')).filter(Boolean)
      : []
    payload.schema = {
      template: standardForm.schema.template,
      timestamp_format: standardForm.schema.timestamp_format,
      defaults: standardForm.schema.defaults,
      allowed_extensions,
    }
  } else {
    // 非命名规范：保存键值对规则
    payload.schema = {
      rules: (standardForm.schema.rules || [])
        .filter(r => (r.key || '').trim() || (r.value || '').trim())
        .map(r => ({ key: (r.key || '').trim(), value: (r.value || '').trim(), note: r.note || '' })),
    }
  }
  standardSaving.value = true
  try {
    if (editingStandardId.value) {
      await updateStandardApi(editingStandardId.value, payload)
      ElMessage.success('规范已更新')
    } else {
      await createStandardApi(payload)
      ElMessage.success('规范创建成功')
    }
    standardDialog.value = false
    loadStandards()
  } catch (e) {
    /* 错误已处理 */
  } finally {
    standardSaving.value = false
  }
}

const removeStandard = async (row) => {
  try {
    await ElMessageBox.confirm(
      `确认删除规范「${row.name}」？该操作不可恢复。`,
      '危险操作',
      { type: 'warning', confirmButtonText: '确认删除', confirmButtonClass: 'el-button--danger' },
    )
    await deleteStandardApi(row.id)
    ElMessage.success('规范已删除')
    loadStandards()
  } catch (e) { /* 拦截器已提示 */ }
}

const handlePreview = async () => {
  previewLoading.value = true
  try {
    const res = await desensitizePreviewApi({})
    const before = res.data.before || []
    const after = res.data.after || []
    previewEnabled.value = !!res.data.enabled
    previewData.value = before.map((b, i) => ({
      field_key: b.field_key,
      field_label: after[i]?.field_label || b.field_key,
      algorithm: after[i]?.algorithm,
      before: b.value,
      after: after[i]?.value ?? b.value,
      desensitized: after[i]?.desensitized ?? false,
    }))
    previewDialog.value = true
  } finally {
    previewLoading.value = false
  }
}

// ===== 角色标签样式（用户与权限 Tab 使用） =====
const roleTagType = (r) => ({
  admin: 'danger', doctor: 'warning', annotator: 'primary',
  nurse: 'info', engineer: 'success',
}[r] || 'info')

// ===== 角色权限 =====
const roleMenuList = ref([])
const roleMenuLoading = ref(false)
const menuTreeData = ref([])
const menuTitleMap = computed(() => {
  const m = {}
  menuTreeData.value.forEach((item) => { m[item.key] = item.title })
  return m
})
const permDialog = ref(false)
const permSaving = ref(false)
const permTreeRef = ref()
const permForm = reactive({ role_key: '', role_label: '', menus: [] })

const loadRoleMenus = async () => {
  roleMenuLoading.value = true
  try {
    const res = await getRoleMenusApi()
    roleMenuList.value = res.data.roles || []
    menuTreeData.value = (res.data.menu_definitions || []).map((m) => ({
      key: m.key,
      title: m.title,
    }))
  } catch (e) {
    roleMenuList.value = []
  } finally {
    roleMenuLoading.value = false
  }
}

const openPermDialog = (row) => {
  permForm.role_key = row.role_key
  permForm.role_label = row.role_label
  permForm.menus = [...row.menus]
  permDialog.value = true
  // el-tree 重新渲染后设置勾选
  nextTick(() => {
    permTreeRef.value?.setCheckedKeys(permForm.menus)
  })
}

const handlePermSubmit = async () => {
  const checked = permTreeRef.value?.getCheckedKeys() || []
  if (!checked.length) {
    ElMessage.warning('至少保留一个菜单')
    return
  }
  permSaving.value = true
  try {
    await updateRoleMenusApi(permForm.role_key, { menus: checked })
    ElMessage.success('权限已更新，该角色用户需重新登录或刷新页面生效')
    permDialog.value = false
    loadRoleMenus()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    permSaving.value = false
  }
}

// ===== 角色 CRUD（新增/编辑/删除）=====
const roleDialog = ref(false)
const roleSaving = ref(false)
const editingRoleKey = ref(null)
const roleTreeRef = ref()
const defaultRoleForm = () => ({
  role_key: '',
  role_label: '',
  description: '',
  is_active: true,
  menus: ['dashboard'],  // 默认勾选工作台
})
const roleForm = reactive(defaultRoleForm())

// 系统内置角色标签映射（与后端 ROLE_LABELS 对齐，用作下拉选项兜底）
const systemRoleMap = {
  admin: '管理员',
  doctor: '医生',
  annotator: '标注员',
  nurse: '护理人员',
  engineer: '数据工程师',
}

// 当前可用角色列表（供用户筛选/弹窗下拉使用，动态加载）
const roleOptions = ref([])
const loadRoleOptions = async () => {
  try {
    const res = await getRolesApi()
    roleOptions.value = (res.data || []).map((r) => ({
      role_key: r.role_key,
      role_label: r.role_label,
      is_system: r.is_system,
    }))
  } catch (e) {
    roleOptions.value = []
  }
}

// 角色中文名兜底：先查 roleOptions，再查系统内置
const roleLabelOf = (key) => {
  const opt = roleOptions.value.find((r) => r.role_key === key)
  if (opt) return opt.role_label
  return systemRoleMap[key] || key
}

const openRoleDialog = () => {
  editingRoleKey.value = null
  Object.assign(roleForm, defaultRoleForm())
  roleDialog.value = true
  nextTick(() => {
    roleTreeRef.value?.setCheckedKeys(roleForm.menus)
  })
}

const openEditRoleDialog = (row) => {
  editingRoleKey.value = row.role_key
  Object.assign(roleForm, {
    role_key: row.role_key,
    role_label: row.role_label,
    description: row.description || '',
    is_active: row.is_active ?? true,
    menus: [...(row.menus || [])],
  })
  roleDialog.value = true
}

const handleSaveRole = async () => {
  // 基础校验
  const key = (roleForm.role_key || '').trim()
  const label = (roleForm.role_label || '').trim()
  if (!editingRoleKey.value) {
    if (!key) {
      ElMessage.warning('请填写角色标识')
      return
    }
    if (!/^[a-zA-Z0-9_]{2,32}$/.test(key)) {
      ElMessage.warning('角色标识仅允许字母数字下划线，长度 2-32')
      return
    }
    if (key.toLowerCase() === 'admin') {
      ElMessage.warning('admin 为系统保留角色标识')
      return
    }
  }
  if (!label) {
    ElMessage.warning('请填写角色名称')
    return
  }
  roleSaving.value = true
  try {
    if (editingRoleKey.value) {
      // 编辑：仅更新 label/description/is_active
      await updateRoleInfoApi(editingRoleKey.value, {
        role_label: label,
        description: roleForm.description,
        is_active: roleForm.is_active,
      })
      ElMessage.success('角色信息已更新')
    } else {
      // 新增：role_key + label + description + is_active + menus
      const checked = roleTreeRef.value?.getCheckedKeys() || []
      if (!checked.length) {
        ElMessage.warning('至少勾选一个菜单')
        roleSaving.value = false
        return
      }
      await createRoleApi({
        role_key: key.toLowerCase(),
        role_label: label,
        description: roleForm.description,
        is_active: roleForm.is_active,
        menus: checked,
      })
      ElMessage.success('角色创建成功')
    }
    roleDialog.value = false
    loadRoleMenus()
    loadRoleOptions()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    roleSaving.value = false
  }
}

const removeRole = (row) => {
  ElMessageBox.confirm(
    `确认删除角色「${row.role_label}（${row.role_key}）」？该操作不可恢复。`,
    '危险操作',
    { type: 'warning', confirmButtonText: '确认删除', confirmButtonClass: 'el-button--danger' },
  ).then(async () => {
    try {
      await deleteRoleApi(row.role_key)
      ElMessage.success('角色已删除')
      loadRoleMenus()
      loadRoleOptions()
    } catch (e) {
      /* 拦截器已提示 */
    }
  }).catch(() => {})
}

// ==================== 数据导出 ====================
const exportDataTypeOptions = [
  { label: '脑电 eeg', value: 'eeg' },
  { label: '心电 ecg', value: 'ecg' },
  { label: '视频 video', value: 'video' },
  { label: '音频 audio', value: 'audio' },
  { label: '眼动 eye', value: 'eye' },
  { label: '量表 scale', value: 'scale' },
  { label: '任务 task', value: 'task' },
  { label: '辅助数据 json', value: 'json' },
]
const exportLayerOptions = [
  { label: '原始层 raw', value: 'raw' },
  { label: '清洗层 cleaned', value: 'cleaned' },
  { label: '特征层 feature', value: 'feature' },
  { label: '标注层 annotation', value: 'annotation' },
]

const exportForm = reactive({
  data_types: [],
  layers: [],
  encrypted: true,
  // 统一脱敏配置：一个总开关 + 各模态子项，一次性覆盖全部脱敏途径。
  // 默认全开（隐私优先）；需要原始数据时按模态单独关闭，或关掉总开关一次全关。
  //   userinfo → userInfo 文件字段级脱敏
  //   audio    → 音频声纹脱敏（定点 PCM 域确定性加噪，可逆；只改样本区，帧数/采样率不变）
  //   video    → 视频人脸区域脱敏（逐帧检测 + 人脸区不可逆马赛克 + 移除音轨）
  //                马赛克块边长按人脸短边自适应（= 人脸短边 / 8）；v3 起不再用模糊，
  //                因为模糊加大到一定程度后 SFace 余弦会回升（越糊越像同一人）
  //   eeg      → 脑电值级脱敏（全列保留：信号列加噪 + 绝对时间列整列平移）
  //   ecg      → 心电值级脱敏（全列保留：value 列加噪，heart_rate 列同样加噪）
  //   restore_kit → 随包附带「还原包」：离线还原脚本 + 本包专用密钥 + 说明
  //                 **默认开启**（2026-09-15 起）—— 接收方拿到包即可自行还原
  //                 ⚠️ 附带密钥等于把"脱敏"降级为"加扰"：若不希望接收方能还原，需手动取消勾选
  //                 注：视频脱敏不可逆（马赛克），不在还原包覆盖范围内；且脱敏后
  //                     人脸区域无法再被检测/识别，下游人脸分析不可用
  desensitize: {
    enabled: true,
    userinfo: true,
    audio: true,
    video: true,
    eeg: true,
    ecg: true,
    restore_kit: true,
  },
})
const exportSubjectList = ref([])
const exportSelectedSubjects = ref([])  // 表格当前选中行
const exporting = ref(false)
const exportSubjectTableRef = ref(null)

// 搜索筛选
const exportFilter = reactive({
  keyword: '',
  gender: '',
  riskLevel: '',
  batch: '',
})

// 进度条
const exportProgress = reactive({
  visible: false,
  percent: 0,
  loaded: 0,
  total: 0,
  status: '准备导出...',
  phase: '',        // compress | download
  totalFiles: 0,
  processedFiles: 0,
})
const exportProgressTimer = ref(null)
let _exportPollReject = null  // 导出轮询 Promise 的 reject（卸载时调用，避免挂起）

const exportFilteredSubjects = computed(() => {
  let list = exportSubjectList.value
  const kw = exportFilter.keyword.trim().toLowerCase()
  if (kw) {
    list = list.filter(s =>
      (s.pseudo_id || '').toLowerCase().includes(kw) ||
      (s.real_name || '').toLowerCase().includes(kw) ||
      (s.remark || '').toLowerCase().includes(kw)
    )
  }
  if (exportFilter.gender) {
    list = list.filter(s => s.gender === exportFilter.gender)
  }
  if (exportFilter.riskLevel) {
    if (exportFilter.riskLevel === 'none') {
      list = list.filter(s => !s.cognitive_risk_level)
    } else {
      list = list.filter(s => s.cognitive_risk_level === exportFilter.riskLevel)
    }
  }
  const batchKw = exportFilter.batch.trim().toLowerCase()
  if (batchKw) {
    list = list.filter(s => (s.collection_batch || '').toLowerCase().includes(batchKw))
  }
  return list
})

const onExportSelectionChange = (rows) => {
  exportSelectedSubjects.value = rows
}

// 受试者行展开：懒加载该受试者的数据资产（按受试者 ID 缓存）
const subjectAssetCache = reactive({})

const onExportRowExpand = async (row, expandedRows) => {
  const opened = expandedRows.some(r => r.id === row.id)
  if (!opened) return
  if (subjectAssetCache[row.id]?.loaded) return
  subjectAssetCache[row.id] = { loading: true, loaded: false, items: [] }
  try {
    const items = await fetchAllPages(getAssetsApi, { subject_id: row.id }, 100)
    subjectAssetCache[row.id] = { loading: false, loaded: true, items }
  } catch {
    subjectAssetCache[row.id] = { loading: false, loaded: false, items: [] }
    ElMessage.error('加载受试者数据资产失败')
  }
}

const groupAssetsByType = (items) => {
  const byType = {}
  for (const a of items || []) {
    byType[a.data_type] = (byType[a.data_type] || 0) + 1
  }
  return byType
}

// 全选/反选当前筛选后的列表
const exportToggleAllSubjects = () => {
  const tableRef = exportSubjectTableRef.value
  if (!tableRef) return
  // 如果当前已选数量等于当前列表数量，则清空；否则全选当前列表
  if (exportSelectedSubjects.value.length === exportFilteredSubjects.value.length) {
    tableRef.clearSelection()
  } else {
    exportFilteredSubjects.value.forEach(row => tableRef.toggleRowSelection(row, true))
  }
}

const exportRiskText = (level) => {
  const m = { normal: '正常', mci: '轻度障碍', dementia: '痴呆', '无': '无', '轻度': '轻度', '中度': '中度', '重度': '重度' }
  return m[level] || '未评估'
}
const exportRiskTagType = (level) => {
  const m = { normal: 'success', mci: 'warning', dementia: 'danger', '无': 'info', '轻度': 'warning', '中度': 'danger', '重度': 'danger' }
  return m[level] || 'info'
}

const loadExportSubjects = async () => {
  try {
    exportSubjectList.value = await fetchAllPages(getSubjectsApi)
  } catch (e) {
    ElMessage.error('加载受试者列表失败')
  }
}

// 导出预览：选择条件（受试者/类型/层）变化时实时展示命中的数据资产
const exportPreview = reactive({
  loading: false,
  loaded: false,     // 是否已成功加载（区分"无匹配资产"与"未加载/失败"）
  totalCount: 0,
  totalSize: 0,
  byType: {},
  byLayer: {},
  items: [],
})

let _exportPreviewTimer = null
const refreshExportPreview = () => {
  if (_exportPreviewTimer) clearTimeout(_exportPreviewTimer)
  _exportPreviewTimer = setTimeout(_doRefreshExportPreview, 400)
}

const _doRefreshExportPreview = async () => {
  exportPreview.loading = true
  try {
    const res = await exportPreviewApi({
      subject_ids: exportSelectedSubjects.value.map(s => s.id),
      data_types: exportForm.data_types,
      layers: exportForm.layers,
    })
    const d = res.data || {}
    exportPreview.totalCount = d.total_count || 0
    exportPreview.totalSize = d.total_size || 0
    exportPreview.byType = d.by_type || {}
    exportPreview.byLayer = d.by_layer || {}
    exportPreview.items = d.items || []
    exportPreview.loaded = true
  } catch {
    exportPreview.loaded = false
  } finally {
    exportPreview.loading = false
  }
}

watch(
  [exportSelectedSubjects, () => exportForm.data_types, () => exportForm.layers],
  refreshExportPreview,
  { deep: true },
)

const exportTypeText = (t) => {
  const opt = exportDataTypeOptions.find(o => o.value === t)
  if (opt) return opt.label.split(' ')[0]
  // 步态视频按 video 入库（video_type=gait 子类型）；历史独立 gait 资产兜底显示
  return { gait: '步态' }[t] || t
}
const exportLayerText = (ly) => {
  const m = { raw: '原始层', cleaned: '清洗层', feature: '特征层', annotation: '标注层' }
  return m[ly] || ly
}
// 后端已放开导出规模限制（文件数 / 总大小），预览不再做超限拦截，
// 仅展示命中数量与总大小供用户判断导出范围。

const handleExport = async () => {
  // 未勾选受试者时后端按"全部受试者"导出，二次确认防误触全量导出
  if (!exportSelectedSubjects.value.length) {
    try {
      await ElMessageBox.confirm(
        '当前未勾选任何受试者，将导出系统中全部受试者的数据，可能耗时较长。确定继续吗？',
        '全量导出确认',
        { type: 'warning', confirmButtonText: '继续导出', cancelButtonText: '返回选择' }
      )
    } catch { return }
  }

  // 重置进度
  exportProgress.visible = true
  exportProgress.percent = 0
  exportProgress.loaded = 0
  exportProgress.total = 0
  exportProgress.status = '正在启动导出任务...'
  exportProgress.phase = 'compress'
  exporting.value = true

  // 清理上一次轮询
  if (exportProgressTimer.value) {
    clearInterval(exportProgressTimer.value)
    exportProgressTimer.value = null
  }

  let finishedTask = null
  try {
    const subject_ids = exportSelectedSubjects.value.map(s => s.id)
    // 1. 启动异步导出任务
    const startRes = await exportStartApi({
      subject_ids,
      data_types: exportForm.data_types,
      layers: exportForm.layers,
      encrypted: exportForm.encrypted,
      desensitize: { ...exportForm.desensitize },
    })
    const taskId = startRes.data.task_id
    exportProgress.status = '任务已启动，正在解析导出范围...'

    // 2. 轮询任务进度（超时兜底 30 分钟；组件卸载时 reject，避免 Promise 永久挂起导致 zip 永不下载）
    const _pollStartedAt = Date.now()
    const _POLL_TIMEOUT = 30 * 60 * 1000
    await new Promise((resolve, reject) => {
      _exportPollReject = reject
      exportProgressTimer.value = setInterval(async () => {
        try {
          if (Date.now() - _pollStartedAt > _POLL_TIMEOUT) {
            clearInterval(exportProgressTimer.value)
            exportProgressTimer.value = null
            _exportPollReject = null
            reject(new Error('导出超时，请重试'))
            return
          }
          const res = await exportProgressApi(taskId)
          const task = res.data
          exportProgress.percent = task.percent || 0
          exportProgress.status = task.status_text || ''
          exportProgress.total = task.total_size || 0
          exportProgress.loaded = task.processed_size || 0
          exportProgress.totalFiles = task.total_files || 0
          exportProgress.processedFiles = task.processed_files || 0

          if (task.status === 'success') {
            clearInterval(exportProgressTimer.value)
            exportProgressTimer.value = null
            _exportPollReject = null
            exportProgress.phase = 'download'
            exportProgress.status = '压缩完成，正在下载...'
            resolve(task)
          } else if (task.status === 'failed') {
            clearInterval(exportProgressTimer.value)
            exportProgressTimer.value = null
            _exportPollReject = null
            reject(new Error(task.error || '打包失败'))
          }
        } catch (e) {
          clearInterval(exportProgressTimer.value)
          exportProgressTimer.value = null
          _exportPollReject = null
          reject(e)
        }
      }, 500)
    }).then(task => { finishedTask = task })

    // 3. 下载 zip
    // 优先用短期签名 URL 直接导航下载（浏览器原生流式落盘，不整读内存，与后端流式响应配套）；
    // 签名签发失败时回退 blob 下载。
    exportProgress.status = '正在下载压缩包...'
    let downloaded = false
    try {
      const sigRes = await fetchSignedUrlApi({ kind: 'asset_export', task_id: taskId })
      const signedUrl = sigRes?.data?.url
      if (signedUrl) {
        const a = document.createElement('a')
        a.href = signedUrl
        a.download = `data-export-${new Date().getTime()}.zip`
        document.body.appendChild(a)
        a.click()
        a.remove()
        downloaded = true
      }
    } catch { /* 签名失败则回退 blob 下载 */ }
    if (!downloaded) {
      const dlRes = await exportDownloadApi(taskId)
      const blob = dlRes instanceof Blob ? dlRes : new Blob([dlRes], { type: 'application/zip' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `data-export-${new Date().getTime()}.zip`
      a.click()
      URL.revokeObjectURL(url)
    }

    exportProgress.percent = 100
    exportProgress.status = '导出完成'
    const skipped = finishedTask?.skipped_files || 0
    if (skipped > 0) {
      // 如实展示跳过原因（磁盘丢失 / 脱敏失败 / 打包异常都不同），
      // 不再一律说"磁盘丢失或读取失败"
      const detail = finishedTask?.skipped_detail || []
      const lines = detail
        .slice(0, 3)
        .map((d) => `${d.file_name || d.asset_id}：${d.reason}`)
        .join('<br/>')
      ElMessageBox.alert(
        lines || '原因详见操作日志与服务端日志',
        `导出完成，但 ${skipped} 个文件被跳过`,
        { dangerouslyUseHTMLString: true, confirmButtonText: '我知道了' }
      )
    } else {
      ElMessage.success('导出成功')
    }
    setTimeout(() => { exportProgress.visible = false }, 3000)
  } catch (e) {
    exportProgress.status = '导出失败'
    exportProgress.percent = 0
    exportProgress.phase = ''
    // blob 响应的错误：尝试解析为 JSON 提取 message
    const errData = e?.response?.data
    if (errData instanceof Blob) {
      try {
        const text = await errData.text()
        const err = JSON.parse(text)
        ElMessage.error(err.message || '导出失败')
      } catch {
        ElMessage.error(e?.message || '导出失败')
      }
    } else {
      ElMessage.error(e?.message || e?.response?.data?.message || '导出失败')
    }
    setTimeout(() => { exportProgress.visible = false }, 3000)
  } finally {
    exporting.value = false
  }
}

// ==================== 密钥管理 ====================
const encLoading = ref(false)
const encInfo = ref({})
const verifyLoading = ref(false)
const verifyResult = ref(null)
const rotateDialog = ref(false)
const rotateLoading = ref(false)
const rotateForm = reactive({ password: '' })
const backupDialog = ref(false)
const backupLoading = ref(false)
const backupForm = reactive({ password: '' })

const formatFileSize = (bytes) => {
  if (!bytes || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`
}

const loadEncryptionInfo = async () => {
  encLoading.value = true
  try {
    const res = await getEncryptionInfoApi()
    encInfo.value = res.data
  } catch (e) { /* 拦截器已提示 */ }
  finally {
    encLoading.value = false
  }
}

const handleVerifyKey = async () => {
  verifyLoading.value = true
  verifyResult.value = null
  try {
    const res = await verifyKeyApi()
    verifyResult.value = res.data
  } catch (e) {
    verifyResult.value = { valid: false, message: '验证请求失败' }
  } finally {
    verifyLoading.value = false
  }
}

const openBackupDialog = () => {
  backupForm.password = ''
  backupDialog.value = true
}

const handleBackupKey = async () => {
  if (!backupForm.password) {
    ElMessage.warning('请输入管理员密码')
    return
  }
  backupLoading.value = true
  try {
    const blob = await backupKeyApi({ confirm_password: backupForm.password })
    // 后端成功返回密钥文件 blob；失败返回 JSON（422），由 catch 解析
    const url = window.URL.createObjectURL(new Blob([blob]))
    const a = document.createElement('a')
    a.href = url
    a.download = `master_key_backup_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '')}.key`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
    ElMessage.success('密钥备份已下载，请妥善保管')
    backupDialog.value = false
  } catch (e) {
    // blob 响应下的错误：解析 JSON 提取 message
    const errData = e?.response?.data
    if (errData instanceof Blob) {
      try {
        const text = await errData.text()
        const err = JSON.parse(text)
        ElMessage.error(err.message || '密码验证失败')
      } catch {
        ElMessage.error('密钥备份下载失败')
      }
    } else {
      ElMessage.error(e?.message || '密钥备份下载失败')
    }
  } finally {
    backupLoading.value = false
  }
}

const openRotateDialog = () => {
  rotateForm.password = ''
  rotateDialog.value = true
}

const handleRotateKey = async () => {
  if (!rotateForm.password) {
    ElMessage.warning('请输入管理员密码')
    return
  }
  rotateLoading.value = true
  try {
    const res = await rotateKeyApi({ confirm_password: rotateForm.password })
    ElMessage.success(res.message || '密钥轮换成功')
    rotateDialog.value = false
    loadEncryptionInfo()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    rotateLoading.value = false
  }
}

// 上传/替换密钥
const importDialog = ref(false)
const importLoading = ref(false)
const importForm = reactive({ password: '', file: null })
const importFileList = ref([])

const openImportDialog = () => {
  importForm.password = ''
  importForm.file = null
  importFileList.value = []
  importDialog.value = true
}

const handleImportFileChange = (file) => {
  if (file.raw?.size > MAX_KEY_FILE_BYTES) {
    ElMessage.warning('主密钥文件超过 1MB，请检查是否选错文件')
    importForm.file = null
    importFileList.value = []
    return
  }
  importForm.file = file.raw
  importFileList.value = [file]
}

const handleImportKey = async () => {
  if (!importForm.file) {
    ElMessage.warning('请选择密钥文件')
    return
  }
  if (!importForm.password) {
    ElMessage.warning('请输入管理员密码')
    return
  }
  importLoading.value = true
  try {
    const formData = new FormData()
    formData.append('key_file', importForm.file)
    formData.append('confirm_password', importForm.password)
    const res = await importKeyApi(formData)
    ElMessage.success(res.message || '密钥替换成功')
    importDialog.value = false
    loadEncryptionInfo()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    importLoading.value = false
  }
}

onMounted(() => {
  if (isAdmin.value) {
    loadUsers()
    loadStandards()
    loadRoleMenus()
    loadRoleOptions()
    loadDesensConfig()
    loadEncryptionInfo()
    // 外部密钥已并入「密钥管理」Tab，进入页面即加载
    loadExternalKeys()
    // 支持 ?tab=xxx 直接定位
    const tab = route.query.tab
    if (tab && ['users', 'standards', 'desensitize', 'permissions', 'encryption', 'subjectTemplate', 'export'].includes(tab)) {
      activeTab.value = tab
    }
  }
})

// 组件卸载时清理导出轮询定时器并 reject 挂起的轮询 Promise，避免导出永久挂起
onBeforeUnmount(() => {
  if (exportProgressTimer.value) {
    clearInterval(exportProgressTimer.value)
    exportProgressTimer.value = null
  }
  if (_exportPollReject) {
    _exportPollReject(new Error('页面已关闭，导出中断'))
    _exportPollReject = null
  }
  if (_exportPreviewTimer) {
    clearTimeout(_exportPreviewTimer)
    _exportPreviewTimer = null
  }
})

// 切换到脱敏配置 Tab 时加载配置
watch(activeTab, (val) => {
  if (val === 'desensitize' && desensConfig.rules.length === 0) {
    loadDesensConfig()
  }
  if (val === 'subjectTemplate') {
    loadTemplate()
  }
  if (val === 'export') {
    loadExportSubjects()
    refreshExportPreview()
  }
})

// ==================== 受试者信息模板 ====================
const tplLoading = ref(false)
const tplSaving = ref(false)
const tplFields = ref([])
const tplFieldTypes = ref([
  { value: 'input', label: '单行文本' },
  { value: 'number', label: '数字' },
  { value: 'select', label: '下拉选择' },
  { value: 'textarea', label: '多行文本' },
  { value: 'date', label: '日期' },
])

/* 现有受试者表单布局的默认宽度映射（与 management 编辑弹窗保持一致）
   未显式保存 span 的字段按此映射作为起点 */
const DEFAULT_FIELD_SPAN = {
  pseudo_id: 24, age: 12, gender: 12,
  education_level: 24,
  phone: 12, id_card: 12,
  cognitive_risk_level: 24,
  emotion_status: 24,
  collection_batch: 12, collection_scene: 12,
  remark: 24,
}
const resolveSpan = (f) => f.span || DEFAULT_FIELD_SPAN[f.field_key] || 24

const loadTemplate = async () => {
  tplLoading.value = true
  try {
    const res = await getSubjectTemplateApi()
    const data = res.data?.data || res.data || {}
    tplFields.value = (data.fields || []).map(f => ({
      field_key: f.field_key || '',
      field_label: f.field_label || '',
      field_type: f.field_type || 'input',
      required: !!f.required,
      enabled: f.enabled !== false,
      sort_order: f.sort_order || 0,
      placeholder: f.placeholder || '',
      options: Array.isArray(f.options) ? f.options.map(o => ({ ...o })) : [],
      span: resolveSpan(f),
    }))
  } catch (e) {
    ElMessage.error('加载模板失败')
  } finally {
    tplLoading.value = false
  }
}

const addField = () => {
  tplFields.value.push({
    field_key: '',
    field_label: '',
    field_type: 'input',
    required: false,
    enabled: true,
    sort_order: tplFields.value.length + 1,
    placeholder: '',
    options: [],
    span: 24,
  })
}

const moveField = (idx, dir) => {
  const target = idx + dir
  if (target < 0 || target >= tplFields.value.length) return
  const arr = tplFields.value
  ;[arr[idx], arr[target]] = [arr[target], arr[idx]]
}

const saveTemplate = async () => {
  const fields = tplFields.value.map((f, i) => ({
    field_key: (f.field_key || '').trim(),
    field_label: (f.field_label || '').trim(),
    field_type: f.field_type,
    required: !!f.required,
    enabled: f.enabled !== false,
    sort_order: i + 1,
    placeholder: f.placeholder || '',
    options: f.field_type === 'select' ? (f.options || []) : [],
    span: f.span || 24,
  }))
  tplSaving.value = true
  try {
    await saveSubjectTemplateApi({ fields })
    ElMessage.success('受试者信息模板已保存')
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || '保存失败')
  } finally {
    tplSaving.value = false
  }
}

/* ===== 布局预览：拖拽排序 + 宽度切换 ===== */
const layoutPreviewDialog = ref(false)
const layoutPreviewFields = ref([])
const dragIdx = ref(-1)
const dragOverIdx = ref(-1)

const openLayoutPreview = () => {
  // 只展示启用字段，深拷贝避免直接修改原数据（tplFields 已通过 resolveSpan 填充现有布局）
  layoutPreviewFields.value = tplFields.value
    .filter(f => f.enabled !== false && f.field_key)
    .map(f => ({
      ...f,
      span: f.span || 24,
      options: Array.isArray(f.options) ? f.options.map(o => ({ ...o })) : [],
    }))
  if (!layoutPreviewFields.value.length) {
    ElMessage.warning('没有可预览的启用字段')
    return
  }
  layoutPreviewDialog.value = true
}

const onDragStart = (idx) => { dragIdx.value = idx }
const onDragOver = (idx) => { dragOverIdx.value = idx }
const onDragEnd = () => { dragIdx.value = -1; dragOverIdx.value = -1 }
const onDrop = (targetIdx) => {
  const from = dragIdx.value
  if (from < 0 || from === targetIdx) { dragIdx.value = -1; dragOverIdx.value = -1; return }
  const arr = layoutPreviewFields.value
  const [moved] = arr.splice(from, 1)
  arr.splice(targetIdx, 0, moved)
  dragIdx.value = -1
  dragOverIdx.value = -1
}

const applyLayoutPreview = () => {
  // 将预览中的顺序与 span 同步回 tplFields（保留未启用字段在末尾）
  const enabledKeys = layoutPreviewFields.value.map(f => f.field_key)
  const enabledMap = {}
  layoutPreviewFields.value.forEach(f => { enabledMap[f.field_key] = f })
  // 更新已启用字段的 span 和顺序
  const reordered = []
  const remaining = []
  tplFields.value.forEach(f => {
    if (enabledKeys.includes(f.field_key)) {
      reordered.push({ ...f, span: enabledMap[f.field_key].span })
    } else {
      remaining.push(f)
    }
  })
  // 按预览顺序重排启用字段，未启用字段追加到末尾
  const orderedEnabled = enabledKeys.map(k => reordered.find(f => f.field_key === k))
  tplFields.value = [...orderedEnabled, ...remaining]
  layoutPreviewDialog.value = false
  ElMessage.success('布局已应用，请点击"保存模板"以持久化')
}

// ==================== 外部密钥管理（AES-256-CBC） ====================
const externalKeyList = ref([])
const extKeyLoading = ref(false)

// 新增/编辑对话框
const extKeyDialog = ref(false)
const extKeySaving = ref(false)
const extKeyForm = reactive({
  id: null,
  name: '',
  description: '',
  key_b64: '',
  iv_b64: '',
  is_active: true,
})

// 导入对话框
const extKeyImportDialog = ref(false)
const extKeyImporting = ref(false)
// 密钥文件为 32 字节文本，限制 1MB 内（防止误选大文件）
const MAX_KEY_FILE_BYTES = 1 * 1024 * 1024
const extKeyImportForm = reactive({
  name: '',
  description: '',
  file: null,
})

const handleExtKeyFileChange = (file) => {
  if (file.raw?.size > MAX_KEY_FILE_BYTES) {
    ElMessage.warning('密钥文件超过 1MB，请检查是否选错文件')
    extKeyImportForm.file = null
    return
  }
  extKeyImportForm.file = file.raw
}

const handleExtKeyFileRemove = () => {
  extKeyImportForm.file = null
}

// 验证对话框
const extKeyVerifyDialog = ref(false)
const extKeyVerifying = ref(false)
const extKeyVerifyForm = reactive({
  keyId: null,
  keyName: '',
  file_path: '',
})
const extKeyVerifyResult = ref(null)

const loadExternalKeys = async () => {
  extKeyLoading.value = true
  try {
    const res = await getExternalKeysApi()
    externalKeyList.value = res.data || []
  } catch (e) {
    ElMessage.error('加载外部密钥列表失败')
  } finally {
    extKeyLoading.value = false
  }
}

const resetExtKeyForm = () => {
  extKeyForm.id = null
  extKeyForm.name = ''
  extKeyForm.description = ''
  extKeyForm.key_b64 = ''
  extKeyForm.iv_b64 = ''
  extKeyForm.is_active = true
}

const openCreateExtKeyDialog = () => {
  resetExtKeyForm()
  extKeyDialog.value = true
}

const openEditExtKeyDialog = async (row) => {
  resetExtKeyForm()
  try {
    const res = await getExternalKeyApi(row.id)
    const data = res.data || {}
    extKeyForm.id = data.id
    extKeyForm.name = data.name
    extKeyForm.description = data.description || ''
    extKeyForm.key_b64 = data.key_b64 || ''
    extKeyForm.iv_b64 = data.iv_b64 || ''
    extKeyForm.is_active = data.is_active
    extKeyDialog.value = true
  } catch (e) {
    ElMessage.error('加载密钥详情失败')
  }
}

const handleSaveExtKey = async () => {
  if (!extKeyForm.name.trim()) {
    ElMessage.warning('密钥名称不能为空')
    return
  }
  if (!extKeyForm.id && (!extKeyForm.key_b64.trim() || !extKeyForm.iv_b64.trim())) {
    ElMessage.warning('新建时 AES Key 与 IV 不能为空')
    return
  }
  extKeySaving.value = true
  try {
    const payload = {
      name: extKeyForm.name.trim(),
      description: extKeyForm.description.trim(),
      is_active: extKeyForm.is_active,
    }
    // 编辑时若填写了 key/iv 才提交（避免误清空）
    if (extKeyForm.key_b64.trim() && extKeyForm.iv_b64.trim()) {
      payload.key_b64 = extKeyForm.key_b64.trim()
      payload.iv_b64 = extKeyForm.iv_b64.trim()
    }
    if (extKeyForm.id) {
      await updateExternalKeyApi(extKeyForm.id, payload)
      ElMessage.success('外部密钥已更新')
    } else {
      payload.key_b64 = extKeyForm.key_b64.trim()
      payload.iv_b64 = extKeyForm.iv_b64.trim()
      await createExternalKeyApi(payload)
      ElMessage.success('外部密钥创建成功')
    }
    extKeyDialog.value = false
    loadExternalKeys()
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '保存失败')
  } finally {
    extKeySaving.value = false
  }
}

const handleDeleteExtKey = async (row) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除外部密钥「${row.name}」吗？删除后相关加密文件将无法解密。`,
      '删除确认',
      { type: 'warning' }
    )
    await deleteExternalKeyApi(row.id)
    ElMessage.success(`外部密钥「${row.name}」已删除`)
    loadExternalKeys()
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

const handleDownloadExtKey = async (row) => {
  try {
    const blob = await downloadExternalKeyApi(row.id)
    // 后端成功返回密钥文件 blob；失败返回 JSON，由 catch 解析
    const url = window.URL.createObjectURL(new Blob([blob]))
    const safeName = (row.name || '').replace(/[\\/:*?"<>|\s]+/g, '_').replace(/^_+|_+$/g, '')
    const a = document.createElement('a')
    a.href = url
    a.download = `密钥_${safeName || row.id}.txt`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    window.URL.revokeObjectURL(url)
    ElMessage.success('密钥文件已下载，请妥善保管')
  } catch (e) {
    const errData = e?.response?.data
    if (errData instanceof Blob) {
      try {
        const text = await errData.text()
        const err = JSON.parse(text)
        ElMessage.error(err.message || '密钥下载失败')
      } catch {
        ElMessage.error('密钥下载失败')
      }
    } else {
      ElMessage.error(e?.message || '密钥下载失败')
    }
  }
}

const openImportExtKeyDialog = () => {
  extKeyImportForm.name = ''
  extKeyImportForm.description = ''
  extKeyImportForm.file = null
  extKeyImportDialog.value = true
}

const handleImportExtKey = async () => {
  if (!extKeyImportForm.name.trim()) {
    ElMessage.warning('密钥名称不能为空')
    return
  }
  if (!extKeyImportForm.file) {
    ElMessage.warning('请选择密钥.txt 文件')
    return
  }
  extKeyImporting.value = true
  try {
    const formData = new FormData()
    formData.append('key_file', extKeyImportForm.file)
    formData.append('name', extKeyImportForm.name.trim())
    formData.append('description', extKeyImportForm.description.trim())
    await importExternalKeyApi(formData)
    ElMessage.success(`外部密钥「${extKeyImportForm.name}」导入成功`)
    extKeyImportDialog.value = false
    loadExternalKeys()
  } catch (e) {
    ElMessage.error(e.response?.data?.message || '导入失败')
  } finally {
    extKeyImporting.value = false
  }
}

const openVerifyExtKeyDialog = (row) => {
  extKeyVerifyForm.keyId = row.id
  extKeyVerifyForm.keyName = row.name
  extKeyVerifyForm.file_path = ''
  extKeyVerifyResult.value = null
  extKeyVerifyDialog.value = true
}

const handleVerifyExtKey = async () => {
  if (!extKeyVerifyForm.file_path.trim()) {
    ElMessage.warning('请输入待验证的文件路径')
    return
  }
  extKeyVerifying.value = true
  try {
    const res = await verifyExternalKeyApi(extKeyVerifyForm.keyId, {
      file_path: extKeyVerifyForm.file_path.trim(),
    })
    extKeyVerifyResult.value = res.data || null
  } catch (e) {
    extKeyVerifyResult.value = {
      valid: false,
      message: e.response?.data?.message || '验证请求失败',
    }
  } finally {
    extKeyVerifying.value = false
  }
}

// ==================== .enc 文件选择器（验证密钥辅助） ====================
const encFileDialog = ref(false)
const encFileLoading = ref(false)
const encFileList = ref([])
const encFileKeyword = ref('')
const encFileCurrent = ref(null)

const formatEncFileTime = (ts) => {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const loadEncFiles = async () => {
  encFileLoading.value = true
  try {
    const res = await getEncFilesApi({ keyword: encFileKeyword.value.trim(), limit: 500 })
    encFileList.value = res.data || []
  } catch (e) {
    encFileList.value = []
  } finally {
    encFileLoading.value = false
  }
}

const openEncFileDialog = () => {
  encFileCurrent.value = null
  encFileKeyword.value = ''
  encFileDialog.value = true
  // 首次打开时加载，已加载则保留列表（避免重复请求）
  if (encFileList.value.length === 0) {
    loadEncFiles()
  }
}

const onEncFileCurrentChange = (row) => {
  encFileCurrent.value = row
}

const confirmEncFile = () => {
  if (!encFileCurrent.value) return
  extKeyVerifyForm.file_path = encFileCurrent.value.path
  // 切换到相对路径后清空旧的验证结果，避免误读
  extKeyVerifyResult.value = null
  encFileDialog.value = false
}
</script>


<style scoped>
.form-tip {
  font-size: 12px;
  color: #909399;
  line-height: 1.5;
  margin-top: 4px;
}

/* 布局预览：可拖拽字段卡片 */
.layout-preview-form .el-row {
  row-gap: 12px;
}
.layout-field-card {
  border: 1px dashed #d0d0d0;
  border-radius: 6px;
  padding: 8px 8px 0;
  background: #fafafa;
  transition: border-color 0.2s, box-shadow 0.2s, background 0.2s;
  height: 100%;
}
.layout-field-card:hover {
  border-color: #409eff;
  background: #fff;
}
.layout-field-card.dragging {
  opacity: 0.4;
  border-color: #409eff;
}
.layout-field-card.dragOver {
  border-color: #409eff;
  border-style: solid;
  box-shadow: 0 0 8px rgba(64, 158, 255, 0.3);
  background: #ecf5ff;
}
.layout-field-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.layout-field-head .drag-handle {
  cursor: grab;
  color: #c0c4cc;
}
.layout-field-head .drag-handle:active {
  cursor: grabbing;
}
.layout-field-label {
  font-size: 12px;
  color: #606266;
  font-weight: 500;
  flex: 1;
}
.span-toggle {
  flex-shrink: 0;
}
/* 弹窗内表单禁用态不要太灰 */
.layout-preview-dialog :deep(.el-input.is-disabled .el-input__inner),
.layout-preview-dialog :deep(.el-textarea.is-disabled .el-textarea__inner) {
  color: #606266;
  -webkit-text-fill-color: #606266;
}
</style>
