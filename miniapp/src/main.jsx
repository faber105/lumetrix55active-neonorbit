import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './autoMonitor.css'
import App from './App.jsx'
import AutoTradeMonitor from './AutoTradeMonitor.jsx'
import { installBrokerAutoLive } from './brokerAutoLive.js'

const telegram = window.Telegram?.WebApp
telegram?.ready?.()
telegram?.expand?.()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
    <AutoTradeMonitor />
  </StrictMode>,
)

installBrokerAutoLive()
