<template>
  <el-container class="main-layout">
    <el-aside :width="isCollapse ? '64px' : '200px'" class="sidebar">
      <div class="logo">
        <el-icon size="24"><DataAnalysis /></el-icon>
        <span v-show="!isCollapse" class="logo-text">多模态数据平台</span>
      </div>
      <el-menu
        :default-active="activeMenu"
        :collapse="isCollapse"
        router
        background-color="#001529"
        text-color="#b7c0cd"
        active-text-color="#fff"
      >
        <el-menu-item
          v-for="item in menus"
          :key="item.path"
          :index="item.path"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <template #title>{{ item.title }}</template>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-left">
          <el-icon class="collapse-btn" @click="isCollapse = !isCollapse">
            <Fold v-if="!isCollapse" />
            <Expand v-else />
          </el-icon>
          <el-breadcrumb separator="/">
            <el-breadcrumb-item>{{ currentTitle }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="header-right">
          <el-button text :icon="QuestionFilled" @click="helpDrawer = true">帮助</el-button>
          <el-dropdown @command="handleCommand">
            <span class="user-info">
              <el-avatar :size="32" icon="UserFilled" />
              <span class="username">{{ userInfo?.real_name || userInfo?.username || '用户' }}</span>
              <el-tag size="small" type="info">{{ roleText }}</el-tag>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <el-main class="content">
        <router-view />
      </el-main>
    </el-container>

    <!-- 帮助抽屉 -->
    <el-drawer v-model="helpDrawer" title="使用帮助" size="640px" direction="rtl">
      <el-collapse v-model="activeHelp" accordion>
        <el-collapse-item name="dashboard" title="📊 工作台">
          <div class="help-section">
            <p><b>用途：</b>数据平台总览，展示核心统计指标与最近动态。</p>
            <p><b>操作：</b>进入即显示各项统计卡片与图表，点击"刷新"可重新加载数据。</p>
            <p class="help-tip">提示：工作台数据有 5 分钟缓存，如需实时数据请点刷新按钮。</p>
          </div>
        </el-collapse-item>
        <el-collapse-item name="management" title="🗂️ 数据管理">
          <div class="help-section">
            <p><b>用途：</b>管理受试者与数据资产，是其他模块的数据来源。</p>
            <p><b>主要操作：</b></p>
            <ul>
              <li>新增受试者：填写伪ID（唯一）、年龄、性别、风险分级等，可同时上传数据文件</li>
              <li>筛选：支持按关键词、性别、风险分级（含"未评估"）、批次多条件筛选</li>
              <li>视图切换：顶部支持"按受试者"和"按数据类型"两种视图</li>
              <li>编辑/删除：每行右侧"编辑"修改信息，"删除"会级联清理资产、标注任务与磁盘文件</li>
              <li>数据接入：支持单文件上传或整个文件夹批量导入，按受试者伪ID分目录存储</li>
              <li>自动扫描配置：配置监控文件夹后，系统按秒级间隔（10-86400秒）定期扫描子文件夹，每个子文件夹自动创建为新受试者；首次粘贴完整路径会自动记忆前缀，后续点击"选择"即可基于本地文件夹浏览器复用</li>
              <li>扫描行为：扫到新受试者后会自动清空筛选条件并刷新列表；扫描间隔支持秒级；同名 subject + 同 data_type + 同 original_filename + 同 original_size 视为重复，自动跳过</li>
              <li>数据资产：点击"数据资产"查看该受试者所有文件，可编辑/删除单条资产</li>
              <li>历史版本：每条受试者/数据资产的增改删都会自动归档快照，可点击"历史"查看并回滚（仅 admin）</li>
              <li>操作日志：点击"操作日志"查看所有用户的增删改记录（含时间、用户、身份、IP）</li>
              <li>视频采集：每行"视频采集"按钮进入采集弹窗，按需采集 <b>face/body/gait</b> 三类视频，顶部会显示各类"已采/未采"进度；该类型已有视频时重新采集会替换旧视频</li>
              <li>设备源：支持普通摄像头、<b>Orbbec 深度相机</b>、<b>RealSense 深度相机</b>三种；深度相机模式彩色+深度画面同时显示，并展示设备序列号；深度相机的 RGB 也会作为普通摄像头列出</li>
              <li>USB 透传自愈：深度相机未检测到时，点击"透传深度相机"按钮触发宿主机无人值守复位并重新绑定 USB 设备，全程约 1-3 分钟（远程且网卡受影响时可能断网 10-30 秒），完成后重新检测即可采集</li>
              <li>批量操作：列表顶部分别支持"全选本页/跨页全选"，可批量删除选中的受试者或数据资产</li>
            </ul>
            <p class="help-tip">⚠️ 删除受试者会同步删除其所有数据资产、标注任务与磁盘文件，不可恢复，请谨慎操作。</p>
            <p class="help-tip">🔒 所有数据湖文件均采用 AES-256-GCM 信封加密存储（文件头 77 字节含 DMEC 标识、版本、nonce、加密后的 DEK），密钥文件由管理员单独保管。</p>
          </div>
        </el-collapse-item>
        <el-collapse-item name="cleaning" title="🧹 数据清洗及标准化">
          <div class="help-section">
            <p><b>用途：</b>对原始数据进行清洗、去噪、标准化处理。</p>
            <p><b>操作：</b>选择待处理的数据资产，触发清洗/标准化任务。</p>
            <p class="help-tip">提示：清洗任务通过 Celery 异步执行（功能开发中，当前为模拟提交）。</p>
          </div>
        </el-collapse-item>
        <el-collapse-item name="annotation" title="✏️ 数据标注">
          <div class="help-section">
            <p><b>用途：</b>对数据资产进行标注，支持预标注→人工标注→医生复核全流程。</p>
            <p><b>多模态支持：</b>视频、音频、脑电（EEG）、心电（ECG）、眼动、步态、量表、认知任务八种数据类型，工作台会根据数据类型自动选择合适的预览方式。</p>
            <p><b>主要操作：</b></p>
            <ul>
              <li>新建任务：选择数据资产创建标注任务，支持多资产批量创建为同一任务组</li>
              <li>预标注：对 pending 状态任务触发自动预标注（生成建议标签）</li>
              <li>标注工作台：进入工作台后可按任务组翻页标注，EEG/ECG 自动渲染为 ECharts 多通道波形（内置 dataZoom 缩放）</li>
              <li>添加标签：仅需选择标签和填写备注，不再需要填写时间区间</li>
              <li>复核：医生角色可对已标注任务进行通过/驳回，复核时可微调标注内容</li>
              <li>编辑：可修改标注员、复核医生、备注（状态变更需通过流程按钮）</li>
              <li>删除：级联删除任务及其所有标注结果与版本</li>
            </ul>
            <p><b>可视化面板：</b>页面顶部展示完成率/合格率/复核通过率统计，以及任务状态分布饼图与标注员任务负载柱图。</p>
            <p class="help-tip">⚠️ 提交标注前请确保已选择至少一个标签，否则系统会提示"至少选择一个标签"。</p>
            <p class="help-tip">权限：复核功能仅 admin/doctor 角色可用；标注功能需 admin/annotator 角色。</p>
          </div>
        </el-collapse-item>
        <el-collapse-item name="visualization" title="📈 数据对齐与可视化">
          <div class="help-section">
            <p><b>用途：</b>查看受试者多模态数据对齐可视化（视频/音频/脑电/心电/眼动/步态）。</p>
            <p><b>主要操作：</b></p>
            <ul>
              <li>筛选受试者：顶部支持按伪ID、性别、风险分级（含"未评估"）筛选</li>
              <li>数据概览：未选受试者时显示统计卡片与分布图</li>
              <li>多模态查看：选中受试者后显示视频、音频、脑电、心电、眼动、步态、深度视频、量表图表</li>
              <li>统一时间轴：顶部滑块可对齐各路信号到同一时刻，支持播放/暂停/重置</li>
              <li>视频播放：MKV/AVI 等非原生格式会自动转码为 MP4，首次较慢</li>
              <li>多视频切换：视频模块右上角下拉切换不同视频文件</li>
              <li>深度视频：深度模块按 face/body/gait 整行分三列展示对应视频的深度视频；Orbbec 深度轨（MKV）与 RealSense 原始深度序列（.zst）由后端实时转码为伪彩色 MP4 后播放，首次需数秒至数十秒，之后有缓存秒开</li>
              <li>量表：支持 MoCA/MMSE/AD8 等量表统一解析；受试者模式展示该受试者全部量表资产卡片与得分，按文件模式单选展示雷达图与总分</li>
              <li>眼动：有真实眼动资产时展示能力值/风险指数等指标；数据解析失败会明确提示失败状态，不再显示随机模拟数据</li>
            </ul>
            <p class="help-tip">⚠️ 首次播放非 MP4 格式视频时，后端需转码（可能耗时数秒至数十秒），请耐心等待。</p>
            <p class="help-tip">提示：切换视频时浏览器 Network 面板可能出现 ERR_ABORTED 红条，这是中止旧请求的正常行为，不影响播放。</p>
            <p class="help-tip">深度视频加载失败时界面会给出"无深度轨/转码失败"的分类提示，可点击"重试"按钮；无深度轨的视频无法可视化。</p>
            <p class="help-tip">脑电数据：采用 BrainLink Pro 设备，FP1 通道，512Hz 采样，原始值按公式 raw × (1.8/4096) / 2000 转为 μV。同时支持 OpenBCI CSV 多通道、EDF/EDF+ 标准脑电二进制格式。</p>
          </div>
        </el-collapse-item>
        <el-collapse-item name="label" title="🏷️ 标签管理">
          <div class="help-section">
            <p><b>用途：</b>管理标注用的标签体系（按类型分组，支持多级标签）。</p>
            <p><b>主要操作：</b></p>
            <ul>
              <li>新建/编辑标签：填写标签名、标签值、所属类型，可设置颜色</li>
              <li>替换标签：将旧标签替换为新标签，所有历史标注记录会级联更新</li>
              <li>删除标签：已被标注使用过的标签禁止删除，需先替换或清理相关标注</li>
              <li>样例标注：标签管理页右侧"样例标注"标签页可查看各标签被使用的频次与分布</li>
            </ul>
            <p class="help-tip">⚠️ 标签创建、修改、删除仅 admin 角色可用；所有登录用户均可查看标签。</p>
            <p class="help-tip">提示：标签值变更会级联更新所有标注记录，操作不可逆，请谨慎。</p>
          </div>
        </el-collapse-item>
        <el-collapse-item name="system" title="⚙️ 系统管理">
          <div class="help-section">
            <p><b>用途：</b>管理用户、角色权限、脱敏规则、命名规范、密钥与系统模板。</p>
            <p><b>包含模块（左侧 Tab 切换）：</b></p>
            <ul>
              <li><b>用户管理：</b>新增用户、编辑角色、启用/禁用账号、重置密码</li>
              <li><b>角色权限：</b>为每个角色配置可访问菜单；admin 永远拥有全部菜单，每个角色至少保留一个菜单；操作会记录到日志（target_type=role_menu）</li>
              <li><b>脱敏规则：</b>非 admin 角色看到的真实姓名/电话/邮箱/身份证/地址等字段按规则脱敏；支持 mask_all/mask_middle/mask_head/mask_tail/mask_email/hash/redact 七种算法；全局开关可一键启停；配置改动立即清缓存生效；提供预览对比</li>
              <li><b>命名规范：</b>按数据类型配置文件名模板（支持 {data_type}/{pseudo_id}/{timestamp}/{scene}/{batch} 等变量）；上传时自动规范化，已存在的文件可批量更新；校验扩展名白名单，生成唯一文件名避免覆盖</li>
              <li><b>受试者模板：</b>配置新增受试者表单的字段（输入框/数字/下拉/文本域/日期），可设置必填、占位文字、排序</li>
              <li><b>密钥管理：</b>查看主密钥指纹与加密文件统计；验证密钥完整性（解密全部加密文件）；下载密钥备份（需二次密码确认，仅 admin）；上传/替换密钥（需二次密码，会用新密钥重加密全部文件）；轮换主密钥（高危，需二次密码）</li>
              <li><b>自动扫描：</b>跳转到数据管理页的扫描配置（详见"数据管理"）</li>
              <li><b>数据导出：</b>批量导出数据资产为 ZIP。左侧勾选受试者（支持关键词/性别/风险分级/批次筛选与全选），右侧勾选数据类型与数据层（留空=全部）；"加密导出"开关默认开启（主密钥加密，下载后需对应密钥解密），可切换明文；"导出预览"实时展示命中资产统计与明细，超限（单次最多 200 个文件 / 2GB）会提前警告；导出为异步压缩 + 进度条 + 签名 URL 下载，磁盘缺失或读取失败的文件会跳过并在完成时提示数量</li>
            </ul>
            <p class="help-tip">⚠️ 系统管理仅管理员（admin）角色可访问。</p>
            <p class="help-tip">角色说明：admin（管理员，全权限）、doctor（医生，可复核）、annotator（标注员，可标注）、nurse（护理人员，仅查阅）、engineer（数据工程师）。</p>
            <p class="help-tip">🔒 高危密钥操作（轮换/导入/备份下载）均需二次输入管理员密码确认；备份文件请离线妥善保管，丢失将导致全部数据无法解密。</p>
            <p class="help-tip">提示：所有受试者/数据资产/用户/数据标准/标注任务的增删改都会自动归档为快照，admin 可在记录详情页"历史"中回滚（回滚前会先归档当前状态）。</p>
          </div>
        </el-collapse-item>
        <el-collapse-item name="common" title="💡 通用注意事项">
          <div class="help-section">
            <ul>
              <li>登录后 JWT Token 有效期默认 7 天，过期需重新登录；视频播放与文件下载使用后端签发的短期签名 URL（5 分钟有效），JWT 不会进入 URL</li>
              <li>文件上传按受试者伪ID分目录存储：<code>data_lake/raw/{伪ID}/{规范化文件名}</code></li>
              <li>所有数据湖文件采用 AES-256-GCM 信封加密，密钥由管理员通过"系统管理→密钥管理"单独维护</li>
              <li>视频转码缓存位于 <code>data_lake/.transcodes/{资产ID}.mp4</code>，删除资产会同步清理</li>
              <li>所有增删改操作均记录到操作日志（含用户名/角色/北京时间/目标类型），可在"数据管理→操作日志"查看</li>
              <li>删除操作均会弹出二次确认，确认后不可恢复</li>
              <li>非 admin 角色列表数据按脱敏规则处理；admin 角色始终看到原始数据</li>
              <li>USB 透传依赖宿主机代理：需管理员在宿主机以管理员身份运行 <code>install_usb_agent.ps1</code> 一次性安装（注册开机自启计划任务 <code>DataMaUsbAgent</code>）；未部署时"透传深度相机"按钮不可用</li>
              <li>RealSense 原始深度序列以 DZST 压缩格式存储（.zst 数据资产），可视化播放时由后端实时转码；深度数据加密导出时一并打包</li>
              <li>如遇页面异常，请先检查浏览器控制台（F12）或联系管理员</li>
            </ul>
          </div>
        </el-collapse-item>
      </el-collapse>
    </el-drawer>
  </el-container>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { QuestionFilled } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const userInfo = computed(() => userStore.userInfo)
const isCollapse = ref(false)
const helpDrawer = ref(false)
const activeHelp = ref('dashboard')

const ALL_MENUS = [
  { key: 'dashboard', path: '/dashboard', title: '工作台', icon: 'Odometer' },
  { key: 'management', path: '/management', title: '数据管理', icon: 'Coin' },
  { key: 'data', path: '/data', title: '数据清洗及标准化', icon: 'Brush' },
  { key: 'annotation', path: '/annotation', title: '数据标注', icon: 'EditPen' },
  { key: 'label', path: '/label', title: '标签管理', icon: 'PriceTag' },
  { key: 'visualization', path: '/visualization', title: '数据对齐与可视化', icon: 'DataLine' },
  { key: 'system', path: '/system', title: '系统管理', icon: 'Setting' },
  { key: 'operation_log', path: '/operation-log', title: '操作日志', icon: 'Document' },
]

const menus = computed(() => ALL_MENUS.filter((m) => userStore.hasMenu(m.key)))

const activeMenu = computed(() => route.path)
const currentTitle = computed(() => route.meta.title || '')

const roleMap = {
  admin: '管理员',
  doctor: '医生',
  annotator: '标注员',
  nurse: '护理人员',
  engineer: '数据工程师',
}
const roleText = computed(() => roleMap[userInfo.value?.role] || '用户')

const handleCommand = (command) => {
  if (command === 'logout') {
    userStore.logout()
    router.push('/login')
  }
}
</script>

<style scoped lang="scss">
.main-layout {
  height: 100vh;
}
.sidebar {
  background-color: #001529;
  transition: width 0.28s;
  overflow: hidden;
}
.logo {
  height: 60px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 18px;
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  white-space: nowrap;
  .logo-text {
    font-size: 15px;
  }
}
.el-menu {
  border-right: none;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #ebeef5;
  .header-left {
    display: flex;
    align-items: center;
    gap: 16px;
  }
  .collapse-btn {
    cursor: pointer;
    font-size: 20px;
  }
  .user-info {
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
    .username {
      font-size: 14px;
    }
  }
}
.content {
  background: #f0f2f5;
  overflow-y: auto;
}
.help-section {
  line-height: 1.7;
  font-size: 13px;
  color: #303133;
  p { margin: 6px 0; }
  ul { padding-left: 20px; margin: 6px 0; }
  li { margin: 4px 0; }
  .help-tip {
    color: #909399;
    background: #f4f4f5;
    padding: 6px 10px;
    border-radius: 4px;
    margin: 8px 0;
    border-left: 3px solid #409eff;
  }
}
</style>
