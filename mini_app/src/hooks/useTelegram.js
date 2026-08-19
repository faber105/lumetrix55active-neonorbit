import { useEffect, useMemo } from 'react';
function callTelegram(method) { try { method?.(); } catch { } }
export function useTelegram() {
  const webApp = useMemo(() => window.Telegram?.WebApp || null, []);
  useEffect(() => { if (!webApp) return; callTelegram(() => webApp.ready()); callTelegram(() => webApp.expand()); callTelegram(() => webApp.requestFullscreen?.()); callTelegram(() => webApp.disableVerticalSwipes?.()); callTelegram(() => webApp.enableClosingConfirmation?.()); callTelegram(() => webApp.setHeaderColor('#0D0D0D')); callTelegram(() => webApp.setBackgroundColor('#0D0D0D')); }, [webApp]);
  return { webApp, user: webApp?.initDataUnsafe?.user || null, haptic: { success: () => webApp?.HapticFeedback?.notificationOccurred('success'), warning: () => webApp?.HapticFeedback?.notificationOccurred('warning'), selection: () => webApp?.HapticFeedback?.selectionChanged() } };
}
