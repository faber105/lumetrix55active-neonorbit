import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

const telegram = window.Telegram?.WebApp
const rootStyle = document.documentElement.style

const px = (value) => `${Math.max(0, Number(value) || 0)}px`

function syncTelegramViewport() {
  const viewportHeight =
    telegram?.viewportStableHeight ||
    telegram?.viewportHeight ||
    window.visualViewport?.height ||
    window.innerHeight

  rootStyle.setProperty('--ap-viewport-height', px(Math.round(viewportHeight || 0)))

  const safe = telegram?.safeAreaInset || {}
  const contentSafe = telegram?.contentSafeAreaInset || safe
  rootStyle.setProperty('--ap-safe-top', px(safe.top))
  rootStyle.setProperty('--ap-safe-right', px(safe.right))
  rootStyle.setProperty('--ap-safe-bottom', px(safe.bottom))
  rootStyle.setProperty('--ap-safe-left', px(safe.left))
  rootStyle.setProperty('--ap-content-safe-top', px(contentSafe.top))
  rootStyle.setProperty('--ap-content-safe-right', px(contentSafe.right))
  rootStyle.setProperty('--ap-content-safe-bottom', px(contentSafe.bottom))
  rootStyle.setProperty('--ap-content-safe-left', px(contentSafe.left))
}

function requestImmersiveTelegramMode() {
  if (!telegram) return
  try { telegram.ready?.() } catch {}
  try { telegram.expand?.() } catch {}
  try { telegram.disableVerticalSwipes?.() } catch {}
  try { telegram.enableClosingConfirmation?.() } catch {}
  try { telegram.setHeaderColor?.('#070a12') } catch {}
  try { telegram.setBackgroundColor?.('#070a12') } catch {}
  try { telegram.setBottomBarColor?.('#070a12') } catch {}
  try { telegram.lockOrientation?.() } catch {}
  try {
    if (!telegram.isFullscreen) telegram.requestFullscreen?.()
  } catch {}
  syncTelegramViewport()
}

requestImmersiveTelegramMode()

for (const event of ['viewportChanged', 'safeAreaChanged', 'contentSafeAreaChanged', 'fullscreenChanged']) {
  try { telegram?.onEvent?.(event, syncTelegramViewport) } catch {}
}

try {
  telegram?.onEvent?.('activated', requestImmersiveTelegramMode)
} catch {}

window.visualViewport?.addEventListener?.('resize', syncTelegramViewport)
window.addEventListener('resize', syncTelegramViewport, { passive: true })
window.addEventListener('orientationchange', syncTelegramViewport, { passive: true })

document.addEventListener('pointerdown', requestImmersiveTelegramMode, { once: true, passive: true })

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
