import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

function alphaPulseAutoUiPatch() {
  return {
    name: 'alphapulse-auto-ui-patch',
    enforce: 'pre',
    transform(code, id) {
      if (!id.endsWith('/src/App.jsx') && !id.endsWith('\\src\\App.jsx')) return null

      let next = code

      // Make Smart Confluence a visible, selectable first-class strategy in both
      // AUTO modes. The backend patch handles the actual 5-strategy scan.
      const oldStrategies = 'const AUTO_STRATEGIES=[{id:"trend_pulse",icon:"↗",name:"Trend Pulse",desc:"EMA 9/21/50/200 · ADX/DMI · MACD · RSI",tone:"violet"},{id:"range_reversal",icon:"↔",name:"Range Reversal",desc:"Bollinger · RSI · слабый ADX · rejection",tone:"cyan"},{id:"volatility_breakout",icon:"⚡",name:"Volatility Breakout",desc:"Donchian · ATR expansion · DMI · momentum",tone:"orange"}];'
      const newStrategies = 'const AUTO_STRATEGIES=[{id:"smart_confluence",icon:"AI",name:"Smart Confluence",desc:"5 стратегий · выбирает сильнейший подтверждённый сетап",tone:"violet"},{id:"trend_pulse",icon:"↗",name:"Trend Pulse",desc:"EMA 9/21/50/200 · ADX/DMI · MACD · RSI",tone:"violet"},{id:"range_reversal",icon:"↔",name:"Range Reversal",desc:"Bollinger · RSI · слабый ADX · rejection",tone:"cyan"},{id:"volatility_breakout",icon:"⚡",name:"Volatility Breakout",desc:"Donchian · ATR expansion · DMI · momentum",tone:"orange"}];'
      next = next.replace(oldStrategies, newStrategies)

      // Keep the active-session early-search indicator synchronized with the
      // real server switch instead of displaying a hard-coded ON state.
      if (!next.includes('import "./preload_ui_sync.js";')) {
        next = 'import "./preload_ui_sync.js";\n' + next
      }

      return next === code ? null : { code: next, map: null }
    },
  }
}

export default defineConfig({ plugins: [alphaPulseAutoUiPatch(), react()] })
