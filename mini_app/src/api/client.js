import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';
const TOKEN_KEY = 'alphapulse_access_token';

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15000
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

export function getTelegramInitData() {
  return window.Telegram?.WebApp?.initData || '';
}

export async function ensureAuth() {
  const existing = localStorage.getItem(TOKEN_KEY);
  if (existing) return existing;

  const initData = getTelegramInitData();
  if (!initData) {
    throw new Error('Открой Mini App внутри Telegram, чтобы пройти авторизацию.');
  }
  const { data } = await api.post('/auth/telegram', { initData });
  localStorage.setItem(TOKEN_KEY, data.access_token);
  return data.access_token;
}

export function resetAuth() {
  localStorage.removeItem(TOKEN_KEY);
}

