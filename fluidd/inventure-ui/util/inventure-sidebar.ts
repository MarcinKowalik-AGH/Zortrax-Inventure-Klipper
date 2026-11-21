// Marcin Kowalik <mkowalik@agh.edu.pl>
import { isNavigationFailure, type default as VueRouter } from 'vue-router'
import { consola } from 'consola'

let latestSelection = 0

// A long-running embedded panel can fill the browser's resource timing buffer.
// Observe the current click independently, then stop listening when it settles.
function observeViewAssets () {
  const started = performance.now()
  const urls = new Set<string>()
  const assets = new URL('./assets/', document.baseURI)
  function collect (entries: PerformanceEntry[]) {
    for (const entry of entries as PerformanceResourceTiming[]) {
      if (entry.startTime < started || !entry.name.startsWith(assets.href)) continue
      const url = new URL(entry.name)
      if (!/\.(?:js|css)$/.test(url.pathname)) continue
      if (entry.responseStatus >= 200 && entry.responseStatus < 400) continue
      urls.add(url.href)
    }
  }
  let observer: PerformanceObserver | undefined
  if (typeof PerformanceObserver !== 'undefined') {
    observer = new PerformanceObserver(list => collect(list.getEntries()))
    observer.observe({ entryTypes: ['resource'] })
  }
  function stop () {
    if (observer) {
      collect(observer.takeRecords())
      observer.disconnect()
      observer = undefined
    }
  }
  function failed () {
    stop()
    // Some engines deliver the timing entry after the import rejects.
    collect(performance.getEntriesByType?.('resource') ?? [])
    return urls
  }
  return { stop, failed }
}

async function refetchViewAssets (urls: Set<string>) {
  if (!urls.size) return
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 2000)
  try {
    await Promise.allSettled(Array.from(urls, async url => {
      const response = await fetch(url, { cache: 'reload', signal: controller.signal })
      if (response.ok) await response.arrayBuffer()
    }))
  } finally {
    window.clearTimeout(timeout)
  }
}

function reloadAtRoute (path: string) {
  // Fluidd uses hash routing. Keep the selected printer and deployment path.
  const url = new URL(window.location.href)
  url.hash = path
  window.history.pushState(null, '', url)
  window.location.reload()
}

export async function navigateInventureSidebar (router: VueRouter, name: string, reload = reloadAtRoute) {
  const selection = ++latestSelection
  const from = router.currentRoute
  const target = { name }
  const observation = observeViewAssets()
  try {
    await router.push(target)
  } catch (error) {
    if (isNavigationFailure(error) || selection !== latestSelection || router.currentRoute !== from) return
    const message = error instanceof Error ? error.message : String(error)
    if (/dynamically imported module|importing a module script failed|unable to preload css|loading (?:css )?chunk|error loading imported module/i.test(message)) {
      // WebKit can reuse a failed module even across location.reload().
      await refetchViewAssets(observation.failed())
      if (selection === latestSelection && router.currentRoute === from) reload(router.resolve(target).route.fullPath)
    } else {
      consola.error('[Inventure] Sidebar navigation failed', error)
    }
  } finally {
    observation.stop()
  }
}
