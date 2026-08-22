import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

function alphaPulseRuntimePatch() {
  return {
    name: 'alphapulse-runtime-patch',
    enforce: 'pre',
    transform(code, id) {
      const isApp = id.endsWith('/src/App.jsx') || id.endsWith('\\src\\App.jsx')
      const isApi = id.endsWith('/src/api.js') || id.endsWith('\\src\\api.js')
      if (!isApp && !isApi) return null

      let next = code

      if (isApp) {
        const oldStrategies = 'const AUTO_STRATEGIES=[{id:"trend_pulse",icon:"↗",name:"Trend Pulse",desc:"EMA 9/21/50/200 · ADX/DMI · MACD · RSI",tone:"violet"},{id:"range_reversal",icon:"↔",name:"Range Reversal",desc:"Bollinger · RSI · слабый ADX · rejection",tone:"cyan"},{id:"volatility_breakout",icon:"⚡",name:"Volatility Breakout",desc:"Donchian · ATR expansion · DMI · momentum",tone:"orange"}];'
        const newStrategies = 'const AUTO_STRATEGIES=[{id:"smart_confluence",icon:"AI",name:"Smart Confluence",desc:"5 стратегий · выбирает сильнейший подтверждённый сетап",tone:"violet"},{id:"trend_pulse",icon:"↗",name:"Trend Pulse",desc:"EMA 9/21/50/200 · ADX/DMI · MACD · RSI",tone:"violet"},{id:"range_reversal",icon:"↔",name:"Range Reversal",desc:"Bollinger · RSI · слабый ADX · rejection",tone:"cyan"},{id:"volatility_breakout",icon:"⚡",name:"Volatility Breakout",desc:"Donchian · ATR expansion · DMI · momentum",tone:"orange"}];'
        next = next.replace(oldStrategies, newStrategies)
        if (!next.includes('import "./preload_ui_sync.js";')) {
          next = 'import "./preload_ui_sync.js";\n' + next
        }
      }

      if (isApi) {
        next = next.replace(
          'const CDN_API = "https://birthday-map-race-packing.trycloudflare.com";',
          'const CDN_API = window.location.origin;'
        )
        next = next.replace(
          'const connect = async () => {\n    if (stopped || !getTelegramInitData()) return;',
          'const connect = async () => {\n    if (stopped || !getTelegramInitData()) return;\n    if (isCloudflareCdn) { startPolling(); return; }'
        )
      }

      return next === code ? null : { code: next, map: null }
    },
  }
}

export default defineConfig({ plugins: [alphaPulseRuntimePatch(), react()] })
