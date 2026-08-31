import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/dist/locale/zh-cn.mjs'
import {
  Share, User, SwitchButton, Warning, FirstAidKit, InfoFilled,
  ChatDotRound, Guide, OfficeBuilding, Right, CircleCloseFilled,
  WarningFilled, MoreFilled, Cpu, CopyDocument, Sunny, Star,
  Connection, Calendar, Bowl, Position, Moon, Download, Collection,
  Tickets, Monitor, WindPower, MagicStick, Coin,
  ZoomIn, ZoomOut, RefreshRight, Message, Lock, VideoPause, Promotion, Plus,
  Delete,
} from '@element-plus/icons-vue'
import { createPinia } from 'pinia'
import router from './router'
import App from './App.vue'
import './style.css'

const app = createApp(App)

// 按需注册项目实际使用的图标（用于动态 <component :is>）
const usedIcons = {
  Share, User, SwitchButton, Warning, FirstAidKit, InfoFilled,
  ChatDotRound, Guide, OfficeBuilding, Right, CircleCloseFilled,
  WarningFilled, MoreFilled, Cpu, CopyDocument, Sunny, Star,
  Connection, Calendar, Bowl, Position, Moon, Download, Collection,
  Tickets, Monitor, WindPower, MagicStick, Coin,
  ZoomIn, ZoomOut, RefreshRight, Message, Lock, VideoPause, Promotion, Plus,
  Delete,
}
for (const [key, component] of Object.entries(usedIcons)) {
  app.component(key, component)
}

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')
