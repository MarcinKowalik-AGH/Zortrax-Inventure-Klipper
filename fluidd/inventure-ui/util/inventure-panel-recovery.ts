// Marcin Kowalik <mkowalik@agh.edu.pl>
type Link = 'socket' | 'klippy'

export class InventurePanelRecovery {
  private lost = new Map<Link, string>()
  private restartUrl: string | null = null
  private requested = false
  private reload: () => void

  constructor (reload = () => window.location.reload()) {
    this.reload = reload
  }

  observe (link: Link, url: string, ready: boolean, previouslyReady: boolean) {
    if (this.restartUrl !== url) this.restartUrl = null
    if (!ready) {
      if (url && previouslyReady) this.lost.set(link, url)
      return
    }
    const lostUrl = this.lost.get(link)
    this.lost.delete(link)
    if (!url || url !== lostUrl) return

    this.requestReload(url, false)
  }

  klippyNotification (url: string, ready: boolean) {
    if (!ready) {
      this.restartUrl = url || null
      return
    }
    const confirmed = !!url && this.restartUrl === url
    this.restartUrl = null
    if (confirmed) this.requestReload(url, true)
  }

  private requestReload (url: string, confirmedRestart: boolean) {
    if (this.requested) return
    try {
      const key = 'inventure-panel-reload:' + url
      const now = Date.now()
      const previous = Number(window.sessionStorage.getItem(key))
      if (!confirmedRestart && now - previous < 120_000 && previous <= now) return
      window.sessionStorage.setItem(key, String(now))
    } catch {
      // A real drop was observed. The in-document flag still prevents duplicate reloads.
    }
    this.requested = true
    this.reload()
  }
}

export const inventurePanelRecovery = new InventurePanelRecovery()
