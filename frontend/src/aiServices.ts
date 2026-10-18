import { useEffect, useState } from 'react'
import { api, type AIServices } from './api'

const STORAGE_KEY = 'shortcut.ai-service'

/** The AI model last chosen anywhere in the app ("service:model"), or null for the default. */
export function rememberedService(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY) || null
  } catch {
    return null
  }
}

export function remember(service: string | null) {
  try {
    if (service) localStorage.setItem(STORAGE_KEY, service)
    else localStorage.removeItem(STORAGE_KEY)
  } catch { /* storage unavailable: just not remembered */ }
}

/** The AI services with their availability; loaded once per page. */
export function useAIServices(): AIServices | null {
  const [services, setServices] = useState<AIServices | null>(null)
  useEffect(() => {
    api.aiServices().then(setServices, () => setServices({ services: [], default: null }))
  }, [])
  return services
}
